#!/usr/bin/env python3
"""Validate IPA and route eSpeak replacements into a separate source list."""

import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Optional, TextIO

from clean_rhyme_words import POLICY_VERSION, write_json_line
from generate_espeak_ipa import call_espeak, resolve_espeak
from generate_rhyme_db import (
    LANGUAGES, PROSODY_MARKERS, STRESS_MARKERS, parse_wordlist_line,
    token_is_vowel, tokenize_ipa,
)

MAX_OPTIONAL_VARIANTS = 8
WRAPPERS = {"/": "/", "[": "]"}
FRAGMENT_DASHES = frozenset("-‐‑‒–—―−\u00ad")
DEFAULT_EXTREME_DISTANCE = 0.8
MIN_EXTREME_EDITS = 4
MIN_EXTREME_PHONEMES = 5


def wrapper(value: str) -> Optional[tuple[str, str, str]]:
    if len(value) >= 2 and value[0] in WRAPPERS and value[-1] == WRAPPERS[value[0]]:
        return value[0], value[-1], value[1:-1]
    return None


def split_alternatives(value: str) -> tuple[list[str], list[str]]:
    """Split only visibly unambiguous tilde or wrapped-comma alternatives."""
    transformations: list[str] = []
    if "~" in value:
        wrapped = wrapper(value)
        if wrapped and not any(character in wrapped[2] for character in "/[]"):
            opening, closing, body = wrapped
            parts = [part.strip() for part in body.split("~")]
            if any(not part for part in parts):
                raise ValueError("ambiguous_tilde_notation")
            values = [f"{opening}{part}{closing}" for part in parts]
        else:
            parts = [part.strip() for part in value.split("~")]
            if any(not part or wrapper(part) is None for part in parts):
                raise ValueError("ambiguous_tilde_notation")
            values = parts
        transformations.append("split_tilde_alternatives")
        return values, transformations

    if "," in value:
        # A comma inside one transcription may separate syllables, annotations,
        # or alternatives. Only a sequence of independently wrapped values is
        # safe to split automatically.
        parts = [part.strip() for part in value.split(",")]
        if any(not part or wrapper(part) is None for part in parts):
            raise ValueError("ambiguous_comma_notation")
        transformations.append("split_wrapped_comma_alternatives")
        return parts, transformations

    return [value], transformations


def expand_optional_groups(value: str) -> tuple[list[str], list[str]]:
    if "(" not in value and ")" not in value:
        return [value], []
    if value.count("(") != value.count(")"):
        raise ValueError("invalid_optional_group")
    if any(character in re.sub(r"\([^()]+\)", "", value) for character in "()"):
        raise ValueError("invalid_optional_group")

    variants = [value]
    while any("(" in variant or ")" in variant for variant in variants):
        expanded: list[str] = []
        for variant in variants:
            match = re.search(r"\(([^()]*)\)", variant)
            if match is None or not match.group(1):
                raise ValueError("invalid_optional_group")
            before, optional, after = (
                variant[: match.start()],
                match.group(1),
                variant[match.end() :],
            )
            expanded.extend((before + after, before + optional + after))
        variants = list(dict.fromkeys(expanded))
        if len(variants) > MAX_OPTIONAL_VARIANTS:
            raise ValueError("too_many_optional_variants")
    return variants, ["expand_optional_groups"]


def validate_delimiters(value: str) -> bool:
    wrapped = wrapper(value)
    body = wrapped[2] if wrapped else value
    if wrapped is None and any(character in "/[]" for character in value):
        return False
    return not any(character in "/[]" for character in body)


def is_syllabic_nucleus(token: str, lang_code: str) -> bool:
    return token_is_vowel(token, lang_code) or any(
        mark in token for mark in ("\u0329", "\u030d")
    )


def clean_pronunciation(
    ipa: str, lang_code: str
) -> tuple[list[str], list[str], list[dict[str, object]]]:
    """Return valid normalized variants, transformation names, and rejects."""
    if any(character in ipa for character in "\t\r\n"):
        return [], [], [{"reason": "embedded_control"}]
    if "…" in ipa or "..." in ipa:
        return [], [], [{"reason": "incomplete_pronunciation"}]

    try:
        alternatives, transformations = split_alternatives(ipa.strip())
    except ValueError as error:
        return [], [], [{"reason": str(error)}]

    candidates: list[str] = []
    optional_variants = 0
    try:
        for alternative in alternatives:
            expanded, optional_transformations = expand_optional_groups(alternative)
            candidates.extend(expanded)
            transformations.extend(optional_transformations)
            if optional_transformations:
                optional_variants += len(expanded)
            if optional_variants > MAX_OPTIONAL_VARIANTS:
                raise ValueError("too_many_optional_variants")
    except ValueError as error:
        return [], list(dict.fromkeys(transformations)), [{"reason": str(error)}]

    valid: list[str] = []
    rejects: list[dict[str, object]] = []
    if "'" in ipa:
        transformations.append("normalize_ascii_stress")
    if "·" in ipa:
        transformations.append("normalize_middle_dot")

    for candidate in candidates:
        candidate = unicodedata.normalize(
            "NFC", candidate.replace("'", "ˈ").replace("·", ".")
        )
        reason = None
        details: dict[str, object] = {"candidate": candidate}
        if not candidate.strip():
            reason = "empty_pronunciation"
        elif not validate_delimiters(candidate):
            reason = "invalid_delimiters"
        elif any(character in FRAGMENT_DASHES for character in candidate):
            reason = "incomplete_pronunciation"
            details["characters"] = sorted(set(candidate) & FRAGMENT_DASHES)
        elif re.search(r"[A-Z]", candidate):
            reason = "mixed_uppercase_notation"
        elif any(character in candidate for character in "αε"):
            reason = "non_ipa_orthographic_symbol"
        elif "ı" in candidate:
            reason = "orthographic_dotless_i"
        else:
            unknown: Counter[str] = Counter()
            tokens = tokenize_ipa(candidate, lang_code, unknown)
            if unknown:
                reason = "unrecognized_tokens"
                details["tokens"] = dict(sorted(unknown.items()))
            elif not any(
                token not in STRESS_MARKERS | PROSODY_MARKERS for token in tokens
            ):
                reason = "empty_pronunciation"
            else:
                for index, token in enumerate(tokens):
                    if token not in STRESS_MARKERS:
                        continue
                    following = tokens[index + 1:]
                    next_stress = next(
                        (offset for offset, value in enumerate(following)
                         if value in STRESS_MARKERS), len(following)
                    )
                    if not any(
                        is_syllabic_nucleus(value, lang_code)
                        for value in following[:next_stress]
                    ):
                        reason = "misplaced_stress_mark"
                        break
        if reason:
            rejects.append({"reason": reason, "details": details})
        else:
            valid.append(candidate)

    return list(dict.fromkeys(valid)), list(dict.fromkeys(transformations)), rejects


def phoneme_tokens(ipa: str, lang_code: str) -> list[str]:
    """Get only complete phoneme tokens; never lose an unknown symbol."""
    unknown: Counter[str] = Counter()
    tokens = tokenize_ipa(ipa, lang_code, unknown)
    if unknown:
        raise ValueError(f"unrecognized IPA tokens: {dict(sorted(unknown.items()))}")
    return [token for token in tokens if token not in STRESS_MARKERS | PROSODY_MARKERS]


def phoneme_edit_distance(left: list[str], right: list[str]) -> int:
    """Count complete-phoneme insertions, deletions, and substitutions."""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_token in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_token in enumerate(right, start=1):
            current.append(min(
                previous[right_index] + 1,
                current[right_index - 1] + 1,
                previous[right_index - 1] + (left_token != right_token),
            ))
        previous = current
    return previous[-1]


def stressed_rhyme_tail(ipa: str, lang_code: str) -> list[str]:
    """Return phonemes from the last stressed vowel through the word's end."""
    unknown: Counter[str] = Counter()
    tokens = tokenize_ipa(ipa, lang_code, unknown)
    if unknown:
        raise ValueError(f"unrecognized IPA tokens: {dict(sorted(unknown.items()))}")
    stress_positions = [index for index, token in enumerate(tokens) if token in STRESS_MARKERS]
    if not stress_positions:
        return []
    for token_index in range(stress_positions[-1] + 1, len(tokens)):
        if is_syllabic_nucleus(tokens[token_index], lang_code):
            return [token for token in tokens[token_index:] if token not in PROSODY_MARKERS]
    return []


def compare_pronunciations(wiktionary_ipa: str, espeak_ipa: str,
                           lang_code: str) -> dict[str, object]:
    """Score phoneme divergence and expose the stressed rhyme tails separately."""
    wiktionary_tokens = phoneme_tokens(wiktionary_ipa, lang_code)
    espeak_tokens = phoneme_tokens(espeak_ipa, lang_code)
    edits = phoneme_edit_distance(wiktionary_tokens, espeak_tokens)
    denominator = max(len(wiktionary_tokens), len(espeak_tokens))
    wiktionary_tail = stressed_rhyme_tail(wiktionary_ipa, lang_code)
    espeak_tail = stressed_rhyme_tail(espeak_ipa, lang_code)
    return {
        "phoneme_edits": edits,
        "phoneme_distance_ratio": edits / denominator if denominator else 0.0,
        "wiktionary_phoneme_count": len(wiktionary_tokens),
        "espeak_phoneme_count": len(espeak_tokens),
        "wiktionary_rhyme_tail": wiktionary_tail,
        "espeak_rhyme_tail": espeak_tail,
        "rhyme_tail_matches": bool(wiktionary_tail and espeak_tail) and wiktionary_tail == espeak_tail,
    }


def is_extreme_mismatch(comparison: dict[str, object], threshold: float) -> bool:
    """Require both a large relative and absolute difference on long words."""
    return (
        comparison["phoneme_distance_ratio"] >= threshold
        and comparison["phoneme_edits"] >= MIN_EXTREME_EDITS
        and min(comparison["wiktionary_phoneme_count"],
                comparison["espeak_phoneme_count"]) >= MIN_EXTREME_PHONEMES
    )


class ProgressBar:
    def __init__(self, label: str, total: int, stream: TextIO = sys.stderr,
                 unit: str = "words"):
        self.label = label
        self.total = total
        self.stream = stream
        self.unit = unit
        self.last_width = 0

    def update(self, current: int) -> None:
        if not self.stream.isatty():
            return
        fraction = min(current / self.total, 1.0) if self.total else 1.0
        filled = round(30 * fraction)
        message = (
            f"\r{self.label} [{'#' * filled}{'-' * (30 - filled)}] "
            f"{fraction:6.2%} {current:,}/{self.total:,} {self.unit}"
        )
        self.stream.write(message.ljust(self.last_width))
        self.stream.flush()
        self.last_width = max(self.last_width, len(message))

    def finish(self) -> None:
        if self.stream.isatty():
            self.stream.write("\n")
            self.stream.flush()


def output_paths(wiki_output: Path, espeak_output: Path) -> dict[str, Path]:
    reports_dir = wiki_output.parent / "reports"
    stem = wiki_output.stem
    return {
        "wiktionary": wiki_output,
        "espeak": espeak_output,
        "rejected": reports_dir / f"{stem}_ipa_rejected.json",
        "changes": reports_dir / f"{stem}_ipa_changes.jsonl",
        "comparisons": reports_dir / f"{stem}_ipa_comparisons.jsonl",
        "report": reports_dir / f"{stem}_ipa_report.json",
    }


def create_staging_database(path: Path) -> sqlite3.Connection:
    staging = sqlite3.connect(path)
    try:
        staging.execute("PRAGMA journal_mode = OFF")
        staging.execute("PRAGMA synchronous = OFF")
        staging.executescript("""
            CREATE TABLE words (
                source TEXT NOT NULL, word TEXT NOT NULL, position INTEGER NOT NULL,
                PRIMARY KEY (source, word)
            ) WITHOUT ROWID;
            CREATE TABLE pronunciations (
                source TEXT NOT NULL, word TEXT NOT NULL, ipa TEXT NOT NULL,
                position INTEGER NOT NULL, PRIMARY KEY (source, word, ipa)
            ) WITHOUT ROWID;
            CREATE TABLE regeneration (
                word TEXT PRIMARY KEY, position INTEGER NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE comparison_requests (
                word TEXT PRIMARY KEY, position INTEGER NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE rejected (
                source TEXT NOT NULL, word TEXT NOT NULL, ipa TEXT NOT NULL,
                reason TEXT NOT NULL, details TEXT NOT NULL, position INTEGER NOT NULL
            );
        """)
    except BaseException:
        staging.close()
        raise
    return staging


def write_wordlist(path: Path, staging: sqlite3.Connection, source: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        current_word: Optional[str] = None
        ipas: list[str] = []
        rows = staging.execute(
            "SELECT words.word, pronunciations.ipa FROM words "
            "JOIN pronunciations USING (source, word) "
            "WHERE words.source = ? ORDER BY words.position, pronunciations.position",
            (source,),
        )
        for word, ipa in rows:
            if current_word is not None and word != current_word:
                output.write(
                    f"{current_word}\t{json.dumps(ipas, ensure_ascii=False, separators=(',', ':'))}\n"
                )
                ipas = []
            current_word = word
            ipas.append(ipa)
        if current_word is not None:
            output.write(
                f"{current_word}\t{json.dumps(ipas, ensure_ascii=False, separators=(',', ':'))}\n"
            )


def write_rejections(path: Path, staging: sqlite3.Connection) -> None:
    groups = []
    for (reason,) in staging.execute(
        "SELECT DISTINCT reason FROM rejected ORDER BY reason"
    ):
        entries = []
        for source, word, ipa, details in staging.execute(
            "SELECT source, word, ipa, details FROM rejected "
            "WHERE reason = ? ORDER BY position", (reason,)
        ):
            entries.append({
                "source": source, "word": word, "ipa": ipa,
                "reason": reason, "details": json.loads(details),
                "policy_version": POLICY_VERSION,
            })
        groups.append({"reason": reason, "ipas": [entry["ipa"] for entry in entries],
                       "entries": entries})
    with path.open("w", encoding="utf-8", newline="\n") as output:
        json.dump({"policy_version": POLICY_VERSION, "groups": groups}, output,
                  ensure_ascii=False, indent=2)
        output.write("\n")


def clean_ipa_wordlists(
    wiki_input: Path,
    espeak_input: Path,
    wiki_output: Path,
    espeak_output: Path,
    lang_code: str,
    executable: str,
    batch_size: int = 1000,
    replace_extreme_mismatches: bool = False,
    extreme_distance: float = DEFAULT_EXTREME_DISTANCE,
) -> dict[str, object]:
    """Validate both sources and add eSpeak replacements for faulty Wiktionary IPA."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if not 0.0 < extreme_distance <= 1.0:
        raise ValueError("extreme_distance must be greater than 0 and at most 1")
    for path in (wiki_input, espeak_input):
        if not path.is_file():
            raise FileNotFoundError(f"missing input wordlist: {path}")
    paths = output_paths(wiki_output, espeak_output)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    parts = {name: path.with_name(f"{path.name}.part") for name, path in paths.items()}
    staging_path = wiki_output.with_name(f"{wiki_output.name}.rows.part")
    for path in (*parts.values(), staging_path):
        path.unlink(missing_ok=True)

    staging: Optional[sqlite3.Connection] = None
    counts = Counter()
    rejection_reasons = Counter()
    transformation_reasons = Counter()
    output_positions = Counter()
    rejection_position = 0
    try:
        staging = create_staging_database(staging_path)

        def stage_ipa(source: str, word: str, ipa: str) -> None:
            output_positions[source] += 1
            position = output_positions[source]
            staging.execute("INSERT OR IGNORE INTO words VALUES (?, ?, ?)",
                            (source, word, position))
            staging.execute("INSERT OR IGNORE INTO pronunciations VALUES (?, ?, ?, ?)",
                            (source, word, ipa, position))

        def reject(source: str, word: str, ipa: str, reason: str,
                   details: dict[str, object], position: int) -> None:
            nonlocal rejection_position
            rejection_position += 1
            staging.execute(
                "INSERT INTO rejected VALUES (?, ?, ?, ?, ?, ?)",
                (source, word, ipa, reason,
                 json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 rejection_position),
            )
            rejection_reasons[reason] += 1
            counts["rejected_candidates"] += 1
            if source == "wiktionary":
                staging.execute("INSERT OR IGNORE INTO regeneration VALUES (?, ?)",
                                (word, position))

        with (
            parts["changes"].open("w", encoding="utf-8", newline="\n") as changes,
            parts["comparisons"].open("w", encoding="utf-8", newline="\n") as comparisons,
        ):
            input_bytes = wiki_input.stat().st_size + espeak_input.stat().st_size
            scan_progress = ProgressBar("Validating IPA", input_bytes, unit="bytes")
            scanned_bytes = 0
            try:
                for source_name, input_path in (("wiktionary", wiki_input), ("espeak", espeak_input)):
                    with input_path.open("r", encoding="utf-8") as source:
                        for line_number, line in enumerate(source, start=1):
                            scanned_bytes += len(line.encode("utf-8"))
                            word, ipas = parse_wordlist_line(input_path, line_number, line)
                            if source_name == "wiktionary":
                                staging.execute(
                                    "INSERT OR IGNORE INTO comparison_requests VALUES (?, ?)",
                                    (word, line_number),
                                )
                            counts[f"{source_name}_input_words"] += 1
                            counts[f"{source_name}_input_pronunciations"] += len(ipas)
                            for ipa in ipas:
                                normalized, transformations, rejected = clean_pronunciation(ipa, lang_code)
                                if transformations or (normalized and normalized != [ipa]):
                                    write_json_line(changes, {
                                        "source": source_name, "word": word,
                                        "original_ipa": ipa, "normalized_ipas": normalized,
                                        "transformations": transformations,
                                        "policy_version": POLICY_VERSION,
                                    })
                                    counts["transformed_pronunciations"] += 1
                                    transformation_reasons.update(transformations)
                                for item in rejected:
                                    reject(source_name, word, ipa, str(item["reason"]),
                                           item.get("details", {}), line_number)
                                for candidate in normalized:
                                    if source_name == "wiktionary" and not any(
                                        marker in candidate for marker in STRESS_MARKERS
                                    ):
                                        reject(source_name, word, ipa, "missing_stress_mark",
                                               {"candidate": candidate}, line_number)
                                    else:
                                        stage_ipa(source_name, word, candidate)
                            if line_number % 1_000 == 0:
                                scan_progress.update(scanned_bytes)
                    scan_progress.update(scanned_bytes)
            finally:
                scan_progress.finish()

            staging.commit()
            regeneration_count = staging.execute(
                "SELECT COUNT(*) FROM regeneration"
            ).fetchone()[0]
            counts["regeneration_requested_words"] = regeneration_count
            comparison_count = staging.execute(
                "SELECT COUNT(*) FROM comparison_requests"
            ).fetchone()[0]
            counts["comparison_requested_words"] = comparison_count
            progress = ProgressBar(f"Comparing {lang_code}", comparison_count)
            processed = 0
            batch: list[str] = []

            def compare_batch(words: list[str]) -> None:
                nonlocal processed
                generated = call_espeak(executable, lang_code, words)
                if len(generated) != len(words):
                    raise ValueError("eSpeak result count does not match input count")
                for word, raw_ipa in zip(words, generated):
                    normalized, transformations, rejected = clean_pronunciation(raw_ipa, lang_code)
                    needs_regeneration = staging.execute(
                        "SELECT 1 FROM regeneration WHERE word = ?", (word,)
                    ).fetchone() is not None
                    if needs_regeneration:
                        if normalized:
                            for ipa in normalized:
                                stage_ipa("espeak", word, ipa)
                            counts["regenerated_words"] += 1
                        else:
                            reject("espeak", word, raw_ipa, "espeak_generation_invalid",
                                   {"validation_reasons": [item["reason"] for item in rejected]}, processed)
                            counts["regeneration_failed_words"] += 1
                        reasons = [row[0] for row in staging.execute(
                            "SELECT DISTINCT reason FROM rejected WHERE source = 'wiktionary' "
                            "AND word = ? ORDER BY reason", (word,)
                        )]
                        write_json_line(changes, {
                            "source": "wiktionary", "word": word,
                            "action": "regenerated_with_espeak" if normalized else "regeneration_failed",
                            "original_ipas": [row[0] for row in staging.execute(
                                "SELECT DISTINCT ipa FROM rejected WHERE source = 'wiktionary' "
                                "AND word = ? ORDER BY position", (word,)
                            )],
                            "generated_ipas": normalized, "reasons": reasons,
                            "transformations": transformations,
                            "policy_version": POLICY_VERSION,
                        })

                    wiktionary_ipas = [row[0] for row in staging.execute(
                        "SELECT ipa FROM pronunciations WHERE source = 'wiktionary' "
                        "AND word = ? ORDER BY position", (word,)
                    )]
                    for wiktionary_ipa in wiktionary_ipas:
                        if not normalized:
                            counts["comparison_unavailable_variants"] += 1
                            continue
                        espeak_ipa, comparison = min(
                            ((candidate, compare_pronunciations(wiktionary_ipa, candidate, lang_code))
                             for candidate in normalized),
                            key=lambda item: (item[1]["phoneme_distance_ratio"],
                                              item[1]["phoneme_edits"]),
                        )
                        extreme = is_extreme_mismatch(comparison, extreme_distance)
                        replace = extreme and replace_extreme_mismatches
                        if replace:
                            staging.execute(
                                "DELETE FROM pronunciations WHERE source = 'wiktionary' "
                                "AND word = ? AND ipa = ?", (word, wiktionary_ipa),
                            )
                            stage_ipa("espeak", word, espeak_ipa)
                            counts["extreme_replaced_variants"] += 1
                            write_json_line(changes, {
                                "source": "wiktionary", "word": word,
                                "action": "extreme_mismatch_replaced_with_espeak",
                                "original_ipa": wiktionary_ipa,
                                "generated_ipa": espeak_ipa,
                                "phoneme_edits": comparison["phoneme_edits"],
                                "phoneme_distance_ratio": comparison["phoneme_distance_ratio"],
                                "policy_version": POLICY_VERSION,
                            })
                        elif extreme:
                            counts["extreme_review_variants"] += 1
                        if extreme or comparison["phoneme_distance_ratio"] > 0.5:
                            write_json_line(comparisons, {
                                "word": word, "wiktionary_ipa": wiktionary_ipa,
                                "espeak_ipa": espeak_ipa,
                                "action": "replaced_with_espeak" if replace else "kept_wiktionary",
                                "extreme_mismatch": extreme,
                                "policy_version": POLICY_VERSION,
                                **comparison,
                            })
                            counts["reported_comparison_variants"] += 1
                        counts["compared_variants"] += 1
                    if wiktionary_ipas and replace_extreme_mismatches:
                        staging.execute(
                            "DELETE FROM words WHERE source = 'wiktionary' "
                            "AND word = ? AND NOT EXISTS "
                            "(SELECT 1 FROM pronunciations WHERE source = 'wiktionary' AND word = ?)",
                            (word, word),
                        )
                    processed += 1
                    progress.update(processed)

            try:
                for (word,) in staging.execute(
                    "SELECT word FROM comparison_requests ORDER BY position"
                ):
                    batch.append(word)
                    if len(batch) == batch_size:
                        compare_batch(batch)
                        batch = []
                if batch:
                    compare_batch(batch)
            finally:
                progress.finish()

        staging.commit()
        for source_name in ("wiktionary", "espeak"):
            counts[f"{source_name}_output_words"] = staging.execute(
                "SELECT COUNT(*) FROM words WHERE source = ?", (source_name,)
            ).fetchone()[0]
            counts[f"{source_name}_output_pronunciations"] = staging.execute(
                "SELECT COUNT(*) FROM pronunciations WHERE source = ?", (source_name,)
            ).fetchone()[0]
            write_wordlist(parts[source_name], staging, source_name)
        write_rejections(parts["rejected"], staging)
        staging.close()
        staging = None
        staging_path.unlink()
        report = {
            "policy_version": POLICY_VERSION, "language": lang_code,
            "inputs": {"wiktionary": str(wiki_input), "espeak": str(espeak_input)},
            "outputs": {"wiktionary": str(wiki_output), "espeak": str(espeak_output)},
            "comparison_mode": "replace_extreme" if replace_extreme_mismatches else "report_only",
            "extreme_distance": extreme_distance,
            "minimum_extreme_edits": MIN_EXTREME_EDITS,
            "minimum_extreme_phonemes": MIN_EXTREME_PHONEMES,
            "counts": dict(sorted(counts.items())),
            "rejection_reasons": dict(sorted(rejection_reasons.items())),
            "transformation_reasons": dict(sorted(transformation_reasons.items())),
        }
        with parts["report"].open("w", encoding="utf-8", newline="\n") as output:
            json.dump(report, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
        for name, path in paths.items():
            os.replace(parts[name], path)
        return report
    except BaseException:
        if staging is not None:
            staging.close()
        for path in (*parts.values(), staging_path):
            path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate IPA and regenerate faulty Wiktionary variants.")
    parser.add_argument("wiktionary_input", type=Path)
    parser.add_argument("espeak_input", type=Path)
    parser.add_argument("wiktionary_output", type=Path)
    parser.add_argument("espeak_output", type=Path)
    parser.add_argument("--lang-code", choices=LANGUAGES, required=True)
    parser.add_argument("--espeak", default="espeak-ng")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--extreme-distance", type=float,
                        default=DEFAULT_EXTREME_DISTANCE,
                        help="review threshold for normalized phoneme distance (default: 0.8)")
    parser.add_argument("--replace-extreme-mismatches", action="store_true",
                        help="move extreme Wiktionary variants to eSpeak output; default is report only")
    args = parser.parse_args()
    try:
        executable = resolve_espeak(args.espeak)
        report = clean_ipa_wordlists(
            args.wiktionary_input, args.espeak_input, args.wiktionary_output,
            args.espeak_output, args.lang_code, executable, args.batch_size,
            args.replace_extreme_mismatches, args.extreme_distance,
        )
    except (FileNotFoundError, OSError, sqlite3.Error, UnicodeError, ValueError,
            RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    counts = report["counts"]
    print(f"IPA cleanup policy   : {POLICY_VERSION}")
    print(f"Regeneration requests: {counts.get('regeneration_requested_words', 0):,}")
    print(f"Regenerated words    : {counts.get('regenerated_words', 0):,}")
    print(f"Compared IPA variants: {counts.get('compared_variants', 0):,}")
    print(f"Extreme review flags : {counts.get('extreme_review_variants', 0):,}")
    print(f"Extreme replacements: {counts.get('extreme_replaced_variants', 0):,}")
    print(f"Wiktionary output    : {args.wiktionary_output}")
    print(f"eSpeak output        : {args.espeak_output}")


if __name__ == "__main__":
    main()

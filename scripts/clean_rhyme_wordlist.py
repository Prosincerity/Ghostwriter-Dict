#!/usr/bin/env python3
"""Create an audited, product-filtered wordlist for rhyme database builds."""

import argparse
import json
import os
import re
import sqlite3
import string
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Optional, TextIO

from generate_rhyme_db import LANGUAGES, parse_wordlist_line, tokenize_ipa


POLICY_VERSION = "rhyme-cleanup-v9"
MAX_OPTIONAL_VARIANTS = 8
WRAPPERS = {"/": "/", "[": "]"}
ASCII_HEADWORD_SYMBOLS = frozenset(string.punctuation)
EXTRA_HEADWORD_LETTERS = frozenset("ʻ")
TURKISH_PRODUCT_ALPHABET = frozenset(
    "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ"
    "abcçdefgğhıijklmnoöprsştuüvyz"
    "ÂâÎîÛûQqWwXx"
)
BLOCKED_LATIN_LETTERS = frozenset("ǀǁǂǃꝚꝛ")
HEADWORD_TRANSLATION = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "ʼ": "'",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "−": "-",
        "₀": "0",
        "₁": "1",
        "₂": "2",
        "₃": "3",
        "₄": "4",
        "₅": "5",
        "₆": "6",
        "₇": "7",
        "₈": "8",
        "₉": "9",
    }
)


class ProgressBar:
    def __init__(self, total_bytes: int, stream: TextIO = sys.stderr):
        self.total_bytes = total_bytes
        self.stream = stream
        self.last_width = 0

    def update(self, current_bytes: int, words: int, kept: int) -> None:
        if not self.stream.isatty():
            return
        fraction = (
            min(current_bytes / self.total_bytes, 1.0)
            if self.total_bytes
            else 1.0
        )
        filled = round(30 * fraction)
        bar = "#" * filled + "-" * (30 - filled)
        message = (
            f"\rCleaning [{bar}] {fraction:6.2%} "
            f"{current_bytes:,}/{self.total_bytes:,} bytes "
            f"words={words:,} kept={kept:,}"
        )
        self.stream.write(message.ljust(self.last_width))
        self.stream.flush()
        self.last_width = max(self.last_width, len(message))

    def finish(self) -> None:
        if self.stream.isatty():
            self.stream.write("\n")
            self.stream.flush()


def output_paths(output_path: Path) -> dict[str, Path]:
    stem = output_path.with_suffix("")
    reports_dir = output_path.parent / "reports"
    return {
        "wordlist": output_path,
        "rejected": reports_dir / f"{stem.name}_rejected.json",
        "rejected_words": reports_dir / f"{stem.name}_rejected_words.json",
        "changes": reports_dir / f"{stem.name}_changes.jsonl",
        "word_changes": reports_dir / f"{stem.name}_word_changes.jsonl",
        "report": reports_dir / f"{stem.name}_report.json",
    }


def legacy_rejection_paths(output_path: Path) -> tuple[Path, Path]:
    stem = output_path.with_suffix("")
    reports_dir = output_path.parent / "reports"
    return (
        reports_dir / f"{stem.name}_rejected.jsonl",
        reports_dir / f"{stem.name}_rejected_words.jsonl",
    )


def normalize_headword(word: str) -> tuple[str, list[str]]:
    transformations: list[str] = []
    normalized = word
    if "\N{SOFT HYPHEN}" in normalized:
        normalized = normalized.replace("\N{SOFT HYPHEN}", "")
        transformations.append("remove_soft_hyphen")
    translated = normalized.translate(HEADWORD_TRANSLATION)
    if translated != normalized:
        if any(character in word for character in "’‘ʼ"):
            transformations.append("normalize_apostrophe")
        if any(character in word for character in "‐‑‒–—−"):
            transformations.append("normalize_dash")
        if any(character in word for character in "₀₁₂₃₄₅₆₇₈₉"):
            transformations.append("normalize_subscript_digit")
    return unicodedata.normalize("NFC", translated), transformations


def is_product_alphanumeric(character: str, lang_code: str) -> bool:
    if character in "0123456789":
        return True
    if lang_code == "tr":
        return character in TURKISH_PRODUCT_ALPHABET
    return (
        character not in BLOCKED_LATIN_LETTERS
        and unicodedata.category(character).startswith("L")
        and unicodedata.name(character, "").startswith("LATIN")
    )


def headword_rejection(word: str, lang_code: str) -> Optional[dict[str, object]]:
    """Describe a normalized product-ineligible headword, or return ``None``."""
    if not word:
        return {
            "reason": "empty_headword_after_normalization",
            "details": {"invalid_characters": []},
        }
    invalid = Counter()
    for character in word:
        if (
            is_product_alphanumeric(character, lang_code)
            or character in ASCII_HEADWORD_SYMBOLS
            or character in EXTRA_HEADWORD_LETTERS
        ):
            continue
        invalid[character] += 1
    if not invalid:
        return None
    characters = [
        {
            "character": character,
            "codepoint": f"U+{ord(character):04X}",
            "count": count,
        }
        for character, count in sorted(invalid.items())
    ]
    return {
        "reason": "disallowed_headword_characters",
        "details": {"invalid_characters": characters},
    }


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
    try:
        for alternative in alternatives:
            expanded, optional_transformations = expand_optional_groups(alternative)
            candidates.extend(expanded)
            transformations.extend(optional_transformations)
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
            elif not tokens:
                reason = "empty_pronunciation"
        if reason:
            rejects.append({"reason": reason, "details": details})
        else:
            valid.append(candidate)

    return list(dict.fromkeys(valid)), list(dict.fromkeys(transformations)), rejects


def write_json_line(stream: TextIO, value: dict[str, object]) -> None:
    stream.write(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )
    stream.write("\n")


def write_rejection_groups(
    stream: TextIO,
    staging: sqlite3.Connection,
    table: str,
    value_column: str,
    list_name: str,
    include_entries: bool = False,
) -> None:
    """Write reason-grouped rejection values and optional audit entries."""
    stream.write("{\n")
    stream.write(
        f'  "policy_version": '
        f'{json.dumps(POLICY_VERSION, ensure_ascii=False)},\n'
    )
    stream.write('  "groups": [')
    reasons = staging.execute(
        f"SELECT reason FROM {table} GROUP BY reason ORDER BY reason"
    )
    for group_index, (reason,) in enumerate(reasons):
        stream.write("," if group_index else "")
        stream.write("\n    {\n")
        stream.write(
            f'      "reason": {json.dumps(reason, ensure_ascii=False)},\n'
        )
        stream.write(f'      "{list_name}": [')
        values = staging.execute(
            f"SELECT {value_column} FROM {table} "
            "WHERE reason = ? ORDER BY position",
            (reason,),
        )
        for value_index, (value,) in enumerate(values):
            stream.write("," if value_index else "")
            stream.write(f"\n        {json.dumps(value, ensure_ascii=False)}")
        stream.write("\n      ]")
        if include_entries:
            stream.write(',\n      "entries": [')
            entries = staging.execute(
                f"SELECT entry FROM {table} "
                "WHERE reason = ? ORDER BY position",
                (reason,),
            )
            for entry_index, (entry,) in enumerate(entries):
                stream.write("," if entry_index else "")
                stream.write(f"\n        {entry}")
            stream.write("\n      ]")
        stream.write("\n    }")
    stream.write("\n  ]\n}\n")


def create_staging_database(path: Path) -> sqlite3.Connection:
    staging = sqlite3.connect(path)
    staging.execute("PRAGMA journal_mode = OFF")
    staging.execute("PRAGMA synchronous = OFF")
    staging.executescript(
        """
        CREATE TABLE words (
            word TEXT PRIMARY KEY,
            position INTEGER NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE pronunciations (
            word TEXT NOT NULL,
            ipa TEXT NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (word, ipa)
        ) WITHOUT ROWID;
        CREATE TABLE rejected_words (
            reason TEXT NOT NULL,
            word TEXT NOT NULL,
            position INTEGER NOT NULL
        );
        CREATE TABLE rejected_ipas (
            reason TEXT NOT NULL,
            ipa TEXT NOT NULL,
            entry TEXT NOT NULL,
            position INTEGER NOT NULL
        );
        """
    )
    return staging


def write_eligible_wordlist(
    output_path: Path, staging: sqlite3.Connection
) -> None:
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        current_word: Optional[str] = None
        current_ipas: list[str] = []
        rows = staging.execute(
            "SELECT words.word, pronunciations.ipa "
            "FROM words JOIN pronunciations USING (word) "
            "ORDER BY words.position, pronunciations.position"
        )
        for staged_word, ipa in rows:
            if current_word is not None and staged_word != current_word:
                encoded = json.dumps(
                    current_ipas, ensure_ascii=False, separators=(",", ":")
                )
                output.write(f"{current_word}\t{encoded}\n")
                current_ipas = []
            current_word = staged_word
            current_ipas.append(ipa)
        if current_word is not None:
            encoded = json.dumps(
                current_ipas, ensure_ascii=False, separators=(",", ":")
            )
            output.write(f"{current_word}\t{encoded}\n")


def clean_wordlist(
    input_path: Path, output_path: Path, lang_code: str
) -> dict[str, object]:
    if not input_path.is_file():
        raise FileNotFoundError(f"missing input wordlist: {input_path}")
    paths = output_paths(output_path)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    part_paths = {
        name: path.with_name(f"{path.name}.part") for name, path in paths.items()
    }
    staging_path = output_path.with_name(f"{output_path.name}.rows.part")
    for path in part_paths.values():
        path.unlink(missing_ok=True)
    staging_path.unlink(missing_ok=True)

    counts = Counter()
    rejection_reasons: Counter[str] = Counter()
    word_rejection_reasons: Counter[str] = Counter()
    transformation_reasons: Counter[str] = Counter()
    word_transformation_reasons: Counter[str] = Counter()
    bytes_processed = 0
    progress = ProgressBar(input_path.stat().st_size)
    staging: Optional[sqlite3.Connection] = None

    try:
        staging = create_staging_database(staging_path)
        pronunciation_position = 0
        rejection_position = 0
        with (
            input_path.open("r", encoding="utf-8") as source,
            part_paths["changes"].open("w", encoding="utf-8", newline="\n") as changes,
            part_paths["word_changes"].open(
                "w", encoding="utf-8", newline="\n"
            ) as word_changes,
        ):
            for line_number, line in enumerate(source, start=1):
                bytes_processed += len(line.encode("utf-8"))
                word, ipas = parse_wordlist_line(input_path, line_number, line)
                counts["input_words"] += 1
                counts["input_pronunciations"] += len(ipas)
                eligible: list[str] = []

                normalized_word, word_transformations = normalize_headword(word)
                if word_transformations:
                    write_json_line(
                        word_changes,
                        {
                            "ipas": ipas,
                            "normalized_word": normalized_word,
                            "original_word": word,
                            "policy_version": POLICY_VERSION,
                            "transformations": word_transformations,
                        },
                    )
                    counts["transformed_words"] += 1
                    word_transformation_reasons.update(word_transformations)

                word_rejection = headword_rejection(normalized_word, lang_code)
                if word_rejection:
                    staging.execute(
                        "INSERT INTO rejected_words VALUES (?, ?, ?)",
                        (word_rejection["reason"], word, line_number),
                    )
                    counts["rejected_words"] += 1
                    word_rejection_reasons[str(word_rejection["reason"])] += 1
                    for ipa in ipas:
                        rejection_position += 1
                        entry = {
                            "details": word_rejection["details"],
                            "ipa": ipa,
                            "normalized_word": normalized_word,
                            "policy_version": POLICY_VERSION,
                            "reason": word_rejection["reason"],
                            "word": word,
                        }
                        staging.execute(
                            "INSERT INTO rejected_ipas VALUES (?, ?, ?, ?)",
                            (
                                word_rejection["reason"],
                                ipa,
                                json.dumps(
                                    entry,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                                rejection_position,
                            ),
                        )
                        rejection_reasons[str(word_rejection["reason"])] += 1
                        counts["rejected_candidates"] += 1
                else:
                    for ipa in ipas:
                        normalized, transformations, rejects = clean_pronunciation(
                            ipa, lang_code
                        )
                        eligible.extend(normalized)
                        if transformations or (normalized and normalized != [ipa]):
                            write_json_line(
                                changes,
                                {
                                    "normalized_ipas": normalized,
                                    "original_ipa": ipa,
                                    "policy_version": POLICY_VERSION,
                                    "transformations": transformations,
                                    "word": word,
                                },
                            )
                            counts["transformed_pronunciations"] += 1
                            transformation_reasons.update(transformations)
                        for reject in rejects:
                            row = {
                                "ipa": ipa,
                                "policy_version": POLICY_VERSION,
                                "reason": reject["reason"],
                                "word": word,
                            }
                            if "details" in reject:
                                row["details"] = reject["details"]
                            rejection_position += 1
                            staging.execute(
                                "INSERT INTO rejected_ipas VALUES (?, ?, ?, ?)",
                                (
                                    reject["reason"],
                                    ipa,
                                    json.dumps(
                                        row,
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    ),
                                    rejection_position,
                                ),
                            )
                            rejection_reasons[str(reject["reason"])] += 1
                            counts["rejected_candidates"] += 1

                eligible = list(dict.fromkeys(eligible))
                if eligible:
                    staging.execute(
                        "INSERT OR IGNORE INTO words VALUES (?, ?)",
                        (normalized_word, line_number),
                    )
                    for ipa in eligible:
                        pronunciation_position += 1
                        staging.execute(
                            "INSERT OR IGNORE INTO pronunciations VALUES (?, ?, ?)",
                            (normalized_word, ipa, pronunciation_position),
                        )
                    counts["eligible_input_words"] += 1
                else:
                    counts["omitted_words"] += 1
                if line_number % 1_000 == 0:
                    progress.update(
                        bytes_processed,
                        counts["input_words"],
                        counts["eligible_input_words"],
                    )
            progress.update(
                input_path.stat().st_size,
                counts["input_words"],
                counts["eligible_input_words"],
            )

        staging.commit()
        counts["eligible_words"] = staging.execute(
            "SELECT COUNT(*) FROM words"
        ).fetchone()[0]
        counts["eligible_pronunciations"] = staging.execute(
            "SELECT COUNT(*) FROM pronunciations"
        ).fetchone()[0]
        write_eligible_wordlist(part_paths["wordlist"], staging)
        with part_paths["rejected"].open(
            "w", encoding="utf-8", newline="\n"
        ) as rejected:
            write_rejection_groups(
                rejected,
                staging,
                "rejected_ipas",
                "ipa",
                "ipas",
                include_entries=True,
            )
        with part_paths["rejected_words"].open(
            "w", encoding="utf-8", newline="\n"
        ) as rejected_words:
            write_rejection_groups(
                rejected_words,
                staging,
                "rejected_words",
                "word",
                "words",
            )
        staging.close()
        staging = None
        staging_path.unlink()

        report: dict[str, object] = {
            "counts": dict(sorted(counts.items())),
            "input": str(input_path),
            "language": lang_code,
            "output": str(output_path),
            "policy_version": POLICY_VERSION,
            "rejection_reasons": dict(sorted(rejection_reasons.items())),
            "word_rejection_reasons": dict(
                sorted(word_rejection_reasons.items())
            ),
            "transformation_reasons": dict(sorted(transformation_reasons.items())),
            "word_transformation_reasons": dict(
                sorted(word_transformation_reasons.items())
            ),
        }
        with part_paths["report"].open(
            "w", encoding="utf-8", newline="\n"
        ) as report_file:
            json.dump(report, report_file, ensure_ascii=False, indent=2, sort_keys=True)
            report_file.write("\n")
        for name in (
            "wordlist", "rejected", "rejected_words", "changes",
            "word_changes", "report",
        ):
            os.replace(part_paths[name], paths[name])
        for legacy_path in legacy_rejection_paths(output_path):
            legacy_path.unlink(missing_ok=True)
        return report
    except BaseException:
        if staging is not None:
            staging.close()
        for path in part_paths.values():
            path.unlink(missing_ok=True)
        staging_path.unlink(missing_ok=True)
        raise
    finally:
        progress.finish()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an audited rhyme-eligible wordlist."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--lang-code", choices=LANGUAGES, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        report = clean_wordlist(args.input, args.output, args.lang_code)
    except (
        FileNotFoundError,
        OSError,
        sqlite3.Error,
        UnicodeError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    counts = report["counts"]
    print(f"Cleanup policy       : {POLICY_VERSION}")
    print(f"Input words          : {counts.get('input_words', 0):,}")
    print(f"Input pronunciations : {counts.get('input_pronunciations', 0):,}")
    print(f"Eligible input words : {counts.get('eligible_input_words', 0):,}")
    print(f"Unique output words  : {counts.get('eligible_words', 0):,}")
    print(f"Eligible IPA         : {counts.get('eligible_pronunciations', 0):,}")
    print(f"Omitted words        : {counts.get('omitted_words', 0):,}")
    print(f"Rejected candidates  : {counts.get('rejected_candidates', 0):,}")
    print(f"Output               : {args.output}")


if __name__ == "__main__":
    main()

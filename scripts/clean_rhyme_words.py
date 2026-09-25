#!/usr/bin/env python3
"""Filter and normalize written headwords without changing IPA values."""

import argparse
import json
import os
import sqlite3
import string
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Optional, TextIO

from generate_rhyme_db import LANGUAGES


POLICY_VERSION = "rhyme-cleanup-v13"
BASE_HEADWORD_LETTERS = string.ascii_letters
GERMAN_HEADWORD_LETTERS = "ÄÖÜẞäöüß"
TURKISH_HEADWORD_LETTERS = "ÂÇĞÎİÖŞÛÜâçğîıöşûü"
FRENCH_HEADWORD_LETTERS = "ÀÂÆÇÈÉÊËÎÏÔŒÙÛÜŸàâæçèéêëîïôœùûüÿ"
LOANWORD_HEADWORD_LETTERS = (
    "ÁÃÅÐÍÌÑÒÓÕØÚÝÞ"
    "áãåðíìñòóõøúýþ"
    "ĀĂĄĆĈČĎĐĒĖĘĢĤĪĮĶĹĻĽŁŃŅŇ"
    "ŐŔŖŘŚŜŠŢŤŪŬŰŲŴŶŹŻŽƏ"
    "āăąćĉčďđēėęģĥīįķĺļľłńņň"
    "őŕŗřśŝšţťūŭűųŵŷźżžə"
)
ACCEPTED_HEADWORD_LETTERS = frozenset(
    BASE_HEADWORD_LETTERS
    + GERMAN_HEADWORD_LETTERS
    + TURKISH_HEADWORD_LETTERS
    + FRENCH_HEADWORD_LETTERS
    + LOANWORD_HEADWORD_LETTERS
    + "ʻ"
)
ACCEPTED_HEADWORD_ALPHANUMERICS = ACCEPTED_HEADWORD_LETTERS | frozenset(
    string.digits
)
INTERNAL_HEADWORD_SEPARATORS = frozenset(".-&")
SPECIAL_HEADWORD_SYMBOLS = frozenset("/%+")
HEADWORD_APOSTROPHE = "'"
HEADWORD_TRANSLATION = str.maketrans(
    {
        "’": "'", "‘": "'", "ʼ": "'",
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
        "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
        "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
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
        fraction = min(current_bytes / self.total_bytes, 1.0) if self.total_bytes else 1.0
        filled = round(30 * fraction)
        bar = "#" * filled + "-" * (30 - filled)
        message = (
            f"\rCleaning words [{bar}] {fraction:6.2%} "
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
    reports_dir = output_path.parent / "reports"
    stem = output_path.stem
    return {
        "wordlist": output_path,
        "rejected_words": reports_dir / f"{stem}_rejected_words.json",
        "word_changes": reports_dir / f"{stem}_word_changes.jsonl",
        "report": reports_dir / f"{stem}_report.json",
    }


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
    del lang_code
    return character in ACCEPTED_HEADWORD_ALPHANUMERICS


def headword_rejection(word: str, lang_code: str) -> Optional[dict[str, object]]:
    if not word:
        return {
            "reason": "empty_headword_after_normalization",
            "details": {"invalid_characters": []},
        }
    invalid = Counter()
    for character in word:
        if (
            is_product_alphanumeric(character, lang_code)
            or character in INTERNAL_HEADWORD_SEPARATORS
            or character in SPECIAL_HEADWORD_SYMBOLS
            or character == HEADWORD_APOSTROPHE
        ):
            continue
        invalid[character] += 1
    if invalid:
        characters = [
            {"character": character, "codepoint": f"U+{ord(character):04X}", "count": count}
            for character, count in sorted(invalid.items())
        ]
        return {
            "reason": "disallowed_headword_characters",
            "details": {"invalid_characters": characters},
        }

    if len(word) == 1 and word in ACCEPTED_HEADWORD_LETTERS:
        return {"reason": "single_letter_headword", "details": {"character": word}}

    for position, character in enumerate(word):
        if character in INTERNAL_HEADWORD_SEPARATORS:
            if position == 0:
                return {"reason": "leading_special_character", "details": {"character": character, "position": position}}
            if position == len(word) - 1 and character in ".-":
                return {"reason": "trailing_dash_or_dot", "details": {"character": character, "position": position}}
            if (
                position == len(word) - 1
                or word[position - 1] not in ACCEPTED_HEADWORD_LETTERS
                or word[position + 1] not in ACCEPTED_HEADWORD_LETTERS
            ):
                return {"reason": "misplaced_headword_separator", "details": {"character": character, "position": position}}
        elif character == HEADWORD_APOSTROPHE:
            left_is_alphanumeric = position > 0 and word[position - 1] in ACCEPTED_HEADWORD_ALPHANUMERICS
            right_is_alphanumeric = position + 1 < len(word) and word[position + 1] in ACCEPTED_HEADWORD_ALPHANUMERICS
            if not (left_is_alphanumeric or right_is_alphanumeric):
                return {"reason": "misplaced_headword_apostrophe", "details": {"character": character, "position": position}}
        elif character == "/":
            if (
                position == 0
                or position == len(word) - 1
                or word[position - 1] not in ACCEPTED_HEADWORD_LETTERS
                or word[position + 1] not in ACCEPTED_HEADWORD_LETTERS
            ):
                return {"reason": "misplaced_headword_symbol", "details": {"character": character, "position": position}}
        elif character == "%":
            if position == 0 or position != len(word) - 1 or word[position - 1] not in string.digits:
                return {"reason": "misplaced_headword_symbol", "details": {"character": character, "position": position}}
        elif character == "+":
            plus_start = word.find("+")
            if (
                plus_start == 0
                or word[plus_start - 1] not in ACCEPTED_HEADWORD_LETTERS
                or any(symbol != "+" for symbol in word[plus_start:])
            ):
                return {"reason": "misplaced_headword_symbol", "details": {"character": character, "position": position}}
    return None


def write_json_line(stream: TextIO, value: dict[str, object]) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    stream.write("\n")


def parse_word_row(path: Path, line_number: int, line: str) -> tuple[str, list[str]]:
    """Check TSV shape while leaving every IPA string untouched."""
    row = line.rstrip("\r\n")
    word, separator, encoded_ipas = row.partition("\t")
    if not separator or not word or "\t" in encoded_ipas:
        raise ValueError(f"{path}:{line_number}: expected word<TAB>JSON-array")
    try:
        ipas = json.loads(encoded_ipas)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}:{line_number}: invalid IPA JSON: {error.msg}") from error
    if not isinstance(ipas, list) or not ipas or any(
        not isinstance(ipa, str) or not ipa.strip() for ipa in ipas
    ):
        raise ValueError(f"{path}:{line_number}: expected non-empty IPA string array")
    return unicodedata.normalize("NFC", word), ipas


def clean_wordlist(input_path: Path, output_path: Path, lang_code: str) -> dict[str, object]:
    if not input_path.is_file():
        raise FileNotFoundError(f"missing input wordlist: {input_path}")
    paths = output_paths(output_path)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    parts = {name: path.with_name(f"{path.name}.part") for name, path in paths.items()}
    staging_path = output_path.with_name(f"{output_path.name}.rows.part")
    for path in (*parts.values(), staging_path):
        path.unlink(missing_ok=True)

    counts = Counter()
    rejections = Counter()
    transformations = Counter()
    progress = ProgressBar(input_path.stat().st_size)
    staging: Optional[sqlite3.Connection] = None
    try:
        staging = sqlite3.connect(staging_path)
        staging.execute("PRAGMA journal_mode = OFF")
        staging.execute("PRAGMA synchronous = OFF")
        staging.executescript("""
            CREATE TABLE words (word TEXT PRIMARY KEY, position INTEGER NOT NULL) WITHOUT ROWID;
            CREATE TABLE ipas (word TEXT NOT NULL, ipa TEXT NOT NULL, position INTEGER NOT NULL,
                               PRIMARY KEY (word, ipa)) WITHOUT ROWID;
            CREATE TABLE rejects (reason TEXT NOT NULL, word TEXT NOT NULL,
                                  entry TEXT NOT NULL, position INTEGER NOT NULL);
        """)
        bytes_processed = 0
        ipa_position = 0
        with (
            input_path.open("r", encoding="utf-8") as source,
            parts["word_changes"].open("w", encoding="utf-8", newline="\n") as changes,
        ):
            for line_number, line in enumerate(source, start=1):
                bytes_processed += len(line.encode("utf-8"))
                word, ipas = parse_word_row(input_path, line_number, line)
                counts["input_words"] += 1
                counts["input_pronunciations"] += len(ipas)
                normalized, changed = normalize_headword(word)
                if changed:
                    write_json_line(changes, {
                        "original_word": word, "normalized_word": normalized,
                        "ipas": ipas, "transformations": changed,
                        "policy_version": POLICY_VERSION,
                    })
                    counts["transformed_words"] += 1
                    transformations.update(changed)
                rejection = headword_rejection(normalized, lang_code)
                if rejection:
                    entry = {
                        "word": word, "normalized_word": normalized,
                        "reason": rejection["reason"], "details": rejection["details"],
                        "policy_version": POLICY_VERSION,
                    }
                    staging.execute(
                        "INSERT INTO rejects VALUES (?, ?, ?, ?)",
                        (rejection["reason"], word,
                         json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                         line_number),
                    )
                    counts["rejected_words"] += 1
                    rejections[str(rejection["reason"])] += 1
                else:
                    staging.execute("INSERT OR IGNORE INTO words VALUES (?, ?)", (normalized, line_number))
                    for ipa in ipas:
                        ipa_position += 1
                        staging.execute("INSERT OR IGNORE INTO ipas VALUES (?, ?, ?)", (normalized, ipa, ipa_position))
                    counts["eligible_input_words"] += 1
                if line_number % 1_000 == 0:
                    progress.update(bytes_processed, counts["input_words"], counts["eligible_input_words"])
            progress.update(input_path.stat().st_size, counts["input_words"], counts["eligible_input_words"])
        staging.commit()

        counts["eligible_words"] = staging.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        counts["eligible_pronunciations"] = staging.execute("SELECT COUNT(*) FROM ipas").fetchone()[0]
        with parts["wordlist"].open("w", encoding="utf-8", newline="\n") as output:
            current_word = None
            current_ipas: list[str] = []
            for word, ipa in staging.execute(
                "SELECT words.word, ipas.ipa FROM words JOIN ipas USING (word) "
                "ORDER BY words.position, ipas.position"
            ):
                if current_word is not None and word != current_word:
                    output.write(f"{current_word}\t{json.dumps(current_ipas, ensure_ascii=False, separators=(',', ':'))}\n")
                    current_ipas = []
                current_word = word
                current_ipas.append(ipa)
            if current_word is not None:
                output.write(f"{current_word}\t{json.dumps(current_ipas, ensure_ascii=False, separators=(',', ':'))}\n")
        with parts["rejected_words"].open("w", encoding="utf-8", newline="\n") as output:
            groups = []
            for (reason,) in staging.execute("SELECT DISTINCT reason FROM rejects ORDER BY reason"):
                entries = [json.loads(row[0]) for row in staging.execute(
                    "SELECT entry FROM rejects WHERE reason = ? ORDER BY position", (reason,)
                )]
                groups.append({"reason": reason, "words": [entry["word"] for entry in entries], "entries": entries})
            json.dump({"policy_version": POLICY_VERSION, "groups": groups}, output, ensure_ascii=False, indent=2)
            output.write("\n")
        staging.close()
        staging = None
        staging_path.unlink()
        report = {
            "policy_version": POLICY_VERSION, "input": str(input_path),
            "output": str(output_path), "language": lang_code,
            "counts": dict(sorted(counts.items())),
            "word_rejection_reasons": dict(sorted(rejections.items())),
            "word_transformation_reasons": dict(sorted(transformations.items())),
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
    finally:
        progress.finish()


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean written headwords in an IPA wordlist.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--lang-code", choices=LANGUAGES, required=True)
    args = parser.parse_args()
    try:
        report = clean_wordlist(args.input, args.output, args.lang_code)
    except (FileNotFoundError, OSError, sqlite3.Error, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    counts = report["counts"]
    print(f"Word cleanup policy  : {POLICY_VERSION}")
    print(f"Input words          : {counts.get('input_words', 0):,}")
    print(f"Eligible words       : {counts.get('eligible_words', 0):,}")
    print(f"Output               : {args.output}")


if __name__ == "__main__":
    main()

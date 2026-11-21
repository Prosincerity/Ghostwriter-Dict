#!/usr/bin/env python3
"""Build a versioned SQLite rhyme index from one IPA wordlist.

Input rows are ``word<TAB>["ipa1","ipa2"]``. Each pronunciation becomes one
row in the database. The Kaikki release is encoded in the output filename so
per-language and per-source databases remain independently identifiable.

Identity rhymes caused by compounds are intentionally retained. A future
on-device post-match filter can reject a long matching tail when that tail is
itself a standalone dictionary word; that policy does not belong in this
lossless build stage.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional, TextIO

from ipa_tokens import LANGUAGES, NON_PHONEME_MARKERS, token_is_vowel, tokenize_ipa


SCHEMA = """
CREATE TABLE dictionary (
    word TEXT NOT NULL,
    ipa TEXT NOT NULL,
    ipa_reversed TEXT NOT NULL,
    assonance_reversed TEXT NOT NULL,
    PRIMARY KEY (word, ipa)
) WITHOUT ROWID;
"""

INDEXES = """
CREATE INDEX idx_ipa_reversed ON dictionary(ipa_reversed);
CREATE INDEX idx_assonance_reversed ON dictionary(assonance_reversed);
"""


class ProgressBar:
    def __init__(self, total_bytes: int, stream: TextIO = sys.stderr):
        self.total_bytes = total_bytes
        self.stream = stream
        self.last_width = 0

    def update(self, current_bytes: int, lines: int, rows: int) -> None:
        if not self.stream.isatty():
            return
        fraction = min(current_bytes / self.total_bytes, 1.0) if self.total_bytes else 1.0
        filled = round(30 * fraction)
        bar = "#" * filled + "-" * (30 - filled)
        message = (
            f"\rBuilding [{bar}] {fraction:6.2%} "
            f"{current_bytes:,}/{self.total_bytes:,} bytes "
            f"lines={lines:,} rows={rows:,}"
        )
        self.stream.write(message.ljust(self.last_width))
        self.stream.flush()
        self.last_width = max(self.last_width, len(message))

    def finish(self) -> None:
        if self.stream.isatty():
            self.stream.write("\n")
            self.stream.flush()


def derived_values(tokens: list[str], lang_code: str) -> tuple[str, str]:
    """Compute reversed full IPA and its reversed vowel sequence."""
    ipa_reversed = "".join(reversed(tokens))
    vowel_tokens = [token for token in tokens if token_is_vowel(token, lang_code)]
    return ipa_reversed, " ".join(reversed(vowel_tokens))


def parse_wordlist_line(path: Path, line_number: int, line: str) -> tuple[str, list[str]]:
    row = line.rstrip("\r\n")
    if not row:
        raise ValueError(f"{path}:{line_number}: empty row")
    word, separator, encoded_ipas = row.partition("\t")
    if not separator or not word or "\t" in encoded_ipas:
        raise ValueError(f"{path}:{line_number}: expected word<TAB>JSON-array")
    try:
        values = json.loads(encoded_ipas)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}:{line_number}: invalid IPA JSON: {error.msg}") from error
    if not isinstance(values, list):
        raise ValueError(f"{path}:{line_number}: IPA value must be a JSON array")
    if not values:
        raise ValueError(f"{path}:{line_number}: IPA array must not be empty")

    word = unicodedata.normalize("NFC", word)
    ipas: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"{path}:{line_number}: every IPA array item must be a non-empty string"
            )
        if any(unicodedata.category(char) == "Cc" for char in value):
            raise ValueError(f"{path}:{line_number}: IPA contains a control character")
        ipas.append(unicodedata.normalize("NFC", value.strip()))
    return word, ipas


def iter_rows(
    input_path: Path,
    lang_code: str,
    unknown: Counter[str],
    progress: ProgressBar,
) -> Iterable[tuple[str, str, str, str]]:
    lines = 0
    rows = 0
    bytes_processed = 0
    with input_path.open("r", encoding="utf-8") as source:
        for lines, line in enumerate(source, start=1):
            bytes_processed += len(line.encode("utf-8"))
            word, ipas = parse_wordlist_line(input_path, lines, line)
            for ipa in ipas:
                candidate_unknown: Counter[str] = Counter()
                tokens = tokenize_ipa(ipa, lang_code, candidate_unknown)
                if candidate_unknown:
                    unknown.update(candidate_unknown)
                    raise ValueError(
                        f"{input_path}:{lines}: unrecognized IPA tokens "
                        f"for {word!r}, {ipa!r}: {dict(sorted(candidate_unknown.items()))}"
                    )
                if not any(token not in NON_PHONEME_MARKERS for token in tokens):
                    raise ValueError(f"{input_path}:{lines}: no phoneme for {word!r}, {ipa!r}")
                ipa_reversed, assonance = derived_values(tokens, lang_code)
                rows += 1
                yield word, ipa, ipa_reversed, assonance
            if lines % 1_000 == 0:
                progress.update(bytes_processed, lines, rows)
        progress.update(input_path.stat().st_size, lines, rows)


def validate_release_version(release_version: str, output_path: Path) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", release_version):
        raise ValueError(
            "release version must contain only letters, digits, '.', '_', '+', or '-'"
        )
    if not (
        output_path.stem == release_version
        or output_path.stem.endswith(f"_{release_version}")
    ):
        raise ValueError(
            "output filename must include Kaikki release version "
            f"{release_version!r} as its final component"
        )


def build_database(
    input_path: Path, output_path: Path, lang_code: str, release_version: str
) -> tuple[int, int, Counter[str]]:
    if not input_path.is_file():
        raise FileNotFoundError(f"missing input wordlist: {input_path}")
    if output_path.suffix != ".db":
        raise ValueError("output path must end in .db")
    validate_release_version(release_version, output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = output_path.with_name(f"{output_path.name}.part")
    part_path.unlink(missing_ok=True)
    unknown: Counter[str] = Counter()
    row_count = 0
    progress = ProgressBar(input_path.stat().st_size)
    connection: Optional[sqlite3.Connection] = None

    try:
        connection = sqlite3.connect(part_path)
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.executescript(SCHEMA)
        insert = "INSERT INTO dictionary VALUES (?, ?, ?, ?)"
        for word, ipa, ipa_reversed, assonance in iter_rows(
            input_path, lang_code, unknown, progress
        ):
            try:
                connection.execute(insert, (word, ipa, ipa_reversed, assonance))
            except sqlite3.IntegrityError as error:
                raise ValueError(f"duplicate (word, ipa) pair: {word!r}, {ipa!r}") from error
            row_count += 1

        connection.executescript(INDEXES)
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise ValueError(f"database integrity check failed: {integrity}")
        unique_words = connection.execute(
            "SELECT COUNT(DISTINCT word) FROM dictionary"
        ).fetchone()[0]
        connection.close()
        connection = None
        os.replace(part_path, output_path)
    except BaseException:
        if connection is not None:
            connection.close()
        part_path.unlink(missing_ok=True)
        raise
    finally:
        progress.finish()

    return row_count, unique_words, unknown


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a versioned SQLite rhyme index.")
    parser.add_argument("input", type=Path, help="wordlist_<lang>_ipa.txt input")
    parser.add_argument("output", type=Path, help="versioned output .db path")
    parser.add_argument("--lang-code", choices=LANGUAGES, required=True)
    parser.add_argument(
        "--release-version",
        required=True,
        help="Kaikki release slug, which must appear in the output filename",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        rows, words, unknown = build_database(
            args.input, args.output, args.lang_code, args.release_version
        )
    except (FileNotFoundError, OSError, sqlite3.Error, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Kaikki release       : {args.release_version}")
    print(f"Total rows           : {rows:,}")
    print(f"Unique words         : {words:,}")
    print(f"Unrecognized symbols : {sum(unknown.values()):,}")
    print(f"Output               : {args.output}")


if __name__ == "__main__":
    main()

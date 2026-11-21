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
import re
import sqlite3
import subprocess
import sys
import unicodedata
from collections import Counter
from itertools import islice
from pathlib import Path
from typing import Iterable, Optional, TextIO

from build_reports import publish_files, write_json
from generate_espeak_ipa import call_espeak
from ipa_audit import Audit
from ipa_reversal import SOURCES, analyze_source, verified_key
from ipa_tokens import LANGUAGES, analysis_tokens, token_is_vowel
from progress import ProgressBar


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


class DatabaseProgress(ProgressBar):
    def __init__(self, total_bytes: int, stream: TextIO = sys.stderr):
        super().__init__("Building", total_bytes, stream, unit="bytes")

    def update(self, current_bytes: int, lines: int, rows: int) -> None:
        self.show(current_bytes, f"lines={lines:,} rows={rows:,}")


def assonance_key(ipa: str, lang: str, source: str) -> str:
    # This established index uses the cleanup policy's normalized vowel view.
    tokens = analysis_tokens(ipa, lang, espeak=source.startswith("espeak"))
    return " ".join(token for token in reversed(tokens) if token_is_vowel(token, lang))


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
        ipas.append(value)
    return word, ipas


def input_rows(path, lang, executable):
    """Fetch optional engine boundaries once per TSV word, shared by all variants."""
    with path.open(encoding="utf-8") as stream:
        lines = enumerate(stream, start=1)
        while batch := list(islice(lines, 1000)):
            rows = [(number, len(line.encode("utf-8")), *parse_wordlist_line(path, number, line))
                    for number, line in batch]
            references = (call_espeak(executable, lang, [row[2] for row in rows], boundaries=True)
                          if executable else [None] * len(rows))
            for row, reference in zip(rows, references, strict=True):
                yield (*row, reference)


def iter_rows(
    input_path: Path,
    lang_code: str,
    unknown: Counter[str],
    progress: DatabaseProgress,
    source_format: str = "wiktionary",
    audit: Audit | None = None,
    boundary_espeak: str | None = None,
) -> Iterable[tuple[str, str, str, str]]:
    lines = 0
    rows = 0
    bytes_processed = 0
    for lines, size, word, ipas, reference in input_rows(input_path, lang_code, boundary_espeak):
        bytes_processed += size
        for ipa in ipas:
            parsed, evidence = analyze_source(ipa, lang_code, source_format, reference)
            candidate_unknown = Counter(value for reason, value in parsed.issues if reason == "unknown")
            if candidate_unknown:
                unknown.update(candidate_unknown)
                raise ValueError(f"{input_path}:{lines}: unrecognized IPA tokens "
                                 f"for {word!r}, {ipa!r}: {dict(candidate_unknown)}")
            if not parsed.segments:
                raise ValueError(f"{input_path}:{lines}: no phoneme for {word!r}, {ipa!r}")
            try:
                ipa_reversed = verified_key(ipa, parsed)
            except ValueError as error:
                raise ValueError(f"{lang_code}/{source_format} {input_path}:{lines} {word!r}: {error}") from error
            assonance = assonance_key(ipa, lang_code, source_format)
            if audit is not None:
                audit.record(word, ipa, parsed, evidence, reference)
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
    input_path: Path, output_path: Path, lang_code: str, release_version: str,
    source_format: str = "wiktionary", report_path: Path | None = None,
    boundary_espeak: str | None = None,
) -> tuple[int, int, Counter[str]]:
    if not input_path.is_file():
        raise FileNotFoundError(f"missing input wordlist: {input_path}")
    if output_path.suffix != ".db":
        raise ValueError("output path must end in .db")
    validate_release_version(release_version, output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = output_path.with_name(f"{output_path.name}.part")
    unknown: Counter[str] = Counter()
    audit = Audit(lang_code, source_format, input_path, "database_input")
    if report_path is None:
        report_path = output_path.parent / "reports" / "ipa_reversal" / f"{output_path.stem}.json"
    report_part = report_path.with_name(report_path.name + ".part")
    destinations = [input_path, output_path, part_path, report_path, report_part, Path(f"{report_part}.part")]
    if len({path.resolve() for path in destinations}) != len(destinations):
        raise ValueError("input, database, report and staging paths must be distinct")
    if source_format not in SOURCES or lang_code not in LANGUAGES:
        raise ValueError("unsupported IPA source or language")
    part_path.unlink(missing_ok=True)
    if boundary_espeak:
        if source_format != "espeak":
            raise ValueError("engine boundary lookup requires the espeak source")
        audit.engine = subprocess.check_output([boundary_espeak, "--version"], text=True,
                                               stdin=subprocess.DEVNULL).strip()
    row_count = 0
    progress = DatabaseProgress(input_path.stat().st_size)
    connection: Optional[sqlite3.Connection] = None

    try:
        connection = sqlite3.connect(part_path)
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA user_version = 2")
        connection.executescript(SCHEMA)
        insert = "INSERT INTO dictionary VALUES (?, ?, ?, ?)"
        for word, ipa, ipa_reversed, assonance in iter_rows(
            input_path, lang_code, unknown, progress, source_format, audit, boundary_espeak
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
        write_json(report_part, audit.report())
        publish_files([(part_path, output_path), (report_part, report_path)])
    except BaseException:
        if connection is not None:
            connection.close()
        part_path.unlink(missing_ok=True)
        report_part.unlink(missing_ok=True)
        raise
    finally:
        progress.finish()

    return row_count, unique_words, unknown


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a versioned SQLite rhyme index.")
    parser.add_argument("input", type=Path, help="wordlist_<lang>_ipa.txt input")
    parser.add_argument("output", type=Path, help="versioned output .db path")
    parser.add_argument("--source", choices=SOURCES, default="wiktionary")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--boundary-espeak", help="recover matching eSpeak phoneme boundaries")
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
            args.input, args.output, args.lang_code, args.release_version, args.source, args.report, args.boundary_espeak
        )
    except (OSError, sqlite3.Error, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Kaikki release       : {args.release_version}")
    print(f"Total rows           : {rows:,}")
    print(f"Unique words         : {words:,}")
    print(f"Unrecognized symbols : {sum(unknown.values()):,}")
    print(f"Output               : {args.output}")


if __name__ == "__main__":
    main()

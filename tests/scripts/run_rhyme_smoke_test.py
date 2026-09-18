#!/usr/bin/env python3
"""Build deterministic sampled rhyme databases from local wordlists.

This is an opt-in integration/smoke test, not part of the lightweight unit
suite. Generated samples and databases live below ``out/<lang>/`` and are
ignored by Git.
"""

import argparse
import hashlib
import heapq
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import TextIO


PROJECT_DIR = Path(__file__).resolve().parents[2]
RHYME_DB_SCRIPT = PROJECT_DIR / "scripts" / "generate_rhyme_db.py"
LANGUAGES = ("en", "de", "tr")
SOURCES = ("wiktionary", "espeak")
CLEANUP_POLICY_VERSION = "rhyme-cleanup-v11"


class ProgressBar:
    def __init__(self, label: str, total_bytes: int, stream: TextIO = sys.stderr):
        self.label = label
        self.total_bytes = total_bytes
        self.stream = stream
        self.last_width = 0

    def update(self, current_bytes: int, words: int) -> None:
        if not self.stream.isatty():
            return
        fraction = (
            min(current_bytes / self.total_bytes, 1.0) if self.total_bytes else 1.0
        )
        filled = round(30 * fraction)
        bar = "#" * filled + "-" * (30 - filled)
        message = (
            f"\r{self.label} [{bar}] {fraction:6.2%} "
            f"{current_bytes:,}/{self.total_bytes:,} bytes words={words:,}"
        )
        self.stream.write(message.ljust(self.last_width))
        self.stream.flush()
        self.last_width = max(self.last_width, len(message))

    def finish(self) -> None:
        if self.stream.isatty():
            self.stream.write("\n")
            self.stream.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deterministic sampled SQLite rhyme databases."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_DIR / "out",
        help="directory containing source wordlists (default: repository out/)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_DIR / "out",
        help="generated artifact root (default: repository out/)",
    )
    parser.add_argument(
        "--lang-code",
        action="append",
        choices=LANGUAGES,
        dest="lang_codes",
        help="language to sample; repeat as needed (default: en, de, tr)",
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=SOURCES,
        dest="sources",
        help="pronunciation source; repeat as needed (default: wiktionary)",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=50_000,
        help="maximum sampled words per language/source (default: 50000)",
    )
    parser.add_argument(
        "--seed",
        default="ghostwriter-rhyme-smoke-v1",
        help="stable sampling seed",
    )
    parser.add_argument(
        "--release-version",
        required=True,
        help="traceable Kaikki release slug included in generated filenames",
    )
    args = parser.parse_args()
    if args.sample_size < 1:
        parser.error("--sample-size must be at least 1")
    return args


def source_name(lang_code: str, source: str) -> str:
    suffix = "_espeak_rhyme_eligible" if source == "espeak" else "_rhyme_eligible"
    return f"wordlist_{lang_code}{suffix}.txt"


def artifact_stem(
    lang_code: str, source: str, sample_size: int, release_version: str
) -> str:
    source_suffix = "_espeak" if source == "espeak" else ""
    return f"{lang_code}{source_suffix}_sample-{sample_size}_{release_version}"


def selection_score(seed: str, lang_code: str, source: str, line: str) -> int:
    digest = hashlib.blake2b(digest_size=16)
    digest.update(seed.encode("utf-8"))
    digest.update(b"\0")
    digest.update(lang_code.encode("ascii"))
    digest.update(b"\0")
    digest.update(source.encode("ascii"))
    digest.update(b"\0")
    digest.update(line.encode("utf-8"))
    return int.from_bytes(digest.digest(), "big")


def create_sample(
    input_path: Path,
    sample_path: Path,
    lang_code: str,
    source: str,
    sample_size: int,
    seed: str,
) -> tuple[int, int]:
    """Atomically write the exact N lowest seeded-hash wordlist rows."""
    if not input_path.is_file():
        raise FileNotFoundError(f"missing source wordlist: {input_path}")

    # The heap retains at most sample_size rows and makes a single streaming
    # pass. Negative scores turn heapq's min-heap into a bounded max-heap.
    selected: list[tuple[int, int, str]] = []
    input_words = 0
    bytes_processed = 0
    total_bytes = input_path.stat().st_size
    progress = ProgressBar(f"Sampling {lang_code}/{source}", total_bytes)

    try:
        with input_path.open("r", encoding="utf-8") as source_file:
            for line_number, line in enumerate(source_file, start=1):
                bytes_processed += len(line.encode("utf-8"))
                if not line.strip():
                    raise ValueError(f"{input_path}:{line_number}: empty row")
                input_words += 1
                score = selection_score(seed, lang_code, source, line)
                candidate = (-score, -line_number, line)
                if len(selected) < sample_size:
                    heapq.heappush(selected, candidate)
                elif candidate > selected[0]:
                    heapq.heapreplace(selected, candidate)
                if line_number % 10_000 == 0:
                    progress.update(bytes_processed, input_words)
        progress.update(total_bytes, input_words)
    finally:
        progress.finish()

    sample_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = sample_path.with_name(f"{sample_path.name}.part")
    part_path.unlink(missing_ok=True)
    try:
        with part_path.open("w", encoding="utf-8", newline="\n") as output:
            for _, _, line in sorted(selected, key=lambda item: -item[1]):
                output.write(line if line.endswith("\n") else f"{line}\n")
        os.replace(part_path, sample_path)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise

    return input_words, len(selected)


def validate_database(database_path: Path, expected_words: int) -> dict[str, object]:
    if not database_path.is_file():
        raise FileNotFoundError(f"missing database: {database_path}")
    database_uri = f"{database_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(database_uri, uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")

        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(dictionary)")
        ]
        expected_columns = [
            "word",
            "ipa",
            "ipa_reversed",
            "assonance_reversed",
        ]
        if columns != expected_columns:
            raise RuntimeError(f"unexpected dictionary columns: {columns}")

        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        expected_indexes = {
            "idx_ipa_reversed",
            "idx_assonance_reversed",
        }
        user_indexes = {name for name in indexes if not name.startswith("sqlite_")}
        if user_indexes != expected_indexes:
            raise RuntimeError(
                f"unexpected dictionary indexes: {sorted(user_indexes)}"
            )

        rows = connection.execute("SELECT COUNT(*) FROM dictionary").fetchone()[0]
        words = connection.execute(
            "SELECT COUNT(DISTINCT word) FROM dictionary"
        ).fetchone()[0]
        if words != expected_words:
            raise RuntimeError(
                f"database contains {words:,} words; expected {expected_words:,}"
            )
        if rows < words:
            raise RuntimeError(f"database contains fewer rows ({rows:,}) than words")

        return {
            "integrity": integrity,
            "rows": rows,
            "unique_words": words,
        }
    finally:
        connection.close()


def write_manifest(output_root: Path, manifest: dict[str, object]) -> Path:
    manifest_path = output_root / "reports" / "smoke_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = manifest_path.with_name(f"{manifest_path.name}.part")
    part_path.unlink(missing_ok=True)
    try:
        with part_path.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
        os.replace(part_path, manifest_path)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise
    return manifest_path


def main() -> None:
    args = parse_args()
    lang_codes = tuple(dict.fromkeys(args.lang_codes or LANGUAGES))
    sources = tuple(dict.fromkeys(args.sources or ("wiktionary",)))
    args.output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []

    try:
        for source in sources:
            for lang_code in lang_codes:
                input_path = args.input_dir / lang_code / source_name(lang_code, source)
                stem = artifact_stem(
                    lang_code, source, args.sample_size, args.release_version
                )
                language_outdir = args.output_root / lang_code
                sample_path = language_outdir / "samples" / f"wordlist_{stem}.txt"
                database_path = language_outdir / "databases" / f"{stem}.db"
                print(f"\nSampling {input_path}...", flush=True)
                input_words, sampled_words = create_sample(
                    input_path,
                    sample_path,
                    lang_code,
                    source,
                    args.sample_size,
                    args.seed,
                )
                print(
                    f"Selected {sampled_words:,} of {input_words:,} words -> {sample_path}",
                    flush=True,
                )
                subprocess.run(
                    [
                        sys.executable,
                        str(RHYME_DB_SCRIPT),
                        str(sample_path),
                        str(database_path),
                        "--lang-code",
                        lang_code,
                        "--release-version",
                        args.release_version,
                    ],
                    check=True,
                )
                validation = validate_database(database_path, sampled_words)
                print(
                    f"Validated {database_path}: {validation['rows']:,} rows, "
                    f"integrity={validation['integrity']}"
                )
                results.append(
                    {
                        "language": lang_code,
                        "source": source,
                        "input": str(input_path),
                        "input_words": input_words,
                        "sample": str(sample_path),
                        "sampled_words": sampled_words,
                        "database": str(database_path),
                        **validation,
                    }
                )
    except (
        FileNotFoundError,
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    manifest_paths = []
    for lang_code in lang_codes:
        manifest = {
            "cleanup_policy_version": CLEANUP_POLICY_VERSION,
            "language": lang_code,
            "release_version": args.release_version,
            "sample_size": args.sample_size,
            "seed": args.seed,
            "sampling": (
                "lowest 128-bit BLAKE2b hashes of seed, language, source, and row"
            ),
            "results": [
                result for result in results if result["language"] == lang_code
            ],
        }
        manifest_paths.append(write_manifest(args.output_root / lang_code, manifest))

    print("\nSmoke test passed. Manifests:")
    for manifest_path in manifest_paths:
        print(f"  {manifest_path}")


if __name__ == "__main__":
    main()

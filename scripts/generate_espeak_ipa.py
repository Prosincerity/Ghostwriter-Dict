#!/usr/bin/env python3
"""Generate IPA for no-IPA wordlists by calling eSpeak NG in batches.

For every selected language this reads ``<lang>/wordlist_<lang>_noipa.txt`` and
atomically overwrites ``<lang>/wordlist_<lang>_espeak_ipa.txt`` below the
output folder. Output rows use ``word<TAB>["ipa"]``.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import TextIO


DEFAULT_LANGUAGES = ("en", "de", "tr")


class ProgressBar:
    def __init__(self, label: str, total: int, stream: TextIO = sys.stderr):
        self.label = label
        self.total = total
        self.stream = stream
        self.last_width = 0

    def update(self, current: int) -> None:
        if not self.stream.isatty():
            return
        fraction = min(current / self.total, 1.0) if self.total else 1.0
        filled = round(30 * fraction)
        bar = "#" * filled + "-" * (30 - filled)
        text = (
            f"\r{self.label} [{bar}] {fraction:6.2%} "
            f"{current:,}/{self.total:,} words"
        )
        self.stream.write(text.ljust(self.last_width))
        self.stream.flush()
        self.last_width = max(self.last_width, len(text))

    def finish(self) -> None:
        if self.stream.isatty():
            self.stream.write("\n")
            self.stream.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate IPA for missing-IPA wordlists with eSpeak NG."
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "out",
        help="root containing <lang>/wordlist_<lang>_noipa.txt files",
    )
    parser.add_argument(
        "--lang-code",
        action="append",
        dest="lang_codes",
        help="language/voice code to process; repeat as needed (default: en, de, tr)",
    )
    parser.add_argument(
        "--espeak",
        default="espeak-ng",
        help="eSpeak NG executable name or path (default: espeak-ng)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10_000,
        help="words sent to each eSpeak NG process (default: 10000)",
    )
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    return args


def resolve_espeak(command: str) -> str:
    executable = shutil.which(command)
    if executable is None:
        raise SystemExit(
            f"eSpeak NG executable not found: {command!r}. "
            "Install espeak-ng or pass --espeak /path/to/espeak-ng."
        )
    return executable


def validate_input(path: Path) -> int:
    word_count = 0
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            word = line.rstrip("\r\n")
            if not word:
                raise ValueError(f"{path}:{line_number}: empty word")
            if "\t" in word:
                raise ValueError(f"{path}:{line_number}: word contains a tab")
            word_count += 1
    return word_count


def call_espeak(executable: str, lang_code: str, words: list[str]) -> list[str]:
    input_text = "".join(f"{word}\n" for word in words)
    result = subprocess.run(
        [executable, "-q", "--ipa", "-v", lang_code],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if result.returncode != 0:
        detail = f": {result.stderr.strip()}" if result.stderr.strip() else ""
        raise RuntimeError(
            f"eSpeak NG failed for voice {lang_code!r} "
            f"with exit code {result.returncode}{detail}"
        )

    pronunciations = result.stdout.splitlines()

    # eSpeak NG may emit a final empty separator line. It is not associated
    # with another input word, so remove only empty excess lines at the end.
    while len(pronunciations) > len(words) and not pronunciations[-1].strip():
        pronunciations.pop()

    if len(pronunciations) == len(words):
        return pronunciations

    # Some punctuation-heavy headwords and phrases produce more than one IPA
    # line. Bisect only the mismatched batch until those entries are isolated,
    # keeping the normal large-batch path fast for the rest of the wordlist.
    if len(words) > 1:
        midpoint = len(words) // 2
        return call_espeak(executable, lang_code, words[:midpoint]) + call_espeak(
            executable, lang_code, words[midpoint:]
        )

    # One input word may contain multiple clauses. Join its non-empty phoneme
    # fragments back into one pronunciation so it cannot shift later words.
    fragments = [line.strip() for line in pronunciations if line.strip()]
    return [" ".join(fragments)]


def generate_language(
    executable: str,
    input_path: Path,
    output_path: Path,
    lang_code: str,
    batch_size: int,
    total: int,
) -> tuple[int, int]:
    generated = 0
    empty = 0
    processed = 0
    part_path = output_path.with_name(f"{output_path.name}.part")
    progress = ProgressBar(f"Generating {lang_code}", total)

    try:
        with (
            input_path.open("r", encoding="utf-8") as source,
            part_path.open("w", encoding="utf-8", newline="\n") as output,
        ):
            batch: list[str] = []
            for line in source:
                batch.append(line.rstrip("\r\n"))
                if len(batch) < batch_size:
                    continue
                pronunciations = call_espeak(executable, lang_code, batch)
                generated_now, empty_now = write_batch(batch, pronunciations, output)
                generated += generated_now
                empty += empty_now
                processed += len(batch)
                progress.update(processed)
                batch.clear()

            if batch:
                pronunciations = call_espeak(executable, lang_code, batch)
                generated_now, empty_now = write_batch(batch, pronunciations, output)
                generated += generated_now
                empty += empty_now
                processed += len(batch)
                progress.update(processed)

        os.replace(part_path, output_path)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise
    finally:
        progress.finish()

    return generated, empty


def write_batch(
    words: list[str], pronunciations: list[str], output: TextIO
) -> tuple[int, int]:
    generated = 0
    empty = 0
    for word, raw_ipa in zip(words, pronunciations):
        word = unicodedata.normalize("NFC", word)
        ipa = unicodedata.normalize("NFC", raw_ipa.strip())
        if not ipa:
            empty += 1
            continue
        ipa_json = json.dumps([ipa], ensure_ascii=False, separators=(",", ":"))
        output.write(f"{word}\t{ipa_json}\n")
        generated += 1
    return generated, empty


def process_language(
    executable: str, outdir: Path, lang_code: str, batch_size: int
) -> None:
    language_outdir = outdir / lang_code
    input_path = language_outdir / f"wordlist_{lang_code}_noipa.txt"
    output_path = language_outdir / f"wordlist_{lang_code}_espeak_ipa.txt"
    if not input_path.is_file():
        raise FileNotFoundError(f"missing input wordlist: {input_path}")

    word_count = validate_input(input_path)
    print(f"Generating {lang_code} IPA for {word_count:,} words...")
    generated, empty = generate_language(
        executable,
        input_path,
        output_path,
        lang_code,
        batch_size,
        word_count,
    )
    print(f"  Generated : {generated:,}")
    print(f"  Empty IPA : {empty:,}")
    print(f"  Output    : {output_path}")


def main() -> None:
    args = parse_args()
    executable = resolve_espeak(args.espeak)
    args.outdir.mkdir(parents=True, exist_ok=True)
    lang_codes = tuple(dict.fromkeys(args.lang_codes or DEFAULT_LANGUAGES))

    try:
        for lang_code in lang_codes:
            process_language(executable, args.outdir, lang_code, args.batch_size)
    except (FileNotFoundError, OSError, RuntimeError, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()

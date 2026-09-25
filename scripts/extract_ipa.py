#!/usr/bin/env python3
"""Merge words and IPA for selected languages from Kaikki JSONL dumps.

The language named by a Kaikki dump is its Wiktionary edition, not a filter.
Every input is searched and each entry is routed using its exact ``lang_code``.

Output (UTF-8, tab-separated where applicable):
  <lang>/wordlist_<lang>_ipa.txt    word<TAB>["ipa1","ipa2"]
  <lang>/wordlist_<lang>_noipa.txt  word

Both uncompressed .jsonl and gzip-compressed .jsonl.gz inputs are supported.
"""

import argparse
import gzip
import json
import os
import sys
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Iterable, Mapping, TextIO


# Square brackets and slashes are valid IPA delimiters. Only values whose
# complete content is a broken/unexpanded placeholder are rejected.
JUNK_IPA = {
    "",
    "?",
    "-",
    "...",
    "…",
    "[]",
    "//",
    "[...]",
    "[…]",
    "/.../",
    "/…/",
    "[?]",
    "/?/",
}


@dataclass
class LanguageExtraction:
    words: OrderedDict[str, None] = field(default_factory=OrderedDict)
    word_ipas: OrderedDict[str, OrderedDict[str, None]] = field(
        default_factory=OrderedDict
    )
    matched: int = 0
    script_rejected: int = 0
    junk_ipa_dropped: int = 0


class ProgressBar:
    def __init__(self, total_bytes: int, stream: TextIO = sys.stderr):
        self.total_bytes = total_bytes
        self.stream = stream
        self.last_width = 0

    def update(
        self, current_bytes: int, records: int, words_by_language: dict[str, int]
    ) -> None:
        if not self.stream.isatty():
            return
        fraction = min(current_bytes / self.total_bytes, 1.0) if self.total_bytes else 1.0
        filled = round(30 * fraction)
        bar = "#" * filled + "-" * (30 - filled)
        words = " ".join(
            f"{lang_code}={count:,}" for lang_code, count in words_by_language.items()
        )
        text = (
            f"\rReading [{bar}] {fraction:6.2%} "
            f"{current_bytes:,}/{self.total_bytes:,} bytes "
            f"records={records:,} words({words})"
        )
        self.stream.write(text.ljust(self.last_width))
        self.stream.flush()
        self.last_width = max(self.last_width, len(text))

    def finish(self) -> None:
        if self.stream.isatty():
            self.stream.write("\n")
            self.stream.flush()


def is_usable_ipa(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return "".join(value.split()) not in JUNK_IPA


def iter_ipa_fields(sounds: object) -> Iterable[object]:
    """Yield both general and audio-specific IPA fields."""
    if not isinstance(sounds, list):
        return
    for sound in sounds:
        if not isinstance(sound, dict):
            continue
        if "ipa" in sound:
            yield sound["ipa"]
        if "audio-ipa" in sound:
            yield sound["audio-ipa"]


def contains_non_latin_letter(value: str) -> bool:
    """Detect letters from scripts unsuitable for selected Latin-script words."""
    for character in value:
        if not unicodedata.category(character).startswith("L"):
            continue
        name = unicodedata.name(character, "")
        if "LATIN" not in name and not name.startswith("MODIFIER LETTER"):
            return True
    return False


def normalize_word(word: str) -> str:
    """Lowercase ordinary headwords while retaining multi-capital spellings."""
    word = unicodedata.normalize("NFC", word)
    if sum(character.isupper() for character in word) >= 2:
        return word
    return unicodedata.normalize("NFC", word.lower())


def open_jsonl(path: Path) -> IO[str]:
    if path.name.endswith(".gz"):
        return gzip.open(path, mode="rt", encoding="utf-8")
    return path.open(mode="r", encoding="utf-8")


def input_position(source: IO[str], path: Path) -> int:
    """Return bytes consumed from the underlying compressed/plain file."""
    buffer = source.buffer  # type: ignore[attr-defined]
    if path.name.endswith(".gz"):
        return buffer.fileobj.tell()
    return buffer.tell()


def write_language_wordlists(
    language_outdir: Path,
    lang_code: str,
    words: Mapping[str, object],
    word_ipas: Mapping[str, Mapping[str, object]],
) -> tuple[Path, Path]:
    """Atomically write one language's IPA and no-IPA wordlists."""
    language_outdir.mkdir(parents=True, exist_ok=True)
    ipa_path = language_outdir / f"wordlist_{lang_code}_ipa.txt"
    noipa_path = language_outdir / f"wordlist_{lang_code}_noipa.txt"
    ipa_part = ipa_path.with_name(f"{ipa_path.name}.part")
    noipa_part = noipa_path.with_name(f"{noipa_path.name}.part")
    part_paths = (ipa_part, noipa_part)
    for part_path in part_paths:
        part_path.unlink(missing_ok=True)

    try:
        with ipa_part.open("w", encoding="utf-8", newline="\n") as output:
            for word, ipas in word_ipas.items():
                ipa_json = json.dumps(
                    list(ipas), ensure_ascii=False, separators=(",", ":")
                )
                output.write(f"{word}\t{ipa_json}\n")

        with noipa_part.open("w", encoding="utf-8", newline="\n") as output:
            for word in words:
                if word not in word_ipas:
                    output.write(f"{word}\n")

        os.replace(ipa_part, ipa_path)
        os.replace(noipa_part, noipa_path)
    except BaseException:
        for part_path in part_paths:
            part_path.unlink(missing_ok=True)
        raise

    return ipa_path, noipa_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge selected languages from one or more Kaikki dumps."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="one or more Kaikki .jsonl or .jsonl.gz files",
    )
    parser.add_argument(
        "--lang-code",
        action="append",
        dest="lang_codes",
        required=True,
        help="exact lang_code to keep; repeat for multiple languages",
    )
    parser.add_argument(
        "--outdir", type=Path, default=Path(__file__).resolve().parents[1] / "out"
    )
    parser.add_argument(
        "--latin-headwords-only",
        action="store_true",
        help="reject headwords containing non-Latin letters",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    # A dict also removes repeated --lang-code arguments while retaining order.
    lang_codes = tuple(dict.fromkeys(args.lang_codes))
    selected_languages = set(lang_codes)
    languages = {code: LanguageExtraction() for code in lang_codes}

    total_lines = 0
    bad_lines = 0
    invalid_entries = 0
    input_sizes = [path.stat().st_size for path in args.inputs]
    total_input_bytes = sum(input_sizes)
    completed_input_bytes = 0
    progress = ProgressBar(total_input_bytes)
    progress.update(0, 0, {code: 0 for code in lang_codes})

    for input_path, input_size in zip(args.inputs, input_sizes):
        with open_jsonl(input_path) as source:
            for line in source:
                if not line.strip():
                    continue
                total_lines += 1
                if total_lines % 25_000 == 0:
                    progress.update(
                        completed_input_bytes + input_position(source, input_path),
                        total_lines,
                        {code: len(languages[code].words) for code in lang_codes},
                    )
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    bad_lines += 1
                    continue

                if not isinstance(obj, dict):
                    invalid_entries += 1
                    continue

                lang_code = obj.get("lang_code")
                if not isinstance(lang_code, str) or lang_code not in selected_languages:
                    continue
                language = languages[lang_code]
                language.matched += 1

                word = obj.get("word")
                if (
                    not isinstance(word, str)
                    or not word
                    or any(character in word for character in "\t\r\n")
                ):
                    invalid_entries += 1
                    continue
                word = unicodedata.normalize("NFC", word)
                if args.latin_headwords_only and contains_non_latin_letter(word):
                    language.script_rejected += 1
                    continue
                word = normalize_word(word)

                language.words.setdefault(word, None)

                ipa_fields = list(iter_ipa_fields(obj.get("sounds")))
                usable_ipas = [
                    unicodedata.normalize("NFC", ipa.strip())
                    for ipa in ipa_fields
                    if is_usable_ipa(ipa) and isinstance(ipa, str)
                ]
                language.junk_ipa_dropped += len(ipa_fields) - len(usable_ipas)

                if usable_ipas:
                    bucket = language.word_ipas.setdefault(word, OrderedDict())
                    for ipa in usable_ipas:
                        bucket.setdefault(ipa, None)

        completed_input_bytes += input_size
        progress.update(
            completed_input_bytes,
            total_lines,
            {code: len(languages[code].words) for code in lang_codes},
        )

    progress.finish()

    print(f"JSONL records         : {total_lines}")
    print(f"Bad JSON records      : {bad_lines}")
    print(f"Other invalid records : {invalid_entries}")

    for lang_code in lang_codes:
        language = languages[lang_code]
        language_outdir = args.outdir / lang_code
        ipa_path, noipa_path = write_language_wordlists(
            language_outdir,
            lang_code,
            language.words,
            language.word_ipas,
        )

        with_ipa = len(language.word_ipas)
        without_ipa = len(language.words) - with_ipa
        print(f"\nLanguage {lang_code}")
        print(f"  Matching records          : {language.matched}")
        print(f"  Non-Latin words rejected  : {language.script_rejected}")
        print(f"  Junk IPA values dropped   : {language.junk_ipa_dropped}")
        print(f"  Unique words with IPA     : {with_ipa} -> {ipa_path}")
        print(f"  Unique words without IPA  : {without_ipa} -> {noipa_path}")


if __name__ == "__main__":
    main()

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
from typing import Iterable, TextIO


LANGUAGES = ("en", "de", "tr")
PRIMARY_STRESS = "ˈ"
STRESS_MARKERS = frozenset((PRIMARY_STRESS, "ˌ"))
IGNORED_SEPARATORS = frozenset(" ./[]()⟨⟩|-‿_")

# These inventories are an audited representation of phonemes occurring in
# the English, German, and Turkish Kaikki-derived wordlists, including common
# broad/narrow transcription variants. They are deliberately language-local:
# an unknown counter makes future source drift visible and provides the input
# for expanding the appropriate inventory after each release audit.
VOWELS = {
    "en": frozenset(
        (
            "a", "e", "i", "o", "u", "y", "æ", "ɑ", "ɒ", "ɔ", "ə", "ɛ", "ɚ",
            "ɘ", "ɜ", "ɝ", "ɞ", "ɤ", "ɨ", "ɪ", "ɯ", "ɵ", "ʉ", "ʊ", "ʌ",
            "ʏ", "ø", "œ", "ɐ", "ᵊ", "ᵻ", "ᵿ", "aɪ", "aʊ", "eɪ", "oʊ",
            "ɔɪ", "əʊ", "ɪə", "eə", "ʊə",
        )
    ),
    "de": frozenset(
        (
            "a", "e", "i", "o", "u", "y", "æ", "ɑ", "ɒ", "ɔ", "ə", "ɛ",
            "ɐ", "ɘ", "ɜ", "ɝ", "ɚ", "ɤ", "ɨ", "ɪ", "ɯ", "ɶ", "ɵ", "ʉ",
            "ʊ", "ʌ", "ʏ", "ø", "œ", "aɪ", "aʊ", "ɔʏ", "oʏ", "ʊɪ",
        )
    ),
    "tr": frozenset(
        (
            "a", "e", "i", "o", "u", "y", "ä", "æ", "ɑ", "ɒ", "ɔ", "ə",
            "ɛ", "ɐ", "ɜ", "ɚ", "ɞ", "ɤ", "ɨ", "ɪ", "ɯ", "ɶ", "ʊ", "ʌ", "ʏ",
            "ø", "œ",
        )
    ),
}

CONSONANTS = {
    "en": frozenset(
        (
            "b", "d", "f", "g", "h", "j", "k", "l", "m", "n", "p", "r",
            "q", "s", "t", "v", "w", "x", "z", "ç", "ð", "β", "ɕ", "ɖ",
            "ɗ", "ɟ", "ɢ", "ɡ", "ɣ", "ɥ", "ɦ", "ɫ", "ɬ", "ɭ", "ɱ", "ɲ",
            "ɳ", "ɸ", "ɹ", "ɻ", "ɽ", "ɾ", "ʀ", "ʁ", "ʂ", "ʃ", "ʈ", "ʋ",
            "ʎ", "ʑ", "ʒ", "ʔ", "ʕ", "ʝ", "ʟ", "ʍ", "θ", "χ", "ŋ", "ł",
            "tʃ", "dʒ", "t͡ʃ", "d͡ʒ", "t͜ʃ", "d͜ʒ", "ʤ",
        )
    ),
    "de": frozenset(
        (
            "b", "c", "d", "f", "g", "h", "j", "k", "l", "m", "n", "p",
            "q", "r", "s", "t", "v", "w", "x", "z", "ç", "ð", "β", "ɓ",
            "ɕ", "ɟ", "ɡ", "ɣ", "ɥ", "ɦ", "ɫ", "ɬ", "ɮ", "ɱ", "ɲ", "ɴ",
            "ɸ", "ɹ", "ɺ", "ɽ", "ɾ", "ʀ", "ʁ", "ʂ", "ʃ", "ʈ", "ʋ", "ʎ",
            "ʑ", "ʒ", "ʔ", "ʕ", "ʝ", "ʟ", "ʙ", "θ", "χ", "ŋ", "pf", "ts",
            "tʃ", "dʒ", "p͡f", "t͡s", "t͡ʃ", "d͡ʒ", "p͜f", "t͜s", "t͜ʃ",
            "d͜ʒ", "ʦ", "ʧ",
        )
    ),
    "tr": frozenset(
        (
            "b", "c", "d", "f", "g", "h", "j", "k", "l", "m", "n", "p",
            "q", "r", "s", "t", "v", "w", "x", "z", "ç", "β", "ɕ", "ɟ",
            "ɡ", "ɣ", "ɥ", "ɦ", "ɫ", "ɰ", "ɱ", "ɲ", "ɳ", "ɸ", "ɹ", "ɾ", "ł",
            "ʀ", "ʁ", "ʃ", "ʈ", "ʋ", "ʎ", "ʑ", "ʐ", "ʒ", "ʔ", "ʕ", "ʝ",
            "θ", "χ", "ŋ", "tʃ", "dʒ", "t͡ʃ", "d͡ʒ", "t͜ʃ", "d͜ʒ", "ʧ",
            "ʤ",
        )
    ),
}

# Spacing modifier letters and IPA marks that qualify the preceding phoneme.
# Unicode combining marks are attached by category in ``tokenize_ipa`` too.
POSTFIX_MODIFIERS = frozenset(
    (
        ":", "ː", "ˑ", "̆", "̯", "̃", "̥", "̬", "̩", "̪", "̺", "̻", "̝", "̞",
        "̘", "̙", "̚", "̰", "̤", "̹", "̜", "̟", "̠", "̼", "̽", "ʰ", "ʷ",
        "ˀ", "˔", "˭", "ʰ", "ʱ", "ʲ", "ˠ", "ˤ", "ʴ", "ʷ", "ⁿ", "ˡ",
        "˞", "ᵈ", "ᵏ", "ᵝ",
    )
)

SCHEMA = """
CREATE TABLE dictionary (
    word TEXT NOT NULL,
    ipa TEXT NOT NULL,
    ipa_reversed TEXT NOT NULL,
    rhyme_key_reversed TEXT NOT NULL,
    assonance_reversed TEXT NOT NULL,
    PRIMARY KEY (word, ipa)
) WITHOUT ROWID;
"""

INDEXES = """
CREATE INDEX idx_ipa_reversed ON dictionary(ipa_reversed);
CREATE INDEX idx_rhyme_key_reversed ON dictionary(rhyme_key_reversed);
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


def inventory(lang_code: str) -> tuple[frozenset[str], tuple[str, ...]]:
    vowels = VOWELS[lang_code]
    phonemes = vowels | CONSONANTS[lang_code]
    return vowels, tuple(sorted(phonemes, key=lambda value: (-len(value), value)))


def is_postfix_modifier(character: str) -> bool:
    return character in POSTFIX_MODIFIERS or unicodedata.category(character) in {
        "Mn",
        "Me",
    }


def tokenize_ipa(
    ipa: str, lang_code: str, unknown: Counter[str] | None = None
) -> list[str]:
    """Return phoneme/stress tokens, preserving and reporting unknown clusters."""
    ipa = unicodedata.normalize("NFC", ipa.strip())
    vowels, candidates = inventory(lang_code)
    del vowels  # Classification happens separately; candidates drive scanning.
    known_single_bases = {value for value in candidates if len(value) == 1}
    tokens: list[str] = []
    position = 0

    while position < len(ipa):
        character = ipa[position]
        if character in IGNORED_SEPARATORS:
            position += 1
            continue
        if character in STRESS_MARKERS:
            tokens.append(character)
            position += 1
            continue

        match = next(
            (value for value in candidates if ipa.startswith(value, position)), None
        )
        if match is None:
            # NFC retains precomposed phonemes such as á. When their NFD form
            # is a known base plus diacritics, recognize the whole code point
            # as that qualified phoneme rather than requiring an inventory row
            # for every canonically equivalent spelling.
            decomposed = unicodedata.normalize("NFD", character)
            if (
                len(decomposed) > 1
                and decomposed[0] in known_single_bases
                and all(is_postfix_modifier(value) for value in decomposed[1:])
            ):
                match = character
                position += 1
                while position < len(ipa) and is_postfix_modifier(ipa[position]):
                    match += ipa[position]
                    position += 1
                tokens.append(match)
                continue

            end = position + 1
            while end < len(ipa) and is_postfix_modifier(ipa[end]):
                end += 1
            match = ipa[position:end]
            if unknown is not None:
                unknown[match] += 1
            # Unknown clusters stay visible in derived strings. Keeping them
            # and counting them is lossless; treating each code point as a
            # known phoneme or dropping it would silently corrupt tail keys.
            tokens.append(match)
            position = end
            continue

        position += len(match)
        while position < len(ipa) and is_postfix_modifier(ipa[position]):
            match += ipa[position]
            position += 1
        tokens.append(match)

    return tokens


def token_is_vowel(token: str, lang_code: str) -> bool:
    if any(token.startswith(vowel) for vowel in VOWELS[lang_code]):
        return True
    decomposed = unicodedata.normalize("NFD", token)
    return any(
        decomposed.startswith(unicodedata.normalize("NFD", vowel))
        for vowel in VOWELS[lang_code]
    )


def derived_values(tokens: list[str], lang_code: str) -> tuple[str, str, str, bool]:
    """Compute reversed full IPA, stress-anchored rhyme key, and assonance."""
    ipa_reversed = " ".join(reversed(tokens))
    vowel_positions = [
        index for index, token in enumerate(tokens) if token_is_vowel(token, lang_code)
    ]
    stress_positions = [
        index for index, token in enumerate(tokens) if token == PRIMARY_STRESS
    ]
    used_fallback = not stress_positions

    rhyme_start = None
    if stress_positions:
        last_stress = stress_positions[-1]
        rhyme_start = next(
            (index for index in vowel_positions if index > last_stress), None
        )
    elif vowel_positions:
        rhyme_start = vowel_positions[-1]

    rhyme_tokens = tokens[rhyme_start:] if rhyme_start is not None else []
    vowel_tokens = [token for token in tokens if token_is_vowel(token, lang_code)]
    return (
        ipa_reversed,
        " ".join(reversed(rhyme_tokens)),
        " ".join(reversed(vowel_tokens)),
        used_fallback,
    )


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
        ipas.append(unicodedata.normalize("NFC", value.strip()))
    return word, ipas


def iter_rows(
    input_path: Path,
    lang_code: str,
    unknown: Counter[str],
    progress: ProgressBar,
) -> Iterable[tuple[str, str, str, str, str, bool]]:
    lines = 0
    rows = 0
    bytes_processed = 0
    with input_path.open("r", encoding="utf-8") as source:
        for lines, line in enumerate(source, start=1):
            bytes_processed += len(line.encode("utf-8"))
            word, ipas = parse_wordlist_line(input_path, lines, line)
            for ipa in ipas:
                tokens = tokenize_ipa(ipa, lang_code, unknown)
                ipa_reversed, rhyme_key, assonance, fallback = derived_values(
                    tokens, lang_code
                )
                rows += 1
                yield word, ipa, ipa_reversed, rhyme_key, assonance, fallback
            if lines % 1_000 == 0:
                progress.update(bytes_processed, lines, rows)
        progress.update(input_path.stat().st_size, lines, rows)


def validate_release_version(release_version: str, output_path: Path) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", release_version):
        raise ValueError(
            "release version must contain only letters, digits, '.', '_', '+', or '-'"
        )
    if release_version not in output_path.stem:
        raise ValueError(
            f"output filename must include Kaikki release version {release_version!r}"
        )


def build_database(
    input_path: Path, output_path: Path, lang_code: str, release_version: str
) -> tuple[int, int, int, Counter[str]]:
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
    fallback_count = 0
    progress = ProgressBar(input_path.stat().st_size)
    connection: sqlite3.Connection | None = None

    try:
        connection = sqlite3.connect(part_path)
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.executescript(SCHEMA)
        insert = "INSERT INTO dictionary VALUES (?, ?, ?, ?, ?)"
        for word, ipa, ipa_reversed, rhyme_key, assonance, fallback in iter_rows(
            input_path, lang_code, unknown, progress
        ):
            try:
                connection.execute(
                    insert, (word, ipa, ipa_reversed, rhyme_key, assonance)
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"duplicate (word, ipa) pair: {word!r}, {ipa!r}") from error
            row_count += 1
            fallback_count += int(fallback)

        connection.executescript(INDEXES)
        connection.commit()
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

    return row_count, unique_words, fallback_count, unknown


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
        rows, words, fallback, unknown = build_database(
            args.input, args.output, args.lang_code, args.release_version
        )
    except (FileNotFoundError, OSError, sqlite3.Error, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Kaikki release       : {args.release_version}")
    print(f"Total rows           : {rows:,}")
    print(f"Unique words         : {words:,}")
    print(f"No primary stress    : {fallback:,}")
    print(f"Unrecognized symbols : {sum(unknown.values()):,}")
    for symbol, count in sorted(unknown.items(), key=lambda item: (-item[1], item[0])):
        codepoints = " ".join(f"U+{ord(character):04X}" for character in symbol)
        print(f"  {symbol!r} ({codepoints}): {count:,}")
    print(f"Output               : {args.output}")


if __name__ == "__main__":
    main()

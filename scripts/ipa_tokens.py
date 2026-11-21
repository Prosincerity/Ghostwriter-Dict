"""Tokenize IPA using the audited English, German, and Turkish inventories."""

import unicodedata
from collections import Counter
from functools import lru_cache


LANGUAGES = ("en", "de", "tr")
STRESS_MARKERS = frozenset(("ˈ", "ˌ"))
# A dash marks a pronunciation fragment and must remain an unknown token.
IGNORED_SEPARATORS = frozenset(" ./[]()⟨⟩⁽⁾|‿⁀‖⫽︎")

# These inventories are an audited representation of phonemes occurring in
# the English, German, and Turkish Kaikki-derived wordlists, including common
# broad/narrow transcription variants. They are deliberately language-local:
# unknown tokens stop the build with their source location so future source
# drift is visible and the inventory can be expanded after a release audit.
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
            "ʊ", "ʌ", "ʏ", "ø", "œ", "ᵊ", "aɪ", "aʊ", "ɔʏ", "oʏ", "ʊɪ",
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
            "b", "c", "d", "f", "g", "h", "j", "k", "l", "m", "n", "p", "r",
            "q", "s", "t", "v", "w", "x", "z", "ç", "ð", "β", "ɓ", "ɕ", "ɖ",
            "ɗ", "ɟ", "ɢ", "ɡ", "ɣ", "ɥ", "ɦ", "ɫ", "ɬ", "ɭ", "ɱ", "ɲ",
            "ɳ", "ɸ", "ɹ", "ɺ", "ɻ", "ɽ", "ɾ", "ʀ", "ʁ", "ʂ", "ʃ", "ʈ", "ʋ",
            "ʎ", "ʑ", "ʒ", "ʔ", "ʕ", "ʝ", "ʟ", "ʙ", "ʍ", "θ", "χ", "ŋ", "ł",
            "ǀ", "ǁ", "ǃ",
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
            "ħ", "ɰ",
            "tʃ", "dʒ", "p͡f", "t͡s", "t͡ʃ", "d͡ʒ", "p͜f", "t͜s", "t͜ʃ",
            "d͜ʒ", "ʦ", "ʧ", "ǀ", "ǁ", "ǃ",
        )
    ),
    "tr": frozenset(
        (
            "b", "c", "d", "f", "g", "h", "j", "k", "l", "m", "n", "p",
            "q", "r", "s", "t", "v", "w", "x", "z", "ç", "β", "ɕ", "ɟ",
            "ɡ", "ɣ", "ɥ", "ɦ", "ɫ", "ɰ", "ɱ", "ɲ", "ɳ", "ɸ", "ɹ", "ɾ", "ł",
            "ʀ", "ʁ", "ʃ", "ʈ", "ʋ", "ʎ", "ʑ", "ʐ", "ʒ", "ʔ", "ʕ", "ʝ",
            "θ", "χ", "ŋ", "tʃ", "dʒ", "t͡ʃ", "d͡ʒ", "t͜ʃ", "d͜ʒ", "ʧ",
            "ʤ", "ǀ", "ǁ", "ǃ",
        )
    ),
}

PHONEMES = {lang: VOWELS[lang] | CONSONANTS[lang] for lang in LANGUAGES}
PHONEME_CANDIDATES = {
    lang: tuple(sorted(phonemes, key=lambda value: (-len(value), value)))
    for lang, phonemes in PHONEMES.items()
}

# Keep longest-first precedence without scanning unrelated initials at every offset.
BY_INITIAL = {
    lang: {c: tuple(p for p in phones if p.startswith(c)) for c in {p[0] for p in phones}}
    for lang, phones in PHONEME_CANDIDATES.items()
}


# Spacing modifier letters and IPA marks that qualify the preceding phoneme.
# Unicode combining marks are attached by category in ``analysis_tokens`` too.
POSTFIX_MODIFIERS = frozenset(
    (
        ":", "ː", "ˑ", "̆", "̯", "̃", "̥", "̬", "̩", "̪", "̺", "̻", "̝", "̞",
        "̘", "̙", "̚", "̰", "̤", "̹", "̜", "̟", "̠", "̼", "̽",
        "ˀ", "˔", "˭", "ʰ", "ʱ", "ʲ", "ˠ", "ˤ", "ʴ", "ʷ", "ⁿ", "ˡ",
        "˞", "ʳ", "ʵ", "ʶ", "ˣ", "˕", "˖", "ᵈ", "ᵏ", "ᵐ", "ᵝ", "ᶦ", "ᶴ",
    )
)

# These spacing modifier letters can legitimately precede a phoneme at a word,
# syllable, or stress boundary. The tokenizer attaches them to the following
# inventory phoneme instead of treating them as independent characters.
PREFIX_MODIFIERS = frozenset(
    ("ˀ", "ʰ", "ʱ", "ʲ", "ˠ", "ˤ", "ʷ", "ⁿ", "ˡ", "ᵈ", "ᵏ", "ᵐ")
)

# Lexical tone and intonation marks are meaningful prosody, not phonemes or
# unknown data. Preserve them as explicit tokens so a release audit can accept
# valid tonal transcriptions without teaching the vowel classifier that they
# are vowels.
PROSODY_MARKERS = frozenset(
    ("˥", "˦", "˧", "˨", "˩", "¹", "²", "³", "⁴", "⁵", "⁻", "↗", "↘", "↑", "↓", "ꜛ", "ꜜ")
)
NON_PHONEME_MARKERS = STRESS_MARKERS | PROSODY_MARKERS


def is_postfix_modifier(character: str) -> bool:
    if character in IGNORED_SEPARATORS:
        return False
    return character in POSTFIX_MODIFIERS or unicodedata.category(character) in {"Mn", "Me"}


def analysis_tokens(
    ipa: str, lang_code: str, unknown: Counter[str] | None = None, *, espeak: bool = False
) -> list[str]:
    """Normalized cleanup/assonance view. Lossless storage uses ipa_reversal."""
    ipa = ipa.replace("\u200d", "͡").replace("\u200c", ".")
    if espeak:
        ipa = ipa.replace("_", ".")
    ipa = unicodedata.normalize("NFC", ipa.strip())
    candidates = BY_INITIAL[lang_code]

    def match_base(offset: int) -> str | None:
        if offset == len(ipa):
            return None
        match = next((value for value in candidates.get(ipa[offset], ()) if ipa.startswith(value, offset)), None)
        if match is not None:
            return match
        # NFC may compose an inventory base and diacritic into one code point.
        character = ipa[offset]
        decomposed = unicodedata.normalize("NFD", character)
        if (len(decomposed) > 1 and decomposed[0] in PHONEMES[lang_code]
                and all(is_postfix_modifier(mark) for mark in decomposed[1:])):
            return character
        return None

    def after_modifiers(offset: int) -> int:
        while offset < len(ipa) and is_postfix_modifier(ipa[offset]):
            offset += 1
        return offset

    tokens: list[str] = []
    position = 0
    at_boundary = True

    while position < len(ipa):
        character = ipa[position]
        if character in IGNORED_SEPARATORS:
            position += 1
            at_boundary = True
            continue
        if character in NON_PHONEME_MARKERS:
            tokens.append(character)
            position += 1
            at_boundary = True
            continue

        if is_postfix_modifier(character):
            end = after_modifiers(position + 1)
            modifiers = ipa[position:end]
            following = match_base(end) if at_boundary and character in PREFIX_MODIFIERS else None
            if following is not None:
                end = after_modifiers(end + len(following))
                tokens.append(ipa[position:end])
            elif tokens and tokens[-1] not in NON_PHONEME_MARKERS:
                tokens[-1] += modifiers
            else:
                tokens.append(modifiers)
                if unknown is not None:
                    unknown[modifiers] += 1
        else:
            match = match_base(position)
            end = after_modifiers(position + len(match or character))
            token = ipa[position:end]
            tokens.append(token)
            if match is None and unknown is not None:
                unknown[token] += 1
        position = end
        at_boundary = False

    return tokens


@lru_cache(maxsize=8192)
def token_is_vowel(token: str, lang_code: str) -> bool:
    without_prefix = token
    while without_prefix and without_prefix[0] in PREFIX_MODIFIERS:
        without_prefix = without_prefix[1:]
    if any(without_prefix.startswith(vowel) for vowel in VOWELS[lang_code]):
        return True
    decomposed = unicodedata.normalize("NFD", without_prefix)
    return any(
        decomposed.startswith(unicodedata.normalize("NFD", vowel))
        for vowel in VOWELS[lang_code]
    )

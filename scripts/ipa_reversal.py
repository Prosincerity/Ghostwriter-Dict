"""Lossless IPA segmentation and the version 2 SQLite search-key codec."""

import re
import unicodedata
from functools import lru_cache
from typing import NamedTuple

from ipa_tokens import (
    IGNORED_SEPARATORS, LANGUAGES, PHONEMES, POSTFIX_MODIFIERS, PREFIX_MODIFIERS,
    PROSODY_MARKERS, STRESS_MARKERS, VOWELS,
)

SOURCES = ("wiktionary", "espeak", "espeak-separated", "espeak-tied")
TIES = frozenset("͜͡\u200d")
PREFIX = PREFIX_MODIFIERS | frozenset("ᵑᶮ")
NON_SEGMENT_LETTERS = PREFIX | STRESS_MARKERS | PROSODY_MARKERS
POSTFIX = POSTFIX_MODIFIERS | frozenset("ʼ")
BOUNDARIES = IGNORED_SEPARATORS | frozenset("~·,;-#‧\u200c")
PAIRS = {"[": "]", "(": ")", "⟨": "⟩", "⁽": "⁾"}
MIRROR = {**PAIRS, **{right: left for left, right in PAIRS.items()}}
LEGACY = frozenset("ʦʧʤ")
MULTI = {lang: {p for p in phones if len(p) > 1 and TIES.isdisjoint(p)}
         for lang, phones in PHONEMES.items()}
AMBIGUOUS_PAIRS = set.union(*MULTI.values()) | {"dz"}
LANGUAGE_SWITCH = re.compile(r"\([a-z]{2,3}(?:-[a-z0-9]+)*\)")
OPTIONAL_MODIFIER = re.compile(r"(?:\(([^()]+)\)|⁽([^⁽⁾]+)⁾)")


class Analysis(NamedTuple):
    tokens: tuple[str, ...]
    segments: tuple[str, ...]
    issues: tuple[tuple[str, str], ...]


@lru_cache(maxsize=4096)
def base_letter(char: str, lang: str) -> str:
    if char in PHONEMES[lang] or char in LEGACY:
        return char
    nfd = unicodedata.normalize("NFD", char)
    if len(nfd) > 1 and nfd[0] in PHONEMES[lang] and all(is_modifier(c) for c in nfd[1:]):
        return nfd[0]
    return ""


@lru_cache(maxsize=1024)
def is_modifier(char: str) -> bool:
    return (char in POSTFIX or unicodedata.category(char) in {"Mn", "Me"}) and char not in TIES and char != "︎"


def has_engine_separators(ipa: str, source: str) -> bool:
    return source == "espeak-separated" or (source == "espeak" and ("_" in ipa or "\u200c" in ipa))


def after_marks(ipa: str, pos: int) -> int:
    while pos < len(ipa) and is_modifier(ipa[pos]):
        pos += 1
    return pos


def opaque_component(char: str) -> bool:
    """An unfamiliar sound symbol may still be explicitly joined by the source."""
    return char.isalpha() and char not in NON_SEGMENT_LETTERS and not is_modifier(char)


def followed_by_tie(ipa, pos):
    if pos < len(ipa) and ipa[pos] in " (⁽":
        # Look ahead without reporting marks before their host is consumed.
        pos = include_written_modifiers(ipa, pos, [])
    return pos < len(ipa) and ipa[pos] in TIES


def segment_end(ipa, first, base, lang, source, explicit, issues):
    size = len(ipa)
    i = after_marks(ipa, first + 1)
    joined = False
    while i < size:
        if ipa[i] in TIES:
            # A combining mark can occur on either side of the tie in the
            # original encoding. Neither the tie nor the marks are reordered.
            right = after_marks(ipa, i + 1)
            if right >= size or not (base_letter(ipa[right], lang) or opaque_component(ipa[right])):
                issues.append(("dangling_tie", ipa[first:right]))
                return include_written_modifiers(ipa, right, issues)
            i = after_marks(ipa, right + 1)
            if not base_letter(ipa[right], lang):
                issues.append(("unknown", ipa[right:i]))
            joined = True
            continue
        right_base = base_letter(ipa[i], lang)
        if not right_base and not (explicit and opaque_component(ipa[i])):
            end = include_written_modifiers(ipa, i, issues)
            if end > i and (explicit or (end < size and ipa[end] in TIES)):
                i = end
                continue
            return end
        end = after_marks(ipa, i + 1)
        pair = base + right_base
        if explicit:
            if not right_base:
                issues.append(("unknown", ipa[i:end]))
            i = end
            continue
        if source == "espeak-tied" or joined or followed_by_tie(ipa, end):
            break
        left_marks = ipa[first + 1:i]
        glide = (base in VOWELS[lang] and right_base in VOWELS[lang]
                 and ("̯" in left_marks or "̯" in ipa[i + 1:end]))
        if glide or (pair in MULTI[lang] and not any(m in left_marks for m in "ːˑ:")):
            if not glide:
                issues.append(("inferred_multigraph", pair))
            i = end
            joined = True
            continue
        if pair in AMBIGUOUS_PAIRS:
            issues.append(("possible_multigraph_split", pair))
        break
    return i


def include_written_modifiers(ipa, i, issues):
    size = len(ipa)
    # Source spellings such as n ̩ and p⁽ʲ⁾ identify the modifier's host.
    # Include the intervening notation in its original place inside the token.
    while i < size:
        if ipa[i] in "(⁽":
            optional = OPTIONAL_MODIFIER.match(ipa, i)
            if optional and all(is_modifier(m) for m in (optional[1] or optional[2])):
                i = after_marks(ipa, optional.end())
                continue
        if ipa[i] == " ":
            next_mark = i + 1
            while next_mark < size and ipa[next_mark] == " ":
                next_mark += 1
            if next_mark < size and unicodedata.combining(ipa[next_mark]) and is_modifier(ipa[next_mark]):
                issues.append(("spaced_diacritic", ipa[i:after_marks(ipa, next_mark)]))
                i = after_marks(ipa, next_mark)
                continue
        break
    return i


def analyze(ipa: str, lang: str, source: str = "wiktionary") -> Analysis:
    """Slice the original text. Normalized base letters are used only for matching."""
    if lang not in LANGUAGES or source not in SOURCES:
        raise ValueError(f"unsupported IPA inventory or source: {lang!r}, {source!r}")
    explicit = has_engine_separators(ipa, source)
    tokens = []
    segments = []
    issues = []
    stack = []
    slash_open = False
    i = 0
    size = len(ipa)

    while i < size:
        start = i
        c = ipa[i]
        if c == "(" and source.startswith("espeak"):
            switch = LANGUAGE_SWITCH.match(ipa, i)
            if switch:
                tokens.append(switch[0])
                issues.append(("engine_language_switch", switch[0]))
                i = switch.end()
                continue
        if c in BOUNDARIES or c.isspace() or c in PROSODY_MARKERS or (explicit and c == "_"):
            if c in PAIRS:
                stack.append(c)
            elif c in MIRROR:
                if stack and PAIRS[stack[-1]] == c:
                    stack.pop()
                else:
                    issues.append(("unmatched_delimiter", c))
            elif c == "/":
                slash_open = not slash_open
            i += 1
            tokens.append(c)
            continue

        while i < size and ipa[i] in STRESS_MARKERS:
            i += 1
        if i - start > 1:
            issues.append(("repeated_stress", ipa[start:i]))
        prefix_start = i
        while i < size and ipa[i] in PREFIX:
            i += 1
        base = base_letter(ipa[i], lang) if i < size else ""
        if not base and i < size and opaque_component(ipa[i]):
            end = after_marks(ipa, i + 1)
            if explicit or followed_by_tie(ipa, end):
                issues.append(("unknown", ipa[i:end]))
                base = ipa[i]
        if not base:
            if i > start:
                reason = "detached_modifier" if i > prefix_start else "detached_stress"
            else:
                i = after_marks(ipa, i + 1)
                reason = "detached_modifier" if is_modifier(c) or c in TIES else "unknown"
            tokens.append(ipa[start:i])
            issues.append((reason, tokens[-1]))
            continue

        i = segment_end(ipa, i, base, lang, source, explicit, issues)
        token = ipa[start:i]
        tokens.append(token)
        segments.append(token)

    issues.extend(("unmatched_delimiter", c) for c in stack)
    if slash_open:
        issues.append(("unmatched_delimiter", "/"))
    if not segments:
        issues.append(("no_segment", ""))
    return Analysis(tuple(tokens), tuple(segments), tuple(issues))


def analyze_source(ipa: str, lang: str, source: str, reference: str | None = None) -> tuple[Analysis, str]:
    """Use engine boundaries only after checking every original code point."""
    if has_engine_separators(ipa, source):
        return analyze(ipa, lang, source), "source_separated"
    if source == "espeak-tied":
        return analyze(ipa, lang, source), "source_tied"
    if source == "espeak" and reference is not None:
        marked = reference.strip()
        if marked.replace("_", "") == ipa:
            parsed = analyze(marked, lang, "espeak-separated")
            tokens = tuple(t for t in parsed.tokens if t != "_")
            return Analysis(tokens, parsed.segments, parsed.issues), "engine_exact_match"
        parsed = analyze(ipa, lang, source)
        return parsed, "engine_spelling_differs"
    return analyze(ipa, lang, source), "source_notation_and_inventory"


def tokenize_ipa(ipa: str, lang: str, source: str = "wiktionary", *,
                 reference: str | None = None) -> tuple[str, ...]:
    parsed, _ = analyze_source(ipa, lang, source, reference)
    return parsed.tokens


def reconstruct(tokens: tuple[str, ...]) -> str:
    return "".join(tokens)


def reverse_tokens(tokens: tuple[str, ...]) -> tuple[str, ...]:
    return tokens[::-1]


# Mirror delimiter glyphs only in the serialized view. Source tokens stay intact.
# Escape a literal backslash and every whitespace code point, including ASCII space.
@lru_cache(maxsize=8192)
def _encode(token: str) -> str:
    if not token:
        raise ValueError("empty IPA token")
    return "".join("\\\\" if c == "\\" else f"\\u{{{ord(c):x}}}" if c.isspace() else c
                   for c in MIRROR.get(token, token))


_ESCAPE = re.compile(r"\\(?:\\|u\{([0-9a-f]{1,6})\})")


@lru_cache(maxsize=8192)
def _decode(field: str) -> str:
    if "\\" in field:
        field = _ESCAPE.sub(lambda m: chr(int(m[1], 16)) if m[1] else "\\", field)
    return MIRROR.get(field, field)


class ReversedIPA(str):
    """Serialized reversed tokens, distinguished from raw IPA even for one token."""


class RoundTripError(ValueError):
    def __init__(self, ipa, tokens, key, restored, reason):
        self.details = dict(ipa=ipa, tokens=tokens, serialization=key, restored=restored, reason=reason)
        super().__init__(f"IPA round-trip failure: {self.details!r}")


def serialize(tokens: tuple[str, ...]) -> ReversedIPA:
    return ReversedIPA(" ".join(map(_encode, tokens)))


def deserialize(key: str) -> tuple[str, ...]:
    if not key:
        return ()
    fields = key.split(" ")
    tokens = tuple(map(_decode, fields))
    if any(not token or _encode(token) != field for field, token in zip(fields, tokens)):
        raise ValueError("noncanonical serialized IPA token")
    return tokens


def restore_original(key: str) -> str:
    return reconstruct(reverse_tokens(deserialize(key)))


def reverse_ipa_for_db(ipa: str, lang: str, source: str = "wiktionary", *,
                       reference: str | None = None) -> ReversedIPA:
    return serialize(reverse_tokens(tokenize_ipa(ipa, lang, source, reference=reference)))


def reverse_ipa(ipa: str, lang: str, source: str = "wiktionary", *,
                reference: str | None = None) -> str:
    if isinstance(ipa, ReversedIPA):
        return restore_original(ipa)
    return reverse_ipa_for_db(ipa, lang, source, reference=reference)


def verified_key(ipa: str, analysis: Analysis) -> ReversedIPA:
    reversed_tokens = reverse_tokens(analysis.tokens)
    key = None
    try:
        key = serialize(reversed_tokens)
        decoded = deserialize(key)
    except ValueError as error:
        raise RoundTripError(ipa, analysis.tokens, key, None, str(error)) from error
    restored = reconstruct(reverse_tokens(decoded))
    if reconstruct(analysis.tokens) != ipa or decoded != reversed_tokens or restored != ipa:
        raise RoundTripError(ipa, analysis.tokens, key, restored, "token or spelling mismatch")
    return key

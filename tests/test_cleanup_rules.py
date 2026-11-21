import importlib.util
import sys
import string
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WORDS = load("clean_rhyme_words_rules", SCRIPTS_DIR / "clean_rhyme_words.py")
IPA = load("clean_rhyme_ipa_rules", SCRIPTS_DIR / "clean_rhyme_ipa.py")


class PronunciationCleanupTest(unittest.TestCase):
    def clean(self, ipa, lang_code="en"):
        return IPA.clean_pronunciation(ipa, lang_code)

    def test_splits_expands_and_normalizes_supported_notation(self):
        self.assertEqual(self.clean("[æm] ~ [am]")[0], ["[æm]", "[am]"])
        valid, _, rejects = self.clean("[-ne ~ -nɛ]")
        self.assertEqual(valid, [])
        self.assertEqual([item["reason"] for item in rejects],
                         ["incomplete_pronunciation"] * 2)
        self.assertEqual(self.clean("[dɔ(ː)ɡ]", "de")[0], ["[dɔɡ]", "[dɔːɡ]"])
        self.assertEqual(self.clean("/'ʃeɪk/")[0], ["/ˈʃeɪk/"])
        self.assertEqual(self.clean("[drɛ·sɑːʒ]")[0], ["[drɛ.sɑːʒ]"])

    def test_keeps_audited_modifiers_and_tone_markers(self):
        self.assertEqual(self.clean("/kɑʳ˦˨/")[0], ["/kɑʳ˦˨/"])
        self.assertEqual(self.clean("/ħaᵊ/", "de")[0], ["/ħaᵊ/"])
        self.assertEqual(self.clean("/aˤ/", "tr")[0], ["/aˤ/"])

    def test_rejects_malformed_or_ambiguous_notation(self):
        examples = {
            "embedded_control": "/siz\nler/",
            "incomplete_pronunciation": "/hæl…/",
            "ambiguous_comma_notation": "[a,b]",
            "mixed_uppercase_notation": "[IntEntsioːn]",
            "non_ipa_orthographic_symbol": "[αa]",
            "orthographic_dotless_i": "[adıyaman]",
            "invalid_delimiters": "/abc]",
            "unrecognized_tokens": "/a☃/",
        }
        for reason, ipa in examples.items():
            with self.subTest(reason=reason):
                valid, _, rejects = self.clean(ipa, "tr")
                self.assertEqual(valid, [])
                self.assertEqual(rejects[0]["reason"], reason)

    def test_underscore_is_not_silently_discarded_from_ipa(self):
        valid, _, rejects = self.clean("/ˈa_b/")
        self.assertEqual(valid, [])
        self.assertEqual(rejects[0]["reason"], "unrecognized_tokens")
        self.assertEqual(rejects[0]["details"]["tokens"], {"_": 1})

    def test_dashes_mark_incomplete_ipa_in_english_and_german(self):
        examples = {
            "de": [
                "/-ˌmeːsɪç/", "/-ˌmɛːsɪk/", "/ʊnˈt͡seːm-/",
                "/-ˌt͡sʏçtɪɡŋ̩/", "[ˈjɑɔ̯-]", "[ˈjɑɔ̯s-]",
                "[-ˌvaɛ̯-]", "/-ˈneːɐ̯/", "[ˈfʊɐ̯-]",
                "/-ˌt͡sʊx/", "[ˈfɛns.tɐ-]", "[ˈfɛɐ̯-]",
            ],
            "en": ["/-ˈkæt/", "/ˈkæt-/", "/ˈk-æt/", "/ˈkæt–/"],
        }
        for lang_code, ipas in examples.items():
            for ipa in ipas:
                with self.subTest(lang_code=lang_code, ipa=ipa):
                    valid, _, rejects = self.clean(ipa, lang_code)
                    self.assertEqual(valid, [])
                    self.assertEqual(rejects[0]["reason"], "incomplete_pronunciation")
                    self.assertEqual(rejects[0]["details"]["characters"],
                                     ["–"] if "–" in ipa else ["-"])

    def test_rejects_pronunciations_with_only_prosody_markers(self):
        for ipa in ("/ˈ/", "[ˌ˥]", "↗"):
            with self.subTest(ipa=ipa):
                valid, _, rejects = IPA.clean_pronunciation(ipa, "en")
                self.assertEqual(valid, [])
                self.assertEqual(rejects[0]["reason"], "empty_pronunciation")

    def test_complete_alternative_survives_an_incomplete_sibling(self):
        for value in ("/ˈma~a…/", "/ˈma/, [a...]"):
            with self.subTest(value=value):
                valid, _, rejects = self.clean(value)
                self.assertEqual(valid, ["/ˈma/"])
                self.assertEqual([item["reason"] for item in rejects], ["incomplete_pronunciation"])

    def test_optional_groups_are_nonnested_and_capped(self):
        self.assertEqual(
            self.clean("/a((b))c/")[2][0]["reason"], "invalid_optional_group"
        )
        self.assertEqual(
            self.clean("/a(b)(c)(d)(e)/")[2][0]["reason"],
            "too_many_optional_variants",
        )

    def test_optional_variant_limit_applies_across_alternatives(self):
        valid, _, rejects = self.clean("/a(b)(c)(d)~e(f)(g)(h)/")
        self.assertEqual(valid, [])
        self.assertEqual(rejects[0]["reason"], "too_many_optional_variants")
        plain = "~".join(f"/a{'b' * i}/" for i in range(9))
        self.assertEqual(len(self.clean(plain)[0]), 9)

    def test_stress_mark_requires_a_following_syllabic_nucleus(self):
        for ipa in ("/ˈb/", "/abˈc/", "/aˌ/", "/ˈbˌa/", "/ˈˌa/"):
            with self.subTest(ipa=ipa):
                valid, _, rejects = self.clean(ipa)
                self.assertEqual(valid, [])
                self.assertEqual(rejects[0]["reason"], "misplaced_stress_mark")
        self.assertEqual(self.clean("/ˈn̩/")[0], ["/ˈn̩/"])


class HeadwordCleanupTest(unittest.TestCase):
    def test_language_alphabets_and_ascii_digits_are_allowed(self):
        examples = {
            "en": "clichéstateoftheart",
            "de": "PokémonÜbergröße2026",
            "tr": "hâlâQWXy7nci",
        }
        for lang_code, word in examples.items():
            with self.subTest(lang_code=lang_code):
                self.assertIsNone(WORDS.headword_rejection(word, lang_code))

    def test_shared_alphabet_accepts_latin_loanword_letters_in_all_languages(self):
        for lang_code in ("en", "de", "tr"):
            for word in ("cœur", "façade", "piñata", "Łódź", "smörgåsbord"):
                with self.subTest(lang_code=lang_code, word=word):
                    self.assertIsNone(WORDS.headword_rejection(word, lang_code))

    def test_reported_examples_are_rejected_with_exact_characters(self):
        examples = {
            "de": ["ǃXóõs", "⁊c.", "◌̈", "◌͝◌", "♥-lichen", "ꝛc.", "🄰"],
            "en": ["⠆", "⠇⠗", "⠈⠒⠏", "🔛🔝", "🚂🦵"],
            "tr": ["◌̧"],
        }
        for lang_code, words in examples.items():
            for word in words:
                with self.subTest(lang_code=lang_code, word=word):
                    rejection = WORDS.headword_rejection(word, lang_code)
                    self.assertIsNotNone(rejection)
                    self.assertEqual(
                        rejection["reason"], "disallowed_headword_characters"
                    )
                    self.assertTrue(rejection["details"]["invalid_characters"])

    def test_okina_is_a_permitted_letter(self):
        self.assertIsNone(WORDS.headword_rejection("Hawaiʻian", "en"))

    def test_punctuation_is_rejected_even_between_letters(self):
        for lang in ("en", "de", "tr"):
            for symbol in string.punctuation + "、。؛":
                with self.subTest(lang=lang, symbol=symbol):
                    _, _, rejection = WORDS.clean_headword("ab" + symbol + "cd", lang)
                    self.assertEqual(rejection["reason"], "disallowed_headword_characters")
                    self.assertEqual(rejection["details"]["invalid_characters"][0]["character"], symbol)

    def test_single_letter_entries_are_rejected(self):
        for lang_code, word in (("de", "ü"), ("de", "ö"), ("de", "ß")):
            with self.subTest(word=word):
                rejection = WORDS.headword_rejection(word, lang_code)
                self.assertEqual(rejection["reason"], "single_letter_headword")
        self.assertIsNone(WORDS.headword_rejection("7", "de"))

    def test_requested_obfuscated_entries_are_rejected(self):
        for word in ("tw*t", "tw*ts", "tw@", "tw@s", "tw@t", "tw@ts"):
            with self.subTest(word=word):
                rejection = WORDS.headword_rejection(word, "en")
                self.assertEqual(rejection["reason"], "disallowed_headword_characters")

    def test_unaudited_ascii_punctuation_is_rejected(self):
        for word in ("email@example.com", "word*word"):
            with self.subTest(word=word):
                rejection = WORDS.headword_rejection(word, "en")
                self.assertEqual(rejection["reason"], "disallowed_headword_characters")

    def test_percent_and_plus_are_rejected_in_every_position(self):
        for lang in ("en", "de", "tr"):
            for symbol in ("%", "+", "％", "＋"):
                for word in (symbol, symbol + "abc", "ab" + symbol + "cd",
                             "100" + symbol, "C" + symbol * 2):
                    with self.subTest(lang=lang, word=word):
                        _, _, rejection = WORDS.clean_headword(word, lang)
                        self.assertEqual(rejection["reason"], "disallowed_headword_characters")
                        self.assertEqual(rejection["details"]["invalid_characters"][0]["character"], symbol)

    def test_malformed_or_non_ascii_spacing_and_symbols_are_rejected(self):
        for word in (
            "two words",
            "Victory Day",
            "Dungeons & Dragons",
            "a. a. O.",
            " leading",
            "trailing ",
            "two  words",
            "two\twords",
            "non\N{NO-BREAK SPACE}breaking",
            "snow☃man",
        ):
            with self.subTest(word=word):
                self.assertIsNotNone(WORDS.headword_rejection(word, "en"))

    def test_apostrophes_are_rejected_anywhere_after_normalization(self):
        for lang in ("en", "de", "tr"):
            for word in ("'a", "'a'", "'a'a", "'a'ā", "'abd", "don't", "losin’", "‘abc", "7ʼnci"):
                with self.subTest(word=word, lang=lang):
                    _, _, rejection = WORDS.clean_headword(word, lang)
                    self.assertEqual(rejection["reason"], "disallowed_headword_characters")
                    self.assertEqual(rejection["details"]["invalid_characters"][0]["character"], "'")

    def test_search_oriented_headword_normalization(self):
        examples = {
            "d’accord": ("d'accord", ["normalize_apostrophe"]),
            "May–December": ("May-December", ["normalize_dash"]),
            "CO₂": ("CO2", ["normalize_subscript_digit"]),
            "soft\N{SOFT HYPHEN}ware": ("software", ["remove_soft_hyphen"]),
        }
        for original, expected in examples.items():
            with self.subTest(original=original):
                self.assertEqual(WORDS.normalize_headword(original), expected)

    def test_headword_removed_entirely_by_normalization_is_rejected(self):
        normalized, transformations = WORDS.normalize_headword("\N{SOFT HYPHEN}")

        self.assertEqual(normalized, "")
        self.assertEqual(transformations, ["remove_soft_hyphen"])
        self.assertEqual(
            WORDS.headword_rejection(normalized, "en"),
            {
                "reason": "empty_headword_after_normalization",
                "details": {"invalid_characters": []},
            },
        )



if __name__ == "__main__":
    unittest.main()

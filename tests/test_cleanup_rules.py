import importlib.util
import sys
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
        self.assertEqual(self.clean("[-ne ~ -nɛ]")[0], ["[-ne]", "[-nɛ]"])
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

    def test_rejects_pronunciations_with_only_prosody_markers(self):
        for ipa in ("/ˈ/", "[ˌ˥]", "↗"):
            with self.subTest(ipa=ipa):
                valid, _, rejects = IPA.clean_pronunciation(ipa, "en")
                self.assertEqual(valid, [])
                self.assertEqual(rejects[0]["reason"], "empty_pronunciation")

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
        for ipa in ("/ˈb/", "/abˈc/", "/aˌ/"):
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
            "tr": "hâlâQWXy7'nci",
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

    def test_audited_separators_elisions_and_okina_are_allowed(self):
        for word in (
            "rock&roll",
            "'cause",
            "'Merica",
            "don't",
            "Hawaiʻian",
            "AC/DC",
            "100%",
            "C++",
        ):
            with self.subTest(word=word):
                self.assertIsNone(WORDS.headword_rejection(word, "en"))

    def test_leading_special_characters_are_rejected(self):
        examples = {
            "$hit": "disallowed_headword_characters",
            "%word": "misplaced_headword_symbol",
            "-casting": "headword_contains_dash_or_dot",
        }
        for word, reason in examples.items():
            with self.subTest(word=word):
                rejection = WORDS.headword_rejection(word, "en")
                self.assertEqual(rejection["reason"], reason)

    def test_single_letter_entries_are_rejected(self):
        for lang_code, word in (("de", "ü"), ("de", "ö"), ("de", "ß")):
            with self.subTest(word=word):
                rejection = WORDS.headword_rejection(word, lang_code)
                self.assertEqual(rejection["reason"], "single_letter_headword")
        self.assertIsNone(WORDS.headword_rejection("7", "de"))

    def test_any_dash_or_dot_is_rejected(self):
        for word in ("inter-galactic", "A.B.D.", "t.b.a", "zyg-", "Dr.",
                     "-casting", ".word", "a..b", "a-7", "7-a", "a-&b"):
            with self.subTest(word=word):
                rejection = WORDS.headword_rejection(word, "en")
                self.assertEqual(rejection["reason"], "headword_contains_dash_or_dot")
                position = rejection["details"]["position"]
                self.assertEqual(word[position], rejection["details"]["character"])

    def test_separators_must_be_between_letters(self):
        for word in ("rock&", "rock&&roll"):
            with self.subTest(word=word):
                rejection = WORDS.headword_rejection(word, "en")
                self.assertEqual(rejection["reason"], "misplaced_headword_separator")

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

    def test_audited_symbols_must_use_valid_positions(self):
        for word in ("AC/", "/DC", "100%off", "%100", "C+17", "+C"):
            with self.subTest(word=word):
                rejection = WORDS.headword_rejection(word, "en")
                self.assertEqual(rejection["reason"], "misplaced_headword_symbol")

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

    def test_internal_and_edge_apostrophes_are_allowed(self):
        for word in ("don't", "'Merica", "losin'"):
            with self.subTest(word=word):
                self.assertIsNone(WORDS.headword_rejection(word, "en"))

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

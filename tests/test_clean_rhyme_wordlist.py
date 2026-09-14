import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "clean_rhyme_wordlist", SCRIPTS_DIR / "clean_rhyme_wordlist.py"
)
assert SPEC is not None and SPEC.loader is not None
CLEANER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLEANER)


class PronunciationCleanupTest(unittest.TestCase):
    def clean(self, ipa, lang_code="en"):
        return CLEANER.clean_pronunciation(ipa, lang_code)

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

    def test_optional_groups_are_nonnested_and_capped(self):
        self.assertEqual(
            self.clean("/a((b))c/")[2][0]["reason"], "invalid_optional_group"
        )
        self.assertEqual(
            self.clean("/a(b)(c)(d)(e)/")[2][0]["reason"],
            "too_many_optional_variants",
        )


class HeadwordCleanupTest(unittest.TestCase):
    def test_language_alphabets_and_ascii_digits_are_allowed(self):
        examples = {
            "en": "state-of-the-art",
            "de": "Über-Größe2026",
            "tr": "7'nci",
        }
        for lang_code, word in examples.items():
            with self.subTest(lang_code=lang_code):
                self.assertIsNone(CLEANER.headword_rejection(word, lang_code))

    def test_reported_examples_are_rejected_with_exact_characters(self):
        examples = {
            "de": ["ǃXóõs", "⁊c.", "◌̈", "◌͝◌", "♥-lichen", "ꝛc.", "🄰"],
            "en": ["⠆", "⠇⠗", "⠈⠒⠏", "🔛🔝", "🚂🦵"],
            "tr": ["◌̧"],
        }
        for lang_code, words in examples.items():
            for word in words:
                with self.subTest(lang_code=lang_code, word=word):
                    rejection = CLEANER.headword_rejection(word, lang_code)
                    self.assertIsNotNone(rejection)
                    self.assertEqual(
                        rejection["reason"], "disallowed_headword_characters"
                    )
                    self.assertTrue(rejection["details"]["invalid_characters"])

    def test_spaces_and_invalid_connector_positions_are_rejected(self):
        for word in ("two words", "-casting", "anti-", "state--art", "'word"):
            with self.subTest(word=word):
                self.assertIsNotNone(CLEANER.headword_rejection(word, "en"))

    def test_internal_hyphens_and_apostrophes_are_allowed(self):
        for word in ("mother-in-law", "don't", "state-of-the-art"):
            with self.subTest(word=word):
                self.assertIsNone(CLEANER.headword_rejection(word, "en"))


class WordlistCleanupTest(unittest.TestCase):
    def test_writes_eligible_wordlist_and_audit_sidecars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "wordlist_en_ipa.txt"
            output = root / "wordlist_en_rhyme_eligible.txt"
            rows = [
                ("-casting", ["/ˈkɑːstɪŋ/"]),
                ("anti-", ["/ˈænti/"]),
                ("♥-lichen", ["/ˈlɪtʃən/"]),
                ("mother-in-law", ["/ˈmʌðɚɪnlɔː/"]),
                ("state-of-the-art", ["/ˌsteɪtəvðiˈɑɹt/"]),
                ("7'nci", ["/jeˈdindʒi/"]),
                ("Word2026", ["/ˈwɝd/"]),
                ("variants", ["/'vɛəriənts/", "/bad…/", "/vɛər(i)ənts/"]),
                ("tones", ["/toʊn˦˨/"]),
            ]
            source.write_text(
                "".join(
                    f"{word}\t{json.dumps(ipas, ensure_ascii=False, separators=(',', ':'))}\n"
                    for word, ipas in rows
                ),
                encoding="utf-8",
            )

            report = CLEANER.clean_wordlist(source, output, "en")
            parsed = {}
            for line in output.read_text(encoding="utf-8").splitlines():
                word, encoded = line.split("\t", 1)
                parsed[word] = json.loads(encoded)
            self.assertNotIn("-casting", parsed)
            self.assertNotIn("anti-", parsed)
            self.assertNotIn("♥-lichen", parsed)
            self.assertIn("mother-in-law", parsed)
            self.assertIn("state-of-the-art", parsed)
            self.assertIn("7'nci", parsed)
            self.assertIn("Word2026", parsed)
            self.assertEqual(
                parsed["variants"],
                ["/ˈvɛəriənts/", "/vɛərənts/", "/vɛəriənts/"],
            )
            self.assertEqual(report["policy_version"], "rhyme-cleanup-v2")
            self.assertEqual(report["counts"]["eligible_words"], 6)
            self.assertEqual(report["counts"]["rejected_words"], 3)

            paths = CLEANER.output_paths(output)
            rejects = [
                json.loads(line)
                for line in paths["rejected"].read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                {row["reason"] for row in rejects},
                {
                    "combining_form",
                    "disallowed_headword_characters",
                    "incomplete_pronunciation",
                },
            )
            rejected_words = [
                json.loads(line)
                for line in paths["rejected_words"]
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                {row["word"] for row in rejected_words},
                {"-casting", "anti-", "♥-lichen"},
            )
            self.assertTrue(
                all(row["details"]["invalid_characters"] for row in rejected_words)
            )
            changes = [
                json.loads(line)
                for line in paths["changes"].read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(
                any(row["original_ipa"] == "/'vɛəriənts/" for row in changes)
            )
            self.assertTrue(
                all(not Path(f"{path}.part").exists() for path in paths.values())
            )

    def test_failure_preserves_all_previous_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "bad.txt"
            output = root / "eligible.txt"
            source.write_text('ok\t["/oʊˈkeɪ/"]\nbroken\tnot-json\n', encoding="utf-8")
            paths = CLEANER.output_paths(output)
            for path in paths.values():
                path.write_text("previous\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                CLEANER.clean_wordlist(source, output, "en")
            for path in paths.values():
                self.assertEqual(path.read_text(encoding="utf-8"), "previous\n")
                self.assertFalse(Path(f"{path}.part").exists())


if __name__ == "__main__":
    unittest.main()

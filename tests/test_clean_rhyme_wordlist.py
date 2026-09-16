import importlib.util
import json
import subprocess
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
            "en": "cliché-state-of-the-art",
            "de": "Pokémon-Übergröße2026",
            "tr": "hâlâ-QWX-7'nci",
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

    def test_keyboard_symbols_elisions_and_okina_are_allowed(self):
        for word in (
            "Victory Day",
            "t.b.a",
            "t.b.a.",
            "Dr.",
            "a. a. O.",
            "high-definition television",
            "'cause",
            "'Murica",
            "Hawaiʻian",
            "Dungeons & Dragons",
            "AC/DC",
            "*NSYNC",
            "100%",
            "C++",
            "email@example.com",
        ):
            with self.subTest(word=word):
                self.assertIsNone(CLEANER.headword_rejection(word, "en"))

    def test_malformed_or_non_ascii_spacing_and_symbols_are_rejected(self):
        for word in (
            " leading",
            "trailing ",
            "two  words",
            "two\twords",
            "non\N{NO-BREAK SPACE}breaking",
            "snow☃man",
        ):
            with self.subTest(word=word):
                self.assertIsNotNone(CLEANER.headword_rejection(word, "en"))

    def test_internal_hyphens_and_apostrophes_are_allowed(self):
        for word in ("mother-in-law", "don't", "state-of-the-art", "losin'"):
            with self.subTest(word=word):
                self.assertIsNone(CLEANER.headword_rejection(word, "en"))

    def test_search_oriented_headword_normalization(self):
        examples = {
            "d’accord": ("d'accord", ["normalize_apostrophe"]),
            "May–December": ("May-December", ["normalize_dash"]),
            "CO₂": ("CO2", ["normalize_subscript_digit"]),
            "soft\N{SOFT HYPHEN}ware": ("software", ["remove_soft_hyphen"]),
        }
        for original, expected in examples.items():
            with self.subTest(original=original):
                self.assertEqual(CLEANER.normalize_headword(original), expected)


class WordlistCleanupTest(unittest.TestCase):
    def test_cli_writes_outputs_and_prints_policy_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.txt"
            output = root / "eligible.txt"
            source.write_text('word\t["/ˈwɝd/"]\n', encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "clean_rhyme_wordlist.py"),
                    str(source),
                    str(output),
                    "--lang-code",
                    "en",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                source.read_text(encoding="utf-8"),
            )
            self.assertIn("Cleanup policy       : rhyme-cleanup-v7", result.stdout)
            self.assertTrue(CLEANER.output_paths(output)["report"].is_file())

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
                ("can't", ["/kænt/"]),
                ("can’t", ["/kɑnt/"]),
                ("CO₂", ["/siːoʊˈtuː/"]),
                ("soft\N{SOFT HYPHEN}ware", ["/ˈsɔftwɛr/"]),
                ("Word2026", ["/ˈwɝd/"]),
                ("Victory Day", ["/ˈvɪktəri ˈdeɪ/"]),
                ("t.b.a.", ["/ˌtiːbiːˈeɪ/"]),
                ("losin’", ["/ˈluːzɪn/"]),
                ("'cause", ["/kəz/"]),
                ("'Murica", ["/ˈmɛɹɪkə/"]),
                ("Hawaiʻian", ["/həˈwaɪən/"]),
                ("Dungeons & Dragons", ["/ˈdʌndʒənz ænd ˈdɹæɡənz/"]),
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

            legacy_rejections = CLEANER.legacy_rejection_paths(output)
            legacy_rejections[0].parent.mkdir(parents=True, exist_ok=True)
            for legacy_path in legacy_rejections:
                legacy_path.write_text("old JSONL\n", encoding="utf-8")

            report = CLEANER.clean_wordlist(source, output, "en")
            parsed = {}
            for line in output.read_text(encoding="utf-8").splitlines():
                word, encoded = line.split("\t", 1)
                parsed[word] = json.loads(encoded)
            self.assertIn("-casting", parsed)
            self.assertIn("anti-", parsed)
            self.assertNotIn("♥-lichen", parsed)
            self.assertIn("mother-in-law", parsed)
            self.assertIn("state-of-the-art", parsed)
            self.assertIn("7'nci", parsed)
            self.assertEqual(parsed["can't"], ["/kænt/", "/kɑnt/"])
            self.assertIn("CO2", parsed)
            self.assertIn("software", parsed)
            self.assertIn("Word2026", parsed)
            self.assertIn("Victory Day", parsed)
            self.assertIn("t.b.a.", parsed)
            self.assertIn("losin'", parsed)
            self.assertIn("'cause", parsed)
            self.assertIn("'Murica", parsed)
            self.assertIn("Hawaiʻian", parsed)
            self.assertIn("Dungeons & Dragons", parsed)
            self.assertEqual(
                parsed["variants"],
                ["/ˈvɛəriənts/", "/vɛərənts/", "/vɛəriənts/"],
            )
            self.assertEqual(report["policy_version"], "rhyme-cleanup-v7")
            self.assertEqual(report["counts"]["eligible_words"], 18)
            self.assertEqual(report["counts"]["rejected_words"], 1)

            paths = CLEANER.output_paths(output)
            self.assertEqual(paths["wordlist"], output)
            self.assertTrue(
                all(
                    path.parent == root / "reports"
                    for name, path in paths.items()
                    if name != "wordlist"
                )
            )
            rejected_ipa_text = paths["rejected"].read_text(encoding="utf-8")
            rejects = json.loads(rejected_ipa_text)
            self.assertEqual(rejects["policy_version"], "rhyme-cleanup-v7")
            ipa_groups = {
                group["reason"]: group for group in rejects["groups"]
            }
            self.assertEqual(
                set(ipa_groups),
                {
                    "disallowed_headword_characters",
                    "incomplete_pronunciation",
                },
            )
            self.assertEqual(
                ipa_groups["incomplete_pronunciation"]["ipas"],
                ["/bad…/"],
            )
            self.assertTrue(
                all(
                    len(group["ipas"]) == len(group["entries"])
                    for group in rejects["groups"]
                )
            )
            self.assertIn('\n        "/bad…/"', rejected_ipa_text)
            rejected_word_text = paths["rejected_words"].read_text(encoding="utf-8")
            rejected_words = json.loads(rejected_word_text)
            self.assertEqual(rejected_words["policy_version"], "rhyme-cleanup-v7")
            self.assertEqual(
                rejected_words["groups"],
                [
                    {
                        "reason": "disallowed_headword_characters",
                        "words": ["♥-lichen"],
                    },
                ],
            )
            self.assertIn('\n        "♥-lichen"', rejected_word_text)
            self.assertTrue(all(not path.exists() for path in legacy_rejections))
            word_changes = [
                json.loads(line)
                for line in paths["word_changes"]
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                {row["original_word"] for row in word_changes},
                {"can’t", "CO₂", "soft\N{SOFT HYPHEN}ware", "losin’"},
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
            self.assertFalse(Path(f"{output}.rows.part").exists())

    def test_failure_preserves_all_previous_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "bad.txt"
            output = root / "eligible.txt"
            source.write_text('ok\t["/oʊˈkeɪ/"]\nbroken\tnot-json\n', encoding="utf-8")
            paths = CLEANER.output_paths(output)
            legacy_rejections = CLEANER.legacy_rejection_paths(output)
            for path in paths.values():
                path.parent.mkdir(parents=True, exist_ok=True)
            for path in paths.values():
                path.write_text("previous\n", encoding="utf-8")
            for legacy_path in legacy_rejections:
                legacy_path.write_text("previous JSONL\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                CLEANER.clean_wordlist(source, output, "en")
            for path in paths.values():
                self.assertEqual(path.read_text(encoding="utf-8"), "previous\n")
                self.assertFalse(Path(f"{path}.part").exists())
            for legacy_path in legacy_rejections:
                self.assertEqual(
                    legacy_path.read_text(encoding="utf-8"),
                    "previous JSONL\n",
                )


if __name__ == "__main__":
    unittest.main()

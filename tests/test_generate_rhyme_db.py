import importlib.util
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
GENERATOR = PROJECT_DIR / "scripts" / "generate_rhyme_db.py"
FIXTURES = PROJECT_DIR / "tests" / "fixtures"
SPEC = importlib.util.spec_from_file_location("generate_rhyme_db", GENERATOR)
assert SPEC is not None and SPEC.loader is not None
RHYME_DB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RHYME_DB)


class PhonemeTokenizationTest(unittest.TestCase):
    def test_english_affricate_long_vowel_and_diacritic(self):
        self.assertEqual(
            RHYME_DB.tokenize_ipa("[ˈtʃɝːtʃ]", "en"),
            ["ˈ", "tʃ", "ɝː", "tʃ"],
        )
        self.assertEqual(
            RHYME_DB.tokenize_ipa("/ˈkʰæt̚/", "en"),
            ["ˈ", "kʰ", "æ", "t̚"],
        )

    def test_german_tied_affricate_and_nonsyllabic_diacritic(self):
        self.assertEqual(
            RHYME_DB.tokenize_ipa("/ˈkat͡sə/", "de"),
            ["ˈ", "k", "a", "t͡s", "ə"],
        )
        self.assertEqual(
            RHYME_DB.tokenize_ipa("/tyːɐ̯/", "de"),
            ["t", "yː", "ɐ̯"],
        )

    def test_turkish_affricate_and_long_vowel(self):
        self.assertEqual(
            RHYME_DB.tokenize_ipa("/t͡ʃoˈd͡ʒuːk/", "tr"),
            ["t͡ʃ", "o", "ˈ", "d͡ʒ", "uː", "k"],
        )
        self.assertEqual(RHYME_DB.tokenize_ipa("/áː/", "tr"), ["áː"])

    def test_prefix_and_detached_modifiers_and_boundaries(self):
        self.assertEqual(
            RHYME_DB.tokenize_ipa("/ʃe.ɾi.ˈˤat/", "tr"),
            ["ʃ", "e", "ɾ", "i", "ˈ", "ˤa", "t"],
        )
        self.assertEqual(
            RHYME_DB.tokenize_ipa("[ˈkɪtn ̩]", "en"),
            ["ˈ", "k", "ɪ", "t", "n̩"],
        )
        self.assertEqual(
            RHYME_DB.tokenize_ipa("[kɪnt⁀ʊnt ‖ ↗a]", "de"),
            ["k", "ɪ", "n", "t", "ʊ", "n", "t", "↗", "a"],
        )
        self.assertEqual(
            RHYME_DB.tokenize_ipa("⫽tai̯²⁴⁻²¹ p⁽ʲ⁾il↗︎⫽", "en"),
            ["t", "a", "i̯", "²", "⁴", "⁻", "²", "¹", "pʲ", "i", "l", "↗"],
        )

    def test_unknown_clusters_are_preserved_and_counted_per_language(self):
        for lang_code in RHYME_DB.LANGUAGES:
            with self.subTest(lang_code=lang_code):
                unknown = Counter()
                tokens = RHYME_DB.tokenize_ipa("/ˈa☃̃/", lang_code, unknown)
                self.assertEqual(tokens, ["ˈ", "a", "☃̃"])
                self.assertEqual(unknown, Counter({"☃̃": 1}))


class DerivedValueTest(unittest.TestCase):
    def values(self, ipa, lang_code):
        tokens = RHYME_DB.tokenize_ipa(ipa, lang_code)
        return RHYME_DB.derived_values(tokens, lang_code)

    def test_complete_ipa_tokens_and_vowels_are_reversed(self):
        examples = {
            "en": ("/ˈkæt/", ("tækˈ", "æ")),
            "de": ("/ˈhaʊs/", ("saʊhˈ", "aʊ")),
            "tr": ("/biˈlec/", ("celˈib", "e i")),
        }
        for lang_code, (ipa, expected) in examples.items():
            with self.subTest(lang_code=lang_code):
                self.assertEqual(self.values(ipa, lang_code), expected)

    def test_stress_markers_remain_available_in_reversed_ipa(self):
        ipa_reversed, _ = self.values("/ˈfoʊtoʊˈgræf/", "en")
        self.assertEqual(ipa_reversed, "færgˈoʊtoʊfˈ")
        self.assertFalse(any(character.isspace() for character in ipa_reversed))

    def test_pure_assonance_matches_without_matching_full_ipa(self):
        examples = {
            "en": ("/ˈsiːd/", "/ˈfiːl/"),
            "de": ("/ˈmiːtə/", "/ˈbiːnə/"),
            "tr": ("/ciˈtap/", "/fiˈdan/"),
        }
        for lang_code, pair in examples.items():
            with self.subTest(lang_code=lang_code):
                first = self.values(pair[0], lang_code)
                second = self.values(pair[1], lang_code)
                self.assertEqual(first[1], second[1])
                self.assertNotEqual(first[0], second[0])


class GenerateRhymeDatabaseTest(unittest.TestCase):
    def test_each_language_fixture_builds_expected_schema_and_queries(self):
        suffix_pairs = {
            "en": ("cat", "bat"),
            "de": ("Haus", "Maus"),
            "tr": ("bilek", "çilek"),
        }
        expected_rows = {"en": 13, "de": 12, "tr": 12}
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for lang_code, (word, partner) in suffix_pairs.items():
                with self.subTest(lang_code=lang_code):
                    output = temp_path / f"{lang_code}_fixture-release.db"
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(GENERATOR),
                            str(FIXTURES / f"wordlist_{lang_code}_ipa.txt"),
                            str(output),
                            "--lang-code",
                            lang_code,
                            "--release-version",
                            "fixture-release",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self.assertIn(f"Total rows           : {expected_rows[lang_code]}", result.stdout)
                    connection = sqlite3.connect(output)
                    try:
                        columns = [
                            row[1]
                            for row in connection.execute("PRAGMA table_info(dictionary)")
                        ]
                        self.assertEqual(
                            columns,
                            [
                                "word",
                                "ipa",
                                "ipa_reversed",
                                "assonance_reversed",
                            ],
                        )
                        table_sql = connection.execute(
                            "SELECT sql FROM sqlite_master WHERE type='table' AND name='dictionary'"
                        ).fetchone()[0]
                        self.assertIn("WITHOUT ROWID", table_sql.upper())
                        indexes = {
                            row[0]
                            for row in connection.execute(
                                "SELECT name FROM sqlite_master WHERE type='index'"
                            )
                        }
                        self.assertTrue(
                            {
                                "idx_ipa_reversed",
                                "idx_assonance_reversed",
                            }.issubset(indexes)
                        )
                        self.assertNotIn("idx_rhyme_key_reversed", indexes)
                        connection.execute("PRAGMA case_sensitive_like = ON")
                        reversed_ipas = [
                            row[0]
                            for row in connection.execute(
                                "SELECT ipa_reversed FROM dictionary "
                                "WHERE word IN (?, ?) ORDER BY word",
                                (word, partner),
                            )
                        ]
                        self.assertEqual(len(reversed_ipas), 2)
                        shared_prefix = []
                        for characters in zip(*reversed_ipas):
                            if len(set(characters)) != 1:
                                break
                            shared_prefix.append(characters[0])
                        self.assertTrue(shared_prefix)
                        query_pattern = f"{''.join(shared_prefix)}%"
                        plan = " ".join(
                            str(value)
                            for value in connection.execute(
                                "EXPLAIN QUERY PLAN SELECT word FROM dictionary "
                                "WHERE ipa_reversed LIKE ?",
                                (query_pattern,),
                            ).fetchone()
                        )
                        self.assertIn("idx_ipa_reversed", plan)
                        self.assertEqual(
                            connection.execute("SELECT COUNT(*) FROM dictionary").fetchone()[0],
                            expected_rows[lang_code],
                        )
                        matches = {
                            row[0]
                            for row in connection.execute(
                                "SELECT word FROM dictionary "
                                "WHERE ipa_reversed LIKE ?",
                                (query_pattern,),
                            )
                        }
                        self.assertIn(word, matches)
                        self.assertIn(partner, matches)
                    finally:
                        connection.close()

    def test_multiple_pronunciations_become_multiple_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "en_fixture-release.db"
            RHYME_DB.build_database(
                FIXTURES / "wordlist_en_ipa.txt",
                output,
                "en",
                "fixture-release",
            )
            connection = sqlite3.connect(output)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM dictionary WHERE word = 'writer'"
                    ).fetchone()[0],
                    2,
                )
            finally:
                connection.close()

    def test_failed_build_preserves_existing_database_and_removes_part(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "bad.txt"
            source.write_text('valid\t["/ˈvælɪd/"]\nbroken\tnot-json\n', encoding="utf-8")
            output = temp_path / "en_fixture-release.db"
            output.write_bytes(b"previous complete database")
            with self.assertRaises(ValueError):
                RHYME_DB.build_database(source, output, "en", "fixture-release")
            self.assertEqual(output.read_bytes(), b"previous complete database")
            self.assertFalse(Path(f"{output}.part").exists())

    def test_unknown_ipa_rejects_build_and_preserves_previous_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "unknown.txt"
            source.write_text('valid\t["/ˈvælɪd/"]\nunknown\t["/ˈa☃/"]\n', encoding="utf-8")
            output = temp_path / "en_fixture-release.db"
            output.write_bytes(b"previous complete database")

            with self.assertRaisesRegex(ValueError, "unrecognized IPA tokens.*☃"):
                RHYME_DB.build_database(source, output, "en", "fixture-release")

            self.assertEqual(output.read_bytes(), b"previous complete database")
            self.assertFalse(Path(f"{output}.part").exists())

    def test_output_filename_must_include_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "must include Kaikki release"):
                RHYME_DB.build_database(
                    FIXTURES / "wordlist_en_ipa.txt",
                    Path(temp_dir) / "en.db",
                    "en",
                    "fixture-release",
                )


if __name__ == "__main__":
    unittest.main()

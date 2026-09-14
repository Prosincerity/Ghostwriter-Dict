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

    def test_unknown_clusters_are_preserved_and_counted_per_language(self):
        for lang_code in RHYME_DB.LANGUAGES:
            with self.subTest(lang_code=lang_code):
                unknown = Counter()
                tokens = RHYME_DB.tokenize_ipa("/ˈa☃̃/", lang_code, unknown)
                self.assertEqual(tokens, ["ˈ", "a", "☃̃"])
                self.assertEqual(unknown, Counter({"☃̃": 1}))


class DerivedKeyTest(unittest.TestCase):
    def key(self, ipa, lang_code):
        tokens = RHYME_DB.tokenize_ipa(ipa, lang_code)
        return RHYME_DB.derived_values(tokens, lang_code)

    def test_perfect_rhymes_match_and_non_rhymes_do_not(self):
        examples = {
            "en": (("/ˈkæt/", "/ˈbæt/"), ("/ˈkæt/", "/ˈkɪt/")),
            "de": (("/ˈhaʊs/", "/ˈmaʊs/"), ("/ˈhaʊs/", "/ˈmaʊt/")),
            "tr": (("/biˈlec/", "/tʃiˈlec/"), ("/biˈlec/", "/biˈlen/")),
        }
        for lang_code, (rhyming, non_rhyming) in examples.items():
            with self.subTest(lang_code=lang_code):
                self.assertEqual(
                    self.key(rhyming[0], lang_code)[1],
                    self.key(rhyming[1], lang_code)[1],
                )
                self.assertNotEqual(
                    self.key(non_rhyming[0], lang_code)[1],
                    self.key(non_rhyming[1], lang_code)[1],
                )

    def test_rhyme_key_uses_last_primary_stress_not_a_fixed_tail(self):
        first = self.key("/ˈkætəɹɪŋ/", "en")[1]
        second = self.key("/kəˈtɛɹɪŋ/", "en")[1]
        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith("æ"))
        self.assertTrue(second.endswith("ɛ"))
        # A naive three-phoneme tail would call these a match.
        self.assertTrue(first.startswith("ŋ ɪ ɹ"))
        self.assertTrue(second.startswith("ŋ ɪ ɹ"))

    def test_no_stress_falls_back_to_last_vowel(self):
        _, rhyme_key, _, fallback = self.key("/kæt/", "en")
        self.assertEqual(rhyme_key, "t æ")
        self.assertTrue(fallback)

    def test_last_primary_stress_wins(self):
        _, rhyme_key, _, fallback = self.key("/ˈfoʊtoʊˈgræf/", "en")
        self.assertEqual(rhyme_key, "f æ")
        self.assertFalse(fallback)

    def test_pure_assonance_matches_without_matching_rhyme(self):
        examples = {
            "en": ("/ˈsiːd/", "/ˈfiːl/"),
            "de": ("/ˈmiːtə/", "/ˈbiːnə/"),
            "tr": ("/ciˈtap/", "/fiˈdan/"),
        }
        for lang_code, pair in examples.items():
            with self.subTest(lang_code=lang_code):
                first = self.key(pair[0], lang_code)
                second = self.key(pair[1], lang_code)
                self.assertEqual(first[2], second[2])
                self.assertNotEqual(first[1], second[1])

    def test_rhyme_and_assonance_columns_are_independent_per_language(self):
        # Each pair rhymes, but a pre-stress vowel makes the complete vowel
        # skeletons different.
        examples = {
            "en": ("/ˈstoʊn/", "/əˈloʊn/"),
            "de": ("/ˈliːbə/", "/bəˈliːbə/"),
            "tr": ("/beˈbec/", "/celeˈbec/"),
        }
        for lang_code, pair in examples.items():
            with self.subTest(lang_code=lang_code):
                first = self.key(pair[0], lang_code)
                second = self.key(pair[1], lang_code)
                self.assertEqual(first[1], second[1])
                self.assertNotEqual(first[2], second[2])


class GenerateRhymeDatabaseTest(unittest.TestCase):
    def test_each_language_fixture_builds_expected_schema_and_queries(self):
        rhyme_pairs = {
            "en": ("cat", "bat"),
            "de": ("Haus", "Maus"),
            "tr": ("bilek", "çilek"),
        }
        expected_rows = {"en": 13, "de": 12, "tr": 12}
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for lang_code, (word, partner) in rhyme_pairs.items():
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
                                "rhyme_key_reversed",
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
                                "idx_rhyme_key_reversed",
                                "idx_assonance_reversed",
                            }.issubset(indexes)
                        )
                        connection.execute("PRAGMA case_sensitive_like = ON")
                        plan = " ".join(
                            str(value)
                            for value in connection.execute(
                                "EXPLAIN QUERY PLAN SELECT word FROM dictionary "
                                "WHERE rhyme_key_reversed LIKE ?",
                                ("t%" if lang_code == "en" else "s%",),
                            ).fetchone()
                        )
                        self.assertIn("idx_rhyme_key_reversed", plan)
                        self.assertEqual(
                            connection.execute("SELECT COUNT(*) FROM dictionary").fetchone()[0],
                            expected_rows[lang_code],
                        )
                        rhyme_key = connection.execute(
                            "SELECT rhyme_key_reversed FROM dictionary WHERE word = ? LIMIT 1",
                            (word,),
                        ).fetchone()[0]
                        matches = {
                            row[0]
                            for row in connection.execute(
                                "SELECT word FROM dictionary WHERE rhyme_key_reversed = ?",
                                (rhyme_key,),
                            )
                        }
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

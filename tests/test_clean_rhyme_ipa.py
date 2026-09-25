import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from generate_rhyme_db import build_database

SPEC = importlib.util.spec_from_file_location("clean_rhyme_ipa", SCRIPTS / "clean_rhyme_ipa.py")
assert SPEC is not None and SPEC.loader is not None
IPA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IPA)


def rows(path):
    return {
        word: json.loads(encoded)
        for word, encoded in (line.split("\t", 1) for line in path.read_text(encoding="utf-8").splitlines())
    }


class PhonemeComparisonTest(unittest.TestCase):
    def test_stressed_syllabic_consonant_has_a_rhyme_tail(self):
        self.assertEqual(IPA.stressed_rhyme_tail("/ˈn̩t/", "en"), ["n̩", "t"])

    def test_compares_complete_tokens_and_reports_rhyme_tail(self):
        same = IPA.compare_pronunciations("/ˈkæt/", "ˈkæt", "en")
        self.assertEqual(same["phoneme_edits"], 0)
        self.assertEqual(same["phoneme_distance_ratio"], 0.0)
        self.assertEqual(same["wiktionary_rhyme_tail"], ["æ", "t"])
        self.assertTrue(same["rhyme_tail_matches"])

        changed = IPA.compare_pronunciations("/ˈkæt/", "/ˈkɑt/", "en")
        self.assertEqual(changed["phoneme_edits"], 1)
        self.assertAlmostEqual(changed["phoneme_distance_ratio"], 1 / 3)
        self.assertFalse(changed["rhyme_tail_matches"])

    def test_unknown_tokens_are_not_silently_dropped(self):
        with self.assertRaisesRegex(ValueError, "unrecognized IPA tokens"):
            IPA.compare_pronunciations("/ˈk☃t/", "/ˈkæt/", "en")


class IpaRegenerationTest(unittest.TestCase):
    def test_partial_ipa_regenerates_into_espeak_database(self):
        examples = {
            "de": {
                "übermäßig": ["/-ˌmeːsɪç/", "/-ˌmɛːsɪk/"],
                "unzähmbarkeit": ["/ʊnˈt͡seːm-/"],
                "unzüchtigen": ["/-ˌt͡sʏçtɪɡŋ̩/"],
                "jauserl": ["[ˈjɑɔ̯-]"],
                "jausnen": ["[ˈjɑɔ̯s-]"],
                "funktionsweise": ["[-ˌvaɛ̯-]"],
                "funktionär": ["/-ˈneːɐ̯/"],
                "furche": ["[ˈfʊɐ̯-]"],
                "feldzug": ["/-ˌt͡sʊx/"],
                "fenstersturz": ["[ˈfɛns.tɐ-]"],
                "ferien": ["[ˈfɛɐ̯-]"],
            },
            "en": {
                "catnip": ["/ˈkæt-/"],
                "catalog": ["/-ˌkæt/"],
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for lang_code, words in examples.items():
                with self.subTest(lang_code=lang_code):
                    language_dir = root / lang_code
                    language_dir.mkdir()
                    wiki_input = language_dir / "wiki.txt"
                    espeak_input = language_dir / "espeak.txt"
                    wiki_output = language_dir / "wiki_eligible.txt"
                    espeak_output = language_dir / "espeak_eligible.txt"
                    wiki_input.write_text("".join(
                        f"{word}\t{json.dumps(ipas, ensure_ascii=False)}\n"
                        for word, ipas in words.items()
                    ), encoding="utf-8")
                    espeak_input.write_text("", encoding="utf-8")
                    generated = "ˈhaːmɐ" if lang_code == "de" else "ˈkæt"
                    with mock.patch.object(IPA, "call_espeak", return_value=[generated] * len(words)) as call:
                        report = IPA.clean_ipa_wordlists(
                            wiki_input, espeak_input, wiki_output, espeak_output,
                            lang_code, "fake-espeak",
                        )
                    call.assert_called_once_with("fake-espeak", lang_code, list(words))
                    self.assertEqual(rows(wiki_output), {})
                    self.assertEqual(rows(espeak_output), {
                        word: [generated] for word in words
                    })
                    self.assertEqual(report["counts"]["regenerated_words"], len(words))
                    rejected = json.loads(IPA.output_paths(wiki_output, espeak_output)["rejected"]
                                          .read_text(encoding="utf-8"))
                    self.assertEqual(
                        {entry["ipa"] for group in rejected["groups"] for entry in group["entries"]},
                        {ipa for ipas in words.values() for ipa in ipas},
                    )
                    self.assertEqual({group["reason"] for group in rejected["groups"]},
                                     {"incomplete_pronunciation"})
                    for source, wordlist in (("kaikki", wiki_output), ("espeak", espeak_output)):
                        database = language_dir / f"{lang_code}_{source}_kaikki-v20260925.db"
                        build_database(wordlist, database, lang_code, "kaikki-v20260925")
                        connection = sqlite3.connect(database)
                        try:
                            database_rows = connection.execute(
                                "SELECT word, ipa FROM dictionary ORDER BY word"
                            ).fetchall()
                        finally:
                            connection.close()
                        expected = [] if source == "kaikki" else [
                            (word, generated) for word in sorted(words)
                        ]
                        self.assertEqual(database_rows, expected)

    def test_keeps_valid_siblings_and_routes_only_replacements_to_espeak(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wiki_input = root / "wiki_words.txt"
            espeak_input = root / "espeak_words.txt"
            wiki_output = root / "wiki_eligible.txt"
            espeak_output = root / "espeak_eligible.txt"
            wiki_input.write_text(
                'cat\t["/ˈkæt/","/kæt/","/bad…/","/ˈkæt-/"]\n'
                'dog\t["/ˈdɔɡ/"]\n'
                'wrong\t["/bad…/"]\n', encoding="utf-8",
            )
            espeak_input.write_text('other\t["ˈʌðɚ"]\n', encoding="utf-8")
            with mock.patch.object(IPA, "call_espeak", return_value=["ˈkæt", "ˈdɔɡ", "ˈɹɔŋ"]) as call:
                report = IPA.clean_ipa_wordlists(
                    wiki_input, espeak_input, wiki_output, espeak_output,
                    "en", "fake-espeak", batch_size=10,
                )
            call.assert_called_once_with("fake-espeak", "en", ["cat", "dog", "wrong"])
            self.assertEqual(rows(wiki_output), {"cat": ["/ˈkæt/"], "dog": ["/ˈdɔɡ/"]})
            self.assertEqual(rows(espeak_output), {
                "other": ["ˈʌðɚ"], "cat": ["ˈkæt"], "wrong": ["ˈɹɔŋ"],
            })
            self.assertEqual(report["counts"]["regeneration_requested_words"], 2)
            self.assertEqual(report["counts"]["regenerated_words"], 2)
            self.assertEqual(report["counts"]["compared_variants"], 2)
            paths = IPA.output_paths(wiki_output, espeak_output)
            rejected = json.loads(paths["rejected"].read_text(encoding="utf-8"))
            self.assertEqual(
                {group["reason"] for group in rejected["groups"]},
                {"incomplete_pronunciation", "missing_stress_mark"},
            )
            changes = [json.loads(line) for line in paths["changes"].read_text(encoding="utf-8").splitlines()]
            regenerated = [row for row in changes if row.get("action") == "regenerated_with_espeak"]
            self.assertEqual({row["word"] for row in regenerated}, {"cat", "wrong"})
            self.assertTrue(all(row["policy_version"] == IPA.POLICY_VERSION for row in regenerated))
            self.assertEqual(list(root.rglob("*.part")), [])

    def test_valid_wiktionary_ipa_is_compared_without_regeneration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wiki_input = root / "wiki.txt"
            espeak_input = root / "espeak.txt"
            wiki_input.write_text('cat\t["/ˈkæt/"]\n', encoding="utf-8")
            espeak_input.write_text('other\t["ˈʌðɚ"]\n', encoding="utf-8")
            with mock.patch.object(IPA, "call_espeak", return_value=["ˈkæt"]) as call:
                report = IPA.clean_ipa_wordlists(
                    wiki_input, espeak_input, root / "wiki_out.txt",
                    root / "espeak_out.txt", "en", "fake",
                )
            call.assert_called_once_with("fake", "en", ["cat"])
            self.assertEqual(report["counts"]["regeneration_requested_words"], 0)
            self.assertEqual(report["counts"]["compared_variants"], 1)
            self.assertEqual(rows(root / "wiki_out.txt"), {"cat": ["/ˈkæt/"]})
            self.assertEqual(rows(root / "espeak_out.txt"), {"other": ["ˈʌðɚ"]})
            self.assertEqual(
                IPA.output_paths(root / "wiki_out.txt", root / "espeak_out.txt")["comparisons"]
                .read_text(encoding="utf-8"), "",
            )

    def test_reports_only_extreme_or_distance_above_half(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wiki_input = root / "wiki.txt"
            espeak_input = root / "espeak.txt"
            wiki_output = root / "wiki_out.txt"
            espeak_output = root / "espeak_out.txt"
            wiki_input.write_text(
                'boundary\t["/ˈkæts/"]\n'
                'large\t["/ˈkæbɪn/"]\n'
                'extreme\t["/ˈkætsbɪnd/"]\n', encoding="utf-8",
            )
            espeak_input.write_text("", encoding="utf-8")
            self.assertEqual(
                IPA.compare_pronunciations("/ˈkæts/", "ˈpɑts", "en")["phoneme_distance_ratio"],
                0.5,
            )
            with mock.patch.object(IPA, "call_espeak", return_value=[
                "ˈpɑts", "ˈtudɪn", "ˈpɑdzbɪnd",
            ]):
                report = IPA.clean_ipa_wordlists(
                    wiki_input, espeak_input, wiki_output, espeak_output,
                    "en", "fake", extreme_distance=0.5,
                )
            comparisons = [json.loads(line) for line in
                           IPA.output_paths(wiki_output, espeak_output)["comparisons"]
                           .read_text(encoding="utf-8").splitlines()]
            self.assertEqual(report["counts"]["compared_variants"], 3)
            self.assertEqual(report["counts"]["reported_comparison_variants"], 2)
            self.assertEqual([row["word"] for row in comparisons], ["large", "extreme"])
            self.assertGreater(comparisons[0]["phoneme_distance_ratio"], 0.5)
            self.assertFalse(comparisons[0]["extreme_mismatch"])
            self.assertEqual(comparisons[1]["phoneme_distance_ratio"], 0.5)
            self.assertTrue(comparisons[1]["extreme_mismatch"])

    def test_extreme_comparison_reports_first_and_opt_in_moves_only_bad_variant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wiki_input = root / "wiki.txt"
            espeak_input = root / "espeak.txt"
            wiki_output = root / "wiki_out.txt"
            espeak_output = root / "espeak_out.txt"
            wiki_input.write_text(
                'catapult\t["/ˈkætəpʌlt/","/ˈʃuːɡɛlmə/"]\n',
                encoding="utf-8",
            )
            espeak_input.write_text("", encoding="utf-8")
            with mock.patch.object(IPA, "call_espeak", return_value=["ˈʃuːɡɛlmə"]) as call:
                report = IPA.clean_ipa_wordlists(
                    wiki_input, espeak_input, wiki_output, espeak_output,
                    "en", "fake",
                )
            call.assert_called_once_with("fake", "en", ["catapult"])
            self.assertEqual(report["comparison_mode"], "report_only")
            self.assertEqual(report["counts"]["extreme_review_variants"], 1)
            self.assertEqual(rows(wiki_output)["catapult"],
                             ["/ˈkætəpʌlt/", "/ˈʃuːɡɛlmə/"])
            self.assertEqual(rows(espeak_output), {})
            comparisons = [json.loads(line) for line in
                           IPA.output_paths(wiki_output, espeak_output)["comparisons"]
                           .read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(comparisons), 1)
            self.assertEqual([row["extreme_mismatch"] for row in comparisons], [True])
            self.assertTrue(all(row["action"] == "kept_wiktionary" for row in comparisons))

            with mock.patch.object(IPA, "call_espeak", return_value=["ˈʃuːɡɛlmə"]):
                report = IPA.clean_ipa_wordlists(
                    wiki_input, espeak_input, wiki_output, espeak_output,
                    "en", "fake", replace_extreme_mismatches=True,
                )
            self.assertEqual(report["counts"]["extreme_replaced_variants"], 1)
            self.assertEqual(rows(wiki_output), {"catapult": ["/ˈʃuːɡɛlmə/"]})
            self.assertEqual(rows(espeak_output), {"catapult": ["ˈʃuːɡɛlmə"]})

    def test_espeak_failure_preserves_previous_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wiki_input = root / "wiki.txt"
            espeak_input = root / "espeak.txt"
            wiki_output = root / "wiki_out.txt"
            espeak_output = root / "espeak_out.txt"
            wiki_input.write_text('cat\t["/kæt/"]\n', encoding="utf-8")
            espeak_input.write_text('other\t["ˈʌðɚ"]\n', encoding="utf-8")
            wiki_output.write_text("previous wiki\n", encoding="utf-8")
            espeak_output.write_text("previous espeak\n", encoding="utf-8")
            with mock.patch.object(IPA, "call_espeak", side_effect=RuntimeError("failed")):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    IPA.clean_ipa_wordlists(
                        wiki_input, espeak_input, wiki_output, espeak_output,
                        "en", "fake",
                    )
            self.assertEqual(wiki_output.read_text(encoding="utf-8"), "previous wiki\n")
            self.assertEqual(espeak_output.read_text(encoding="utf-8"), "previous espeak\n")
            self.assertEqual(list(root.rglob("*.part")), [])


if __name__ == "__main__":
    unittest.main()

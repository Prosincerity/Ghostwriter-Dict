import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import clean_rhyme_ipa as IPA  # noqa: E402
import clean_rhyme_words as WORDS  # noqa: E402


def read_rows(path):
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        word, encoded = line.split("\t", 1)
        rows[word] = json.loads(encoded)
    return rows


class CleanupPipelineTest(unittest.TestCase):
    def test_cli_writes_outputs_and_prints_policy_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.txt"
            wiki_words = root / "wiki_words.txt"
            espeak_words = root / "espeak_words.txt"
            wiki_output = root / "wiki_eligible.txt"
            espeak_output = root / "espeak_eligible.txt"
            source.write_text('word\t["/ˈwɝd/"]\n', encoding="utf-8")
            espeak_words.write_text("", encoding="utf-8")
            word_result = subprocess.run(
                [sys.executable, str(SCRIPTS / "clean_rhyme_words.py"),
                 str(source), str(wiki_words), "--lang-code", "en"],
                check=True, capture_output=True, text=True,
            )
            ipa_result = subprocess.run(
                [sys.executable, str(SCRIPTS / "clean_rhyme_ipa.py"),
                 str(wiki_words), str(espeak_words), str(wiki_output),
                 str(espeak_output), "--lang-code", "en", "--espeak", "/bin/true"],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(wiki_output.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))
            self.assertEqual(espeak_output.read_text(encoding="utf-8"), "")
            self.assertIn("rhyme-cleanup-v12", word_result.stdout)
            self.assertIn("rhyme-cleanup-v12", ipa_result.stdout)
            self.assertTrue(WORDS.output_paths(wiki_words)["report"].is_file())
            self.assertTrue(IPA.output_paths(wiki_output, espeak_output)["report"].is_file())

    def test_writes_eligible_wordlists_and_audit_sidecars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "wordlist_en_ipa.txt"
            wiki_words = root / "wiki_words.txt"
            espeak_words = root / "espeak_words.txt"
            wiki_output = root / "wordlist_en_rhyme_eligible.txt"
            espeak_output = root / "wordlist_en_espeak_rhyme_eligible.txt"
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
                ("t.b.a", ["/ˌtiːbiːˈeɪ/"]),
                ("losin’", ["/ˈluːzɪn/"]),
                ("'cause", ["/kəz/"]),
                ("'Murica", ["/ˈmɛɹɪkə/"]),
                ("Hawaiʻian", ["/həˈwaɪən/"]),
                ("Dungeons & Dragons", ["/ˈdʌndʒənz ænd ˈdɹæɡənz/"]),
                ("variants", ["/'vɛəriənts/", "/bad…/", "/vɛər(i)ənts/"]),
                ("tones", ["/toʊn˦˨/"]),
                ("$hit", ["/hɪt/"]),
                ("ü", ["/yː/"]),
                ("tw*t", ["/twɒt/"]),
            ]
            source.write_text(
                "".join(f"{word}\t{json.dumps(ipas, ensure_ascii=False, separators=(',', ':'))}\n"
                        for word, ipas in rows),
                encoding="utf-8",
            )
            espeak_words.write_text('already\t["ˈɔlɹɛdi"]\n', encoding="utf-8")
            word_report = WORDS.clean_wordlist(source, wiki_words, "en")
            with mock.patch.object(IPA, "call_espeak", side_effect=lambda executable, lang, words: ["ˈkæt"] * len(words)) as call:
                ipa_report = IPA.clean_ipa_wordlists(
                    wiki_words, espeak_words, wiki_output, espeak_output,
                    "en", "fake-espeak",
                )
            self.assertTrue(call.called)
            word_rows = read_rows(wiki_words)
            self.assertEqual(word_rows["can't"], ["/kænt/", "/kɑnt/"])
            self.assertEqual(word_rows["variants"], ["/'vɛəriənts/", "/bad…/", "/vɛər(i)ənts/"])
            wiki_rows = read_rows(wiki_output)
            espeak_rows = read_rows(espeak_output)
            for word in ("-casting", "anti-", "♥-lichen", "Victory Day", "t.b.a.",
                         "Dungeons & Dragons", "$hit", "ü", "tw*t"):
                self.assertNotIn(word, word_rows)
            for word in ("mother-in-law", "state-of-the-art", "7'nci", "CO2",
                         "software", "Word2026", "t.b.a", "losin'", "'Murica",
                         "Hawaiʻian"):
                self.assertIn(word, wiki_rows)
            self.assertNotIn("can't", wiki_rows)
            self.assertNotIn("'cause", wiki_rows)
            self.assertNotIn("tones", wiki_rows)
            self.assertEqual(wiki_rows["variants"], ["/ˈvɛəriənts/"])
            self.assertIn("variants", espeak_rows)
            self.assertIn("can't", espeak_rows)
            self.assertIn("'cause", espeak_rows)
            self.assertIn("tones", espeak_rows)
            self.assertIn("already", espeak_rows)
            self.assertEqual(word_report["counts"]["rejected_words"], 9)
            self.assertEqual(word_report["policy_version"], "rhyme-cleanup-v12")
            self.assertEqual(ipa_report["policy_version"], "rhyme-cleanup-v12")

            word_paths = WORDS.output_paths(wiki_words)
            rejected_words = json.loads(word_paths["rejected_words"].read_text(encoding="utf-8"))
            groups = {group["reason"]: group for group in rejected_words["groups"]}
            self.assertEqual(set(groups), {
                "disallowed_headword_characters", "leading_special_character",
                "single_letter_headword", "trailing_dash_or_dot",
            })
            self.assertEqual(groups["disallowed_headword_characters"]["words"], [
                "♥-lichen", "Victory Day", "Dungeons & Dragons", "$hit", "tw*t",
            ])
            self.assertEqual(groups["leading_special_character"]["words"], ["-casting"])
            self.assertEqual(groups["single_letter_headword"]["words"], ["ü"])
            self.assertEqual(groups["trailing_dash_or_dot"]["words"], ["anti-", "t.b.a."])
            self.assertEqual(
                {json.loads(line)["original_word"] for line in
                 word_paths["word_changes"].read_text(encoding="utf-8").splitlines()},
                {"can’t", "CO₂", "soft\N{SOFT HYPHEN}ware", "losin’"},
            )

            ipa_paths = IPA.output_paths(wiki_output, espeak_output)
            rejected_text = ipa_paths["rejected"].read_text(encoding="utf-8")
            rejected = json.loads(rejected_text)
            ipa_groups = {group["reason"]: group for group in rejected["groups"]}
            self.assertEqual(set(ipa_groups), {"incomplete_pronunciation", "missing_stress_mark"})
            self.assertEqual(ipa_groups["incomplete_pronunciation"]["ipas"], ["/bad…/"])
            self.assertTrue(all(len(group["ipas"]) == len(group["entries"])
                                for group in rejected["groups"]))
            changes = [json.loads(line) for line in ipa_paths["changes"].read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(row.get("original_ipa") == "/'vɛəriənts/" for row in changes))
            self.assertTrue(any(row.get("action") == "regenerated_with_espeak" for row in changes))
            for paths in (word_paths, ipa_paths):
                self.assertTrue(all(not Path(f"{path}.part").exists() for path in paths.values()))
            self.assertFalse(Path(f"{wiki_words}.rows.part").exists())
            self.assertFalse(Path(f"{wiki_output}.rows.part").exists())

    def test_failure_preserves_all_previous_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "bad.txt"
            word_output = root / "words.txt"
            source.write_text('ok\t["/oʊˈkeɪ/"]\nbroken\tnot-json\n', encoding="utf-8")
            word_paths = WORDS.output_paths(word_output)
            for path in word_paths.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("previous\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                WORDS.clean_wordlist(source, word_output, "en")
            for path in word_paths.values():
                self.assertEqual(path.read_text(encoding="utf-8"), "previous\n")
                self.assertFalse(Path(f"{path}.part").exists())

            espeak_input = root / "espeak.txt"
            espeak_input.write_text('other\t["ˈʌðɚ"]\n', encoding="utf-8")
            wiki_input = root / "wiki.txt"
            wiki_input.write_text('cat\t["/kæt/"]\n', encoding="utf-8")
            wiki_output = root / "wiki_eligible.txt"
            espeak_output = root / "espeak_eligible.txt"
            ipa_paths = IPA.output_paths(wiki_output, espeak_output)
            for path in ipa_paths.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("previous\n", encoding="utf-8")
            with mock.patch.object(IPA, "call_espeak", side_effect=RuntimeError("failed")):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    IPA.clean_ipa_wordlists(
                        wiki_input, espeak_input, wiki_output, espeak_output,
                        "en", "fake-espeak",
                    )
            for path in ipa_paths.values():
                self.assertEqual(path.read_text(encoding="utf-8"), "previous\n")
                self.assertFalse(Path(f"{path}.part").exists())


if __name__ == "__main__":
    unittest.main()

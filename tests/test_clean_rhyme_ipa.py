import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("clean_rhyme_ipa", SCRIPTS / "clean_rhyme_ipa.py")
assert SPEC is not None and SPEC.loader is not None
IPA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IPA)


def rows(path):
    return {
        word: json.loads(encoded)
        for word, encoded in (line.split("\t", 1) for line in path.read_text(encoding="utf-8").splitlines())
    }


class IpaRegenerationTest(unittest.TestCase):
    def test_keeps_valid_siblings_and_routes_only_replacements_to_espeak(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wiki_input = root / "wiki_words.txt"
            espeak_input = root / "espeak_words.txt"
            wiki_output = root / "wiki_eligible.txt"
            espeak_output = root / "espeak_eligible.txt"
            wiki_input.write_text(
                'cat\t["/ˈkæt/","/kæt/","/bad…/"]\n'
                'dog\t["/ˈdɔɡ/"]\n'
                'wrong\t["/bad…/"]\n', encoding="utf-8",
            )
            espeak_input.write_text('other\t["ˈʌðɚ"]\n', encoding="utf-8")
            with mock.patch.object(IPA, "call_espeak", return_value=["ˈkæt", "ˈɹɔŋ"]) as call:
                report = IPA.clean_ipa_wordlists(
                    wiki_input, espeak_input, wiki_output, espeak_output,
                    "en", "fake-espeak", batch_size=10,
                )
            call.assert_called_once_with("fake-espeak", "en", ["cat", "wrong"])
            self.assertEqual(rows(wiki_output), {"cat": ["/ˈkæt/"], "dog": ["/ˈdɔɡ/"]})
            self.assertEqual(rows(espeak_output), {
                "other": ["ˈʌðɚ"], "cat": ["ˈkæt"], "wrong": ["ˈɹɔŋ"],
            })
            self.assertEqual(report["counts"]["regeneration_requested_words"], 2)
            self.assertEqual(report["counts"]["regenerated_words"], 2)
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

    def test_no_regeneration_when_all_wiktionary_ipa_are_valid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wiki_input = root / "wiki.txt"
            espeak_input = root / "espeak.txt"
            wiki_input.write_text('cat\t["/ˈkæt/"]\n', encoding="utf-8")
            espeak_input.write_text('other\t["ˈʌðɚ"]\n', encoding="utf-8")
            with mock.patch.object(IPA, "call_espeak") as call:
                IPA.clean_ipa_wordlists(
                    wiki_input, espeak_input, root / "wiki_out.txt",
                    root / "espeak_out.txt", "en", "fake",
                )
            call.assert_not_called()

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

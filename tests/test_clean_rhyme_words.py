import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "clean_rhyme_words.py"


class WordCleanupTest(unittest.TestCase):
    def test_cleans_words_without_changing_ipa_and_reports_rejections(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.txt"
            output = root / "cleaned.txt"
            source.write_text(
                'can’t\t["/kænt/"," /broken…/ "]\n'
                'can\'t\t["/kɑnt/","/kænt/"]\n'
                'bad word\t["/bæd/"]\n',
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, str(SCRIPT), str(source), str(output),
                 "--lang-code", "en"],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                'can\'t\t["/kænt/"," /broken…/ ","/kɑnt/"]\n',
            )
            reports = root / "reports" / "cleaning"
            changes = [json.loads(line) for line in
                       (reports / "cleaned_word_changes.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(changes[0]["original_word"], "can’t")
            self.assertEqual(changes[0]["normalized_word"], "can't")
            rejected = json.loads((reports / "cleaned_rejected_words.json").read_text(encoding="utf-8"))
            self.assertEqual(rejected["groups"][0]["words"], ["bad word"])
            self.assertEqual(
                rejected["groups"][0]["entries"][0]["details"]["invalid_characters"][0]["character"],
                " ",
            )
            report = json.loads((reports / "cleaned_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["counts"]["eligible_words"], 1)
            self.assertEqual(report["counts"]["eligible_pronunciations"], 3)

    def test_failure_preserves_previous_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "bad.txt"
            output = root / "cleaned.txt"
            source.write_text('word\t["/wɝd/"]\nbroken\tnot-json\n', encoding="utf-8")
            output.write_text("previous\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(source), str(output),
                 "--lang-code", "en"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous\n")
            self.assertEqual(list(root.rglob("*.part")), [])


if __name__ == "__main__":
    unittest.main()

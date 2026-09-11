import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
GENERATOR = PROJECT_DIR / "scripts" / "generate_espeak_ipa.py"


FAKE_ESPEAK = """#!/usr/bin/env python3
import sys

voice = sys.argv[sys.argv.index("-v") + 1]
for line in sys.stdin:
    word = line.rstrip("\\r\\n")
    if word == "rizz 😎":
        print(f" {voice}-rizz-part-one")
        print(f" {voice}-rizz-part-two")
    else:
        print(f" {voice}-{word}-ipa")
print()
"""


class GenerateEspeakIpaTest(unittest.TestCase):
    def test_generates_one_json_ipa_row_per_word_for_all_languages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outdir = Path(temp_dir)
            fake_espeak = outdir / "espeak-ng"
            fake_espeak.write_text(FAKE_ESPEAK, encoding="utf-8")
            fake_espeak.chmod(0o755)

            inputs = {
                "en": ["hello", "rizz 😎"],
                "de": ["Dings"],
                "tr": ["kanka", "çevrim içi"],
            }
            for lang_code, words in inputs.items():
                (outdir / f"wordlist_{lang_code}_noipa.txt").write_text(
                    "".join(f"{word}\n" for word in words), encoding="utf-8"
                )

            subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    "--outdir",
                    str(outdir),
                    "--espeak",
                    str(fake_espeak),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            for lang_code, words in inputs.items():
                output_path = outdir / f"wordlist_{lang_code}_espeak_ipa.txt"
                rows = output_path.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(rows), len(words))
                for row, word in zip(rows, words):
                    output_word, ipa_json = row.split("\t", maxsplit=1)
                    self.assertEqual(output_word, word)
                    expected_ipa = f"{lang_code}-{word}-ipa"
                    if word == "rizz 😎":
                        expected_ipa = "en-rizz-part-one en-rizz-part-two"
                    self.assertEqual(json.loads(ipa_json), [expected_ipa])


if __name__ == "__main__":
    unittest.main()

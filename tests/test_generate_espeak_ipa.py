import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
GENERATOR = PROJECT_DIR / "scripts" / "generate_espeak_ipa.py"


FAKE_ESPEAK = """#!/usr/bin/env python3
import os
import sys

voice = sys.argv[sys.argv.index("-v") + 1]
if os.environ.get("ESPEAK_CALL_LOG"):
    with open(os.environ["ESPEAK_CALL_LOG"], "a", encoding="utf-8") as log:
        log.write(f"{voice}\\n")
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
                language_outdir = outdir / lang_code
                language_outdir.mkdir()
                (language_outdir / f"wordlist_{lang_code}_noipa.txt").write_text(
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
                output_path = (
                    outdir / lang_code / f"wordlist_{lang_code}_espeak_ipa.txt"
                )
                rows = output_path.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(rows), len(words))
                for row, word in zip(rows, words):
                    output_word, ipa_json = row.split("\t", maxsplit=1)
                    self.assertEqual(output_word, word)
                    expected_ipa = f"{lang_code}-{word}-ipa"
                    if word == "rizz 😎":
                        expected_ipa = "en-rizz-part-one en-rizz-part-two"
                    self.assertEqual(json.loads(ipa_json), [expected_ipa])

    def test_reuses_only_complete_output_for_unchanged_download(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "download.jsonl.gz"
            archive.write_bytes(b"first download")
            Path(f"{archive}.etag").write_text('"first"\n', encoding="utf-8")
            outdir = root / "out"
            language_dir = outdir / "en"
            language_dir.mkdir(parents=True)
            noipa = language_dir / "wordlist_en_noipa.txt"
            noipa.write_text("hello\n", encoding="utf-8")
            output = language_dir / "wordlist_en_espeak_ipa.txt"
            report_path = language_dir / "reports" / "ipa_generation" / "wordlist_en_espeak_ipa_source.json"
            fake_espeak = root / "espeak-ng"
            fake_espeak.write_text(FAKE_ESPEAK, encoding="utf-8")
            fake_espeak.chmod(0o755)
            call_log = root / "calls.txt"
            environment = os.environ.copy()
            environment["ESPEAK_CALL_LOG"] = str(call_log)
            command = [sys.executable, str(GENERATOR), "--outdir", str(outdir),
                       "--lang-code", "en", "--espeak", str(fake_espeak),
                       "--source-archive", str(archive)]

            def run():
                return subprocess.run(command, check=True, capture_output=True,
                                      text=True, env=environment)

            run()
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["source_archives"][0]["file"], str(archive))
            self.assertEqual(report["source_archives"][0]["etag"], '"first"')
            self.assertEqual(report["generated_words"], 1)
            self.assertIn("generator_sha256", report)
            stage_report = language_dir / "reports" / "ipa_generation" / "report.json"
            self.assertEqual(json.loads(stage_report.read_text(encoding="utf-8"))["status"], "generated")
            self.assertEqual(call_log.read_text(encoding="utf-8").splitlines(), ["en"])

            self.assertIn("Reusing en eSpeak IPA", run().stdout)
            self.assertEqual(json.loads(stage_report.read_text(encoding="utf-8"))["status"], "reused")
            self.assertEqual(call_log.read_text(encoding="utf-8").splitlines(), ["en"])
            cached_command = command.copy()
            cached_command[cached_command.index("--espeak") + 1] = "missing-espeak"
            cached_result = subprocess.run(
                cached_command, check=True, capture_output=True, text=True,
                env=environment,
            )
            self.assertIn("Reusing en eSpeak IPA", cached_result.stdout)

            stale_report = json.loads(report_path.read_text(encoding="utf-8"))
            stale_report["generator_sha256"] = "previous-code-version"
            report_path.write_text(json.dumps(stale_report), encoding="utf-8")
            run()
            self.assertEqual(len(call_log.read_text(encoding="utf-8").splitlines()), 2)

            archive.write_bytes(b"second download")
            Path(f"{archive}.etag").write_text('"second"\n', encoding="utf-8")
            run()
            self.assertEqual(len(call_log.read_text(encoding="utf-8").splitlines()), 3)
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))
                             ["source_archives"][0]["etag"], '"second"')

            output.unlink()
            run()
            self.assertEqual(len(call_log.read_text(encoding="utf-8").splitlines()), 4)

            noipa.write_text("hello\nworld\n", encoding="utf-8")
            run()
            self.assertEqual(len(call_log.read_text(encoding="utf-8").splitlines()), 5)
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 2)

            output.write_text("damaged\n", encoding="utf-8")
            run()
            self.assertEqual(len(call_log.read_text(encoding="utf-8").splitlines()), 6)
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 2)
            self.assertFalse(Path(f"{report_path}.part").exists())


if __name__ == "__main__":
    unittest.main()

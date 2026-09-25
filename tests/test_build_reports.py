import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_reports.py"


class BuildReportsTest(unittest.TestCase):
    def test_download_and_cleanup_reports_are_grouped_by_stage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outdir = root / "out"
            archive = root / "edition.jsonl.gz"
            archive.write_bytes(b"fixture")
            subprocess.run([
                sys.executable, str(SCRIPT), "downloading", "--outdir", str(outdir),
                "--release-version", "kaikki-v20260902", "--archive", str(archive),
                "--status", "en=downloaded",
            ], check=True)
            for lang in ("en", "de", "tr"):
                report_path = outdir / lang / "reports" / "downloading" / "report.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(report["status"]["en"], "downloaded")
                self.assertEqual(report["archives"][0]["size_bytes"], 7)

            reports = outdir / "en" / "reports" / "cleaning"
            reports.mkdir()
            for stem in (
                "wordlist_en_wiktionary_words_cleaned_report.json",
                "wordlist_en_espeak_words_cleaned_report.json",
                "wordlist_en_rhyme_eligible_ipa_report.json",
            ):
                (reports / stem).write_text('{"policy_version":"rhyme-cleanup-v14"}', encoding="utf-8")
            subprocess.run([
                sys.executable, str(SCRIPT), "cleaning", "--outdir", str(outdir),
                "--release-version", "kaikki-v20260902", "--lang-code", "en",
                "--comparison-mode", "report_only",
            ], check=True)
            summary = json.loads((reports / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["comparison_mode"], "report_only")
            self.assertEqual(len(summary["step_reports"]), 3)


if __name__ == "__main__":
    unittest.main()

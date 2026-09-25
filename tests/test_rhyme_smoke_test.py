import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SMOKE_TEST = PROJECT_DIR / "tests" / "scripts" / "run_rhyme_smoke_test.py"
FIXTURE = PROJECT_DIR / "tests" / "fixtures" / "wordlist_en_ipa.txt"


class RhymeSmokeTestTest(unittest.TestCase):
    def run_smoke_test(self, input_dir: Path, output_root: Path) -> None:
        subprocess.run(
            [
                sys.executable,
                str(SMOKE_TEST),
                "--input-dir",
                str(input_dir),
                "--output-root",
                str(output_root),
                "--lang-code",
                "en",
                "--sample-size",
                "5",
                "--seed",
                "fixed-test-seed",
                "--release-version",
                "fixture-release",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_creates_deterministic_sample_database_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            (input_dir / "en").mkdir(parents=True)
            (input_dir / "en" / "wordlist_en_rhyme_eligible.txt").write_text(
                FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
            )
            first_output = temp_path / "first"
            second_output = temp_path / "second"

            self.run_smoke_test(input_dir, first_output)
            self.run_smoke_test(input_dir, second_output)

            sample_name = "wordlist_en_sample-5_fixture-release.txt"
            first_sample = first_output / "en" / "samples" / sample_name
            second_sample = second_output / "en" / "samples" / sample_name
            self.assertEqual(
                first_sample.read_text(encoding="utf-8"),
                second_sample.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                len(first_sample.read_text(encoding="utf-8").splitlines()), 5
            )

            database = (
                first_output
                / "en"
                / "databases"
                / "en_sample-5_fixture-release.db"
            )
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(DISTINCT word) FROM dictionary"
                    ).fetchone()[0],
                    5,
                )
            finally:
                connection.close()

            manifest_path = (
                first_output / "en" / "reports" / "smoke_manifest.json"
            )
            self.assertTrue(manifest_path.is_file())
            self.assertFalse((first_output / "en" / "smoke_manifest.json").exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["language"], "en")
            self.assertEqual(manifest["cleanup_policy_version"], "rhyme-cleanup-v12")
            self.assertEqual(manifest["release_version"], "fixture-release")
            self.assertEqual(manifest["sample_size"], 5)
            self.assertEqual(manifest["results"][0]["integrity"], "ok")
            self.assertNotIn(
                "shared_rhyme_key_groups", manifest["results"][0]
            )


if __name__ == "__main__":
    unittest.main()

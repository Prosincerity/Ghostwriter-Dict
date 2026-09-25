import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_DIR / "scripts" / "clean_and_build.sh"

FAKE_PYTHON = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$CLEAN_BUILD_CALL_LOG"
"""


class CleanAndBuildTest(unittest.TestCase):
    def test_cleans_regenerates_ipa_and_builds_six_databases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            script = scripts_dir / SCRIPT.name
            shutil.copy2(SCRIPT, script)

            for lang_code in ("en", "de", "tr"):
                language_dir = root / "out" / lang_code
                language_dir.mkdir(parents=True)
                for name in (
                    f"wordlist_{lang_code}_ipa.txt",
                    f"wordlist_{lang_code}_espeak_ipa.txt",
                ):
                    (language_dir / name).write_text(
                        'word\t["/wɝd/"]\n', encoding="utf-8"
                    )

            fake_bin = Path(temp_dir) / "bin"
            fake_bin.mkdir()
            fake_python = fake_bin / "python3"
            fake_python.write_text(FAKE_PYTHON, encoding="utf-8")
            fake_python.chmod(0o755)
            call_log = Path(temp_dir) / "calls.txt"
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["CLEAN_BUILD_CALL_LOG"] = str(call_log)

            release = "kaikki-v20260909"
            result = subprocess.run(
                [str(script), "--release-version", release,
                 "--extreme-distance", "0.9"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )

            calls = call_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 18)
            self.assertEqual(
                sum("clean_rhyme_words.py" in call for call in calls), 6
            )
            self.assertEqual(sum("clean_rhyme_ipa.py" in call for call in calls), 3)
            self.assertTrue(all(
                "--replace-extreme-mismatches --extreme-distance 0.9" in call
                for call in calls if "clean_rhyme_ipa.py" in call
            ))
            self.assertEqual(sum("generate_rhyme_db.py" in call for call in calls), 6)
            self.assertTrue(all("extract_ipa.py" not in call for call in calls))
            self.assertTrue(
                all("generate_espeak_ipa.py" not in call for call in calls)
            )
            self.assertIn(f"for {release}", result.stdout)

            call_log.write_text("", encoding="utf-8")
            subprocess.run(
                [str(script), "--release-version", release, "--report-only"],
                check=True, capture_output=True, text=True, env=environment,
            )
            audit_calls = call_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum("clean_rhyme_ipa.py" in call for call in audit_calls), 3)
            self.assertTrue(all(
                "--replace-extreme-mismatches" not in call
                for call in audit_calls if "clean_rhyme_ipa.py" in call
            ))

    def test_requires_a_valid_release_version(self):
        missing = subprocess.run(
            [str(SCRIPT)], check=False, capture_output=True, text=True
        )
        invalid = subprocess.run(
            [str(SCRIPT), "--release-version", "../unsafe"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(missing.returncode, 2)
        self.assertIn("--release-version is required", missing.stderr)
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("invalid release version", invalid.stderr)


if __name__ == "__main__":
    unittest.main()

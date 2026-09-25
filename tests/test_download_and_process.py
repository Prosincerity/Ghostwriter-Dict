import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
PIPELINE = PROJECT_DIR / "scripts" / "download_and_process.sh"


FAKE_CURL = r"""#!/usr/bin/env bash
set -euo pipefail
head_request=false
output=""
etag_save=""
url=""
while (( $# > 0 )); do
    case "$1" in
        --head)
            head_request=true
            shift
            ;;
        --output)
            output="$2"
            shift 2
            ;;
        --etag-save)
            etag_save="$2"
            shift 2
            ;;
        http://*|https://*)
            url="$1"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

if [[ "$head_request" == true ]]; then
    case "$url" in
        */downloads/de/*) modified="Tue, 01 Sep 2026 03:00:00 GMT" ;;
        */downloads/tr/*) modified="Thu, 03 Sep 2026 03:00:00 GMT" ;;
        *) modified="Wed, 02 Sep 2026 03:00:00 GMT" ;;
    esac
    printf 'HTTP/1.1 200 OK\r\nETag: "fixture"\r\nLast-Modified: %s\r\n\r\n' "$modified"
else
    printf 'fixture archive\n' > "$output"
    printf '"fixture"\n' > "$etag_save"
fi
"""

FAKE_PYTHON = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$PIPELINE_CALL_LOG"
"""


class DownloadAndProcessTest(unittest.TestCase):
    def test_orchestrates_complete_six_database_build(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            pipeline = scripts_dir / PIPELINE.name
            shutil.copy2(PIPELINE, pipeline)

            fake_bin = Path(temp_dir) / "bin"
            fake_bin.mkdir()
            for name, contents in (("curl", FAKE_CURL), ("python3", FAKE_PYTHON)):
                executable = fake_bin / name
                executable.write_text(contents, encoding="utf-8")
                executable.chmod(0o755)

            call_log = Path(temp_dir) / "python-calls.txt"
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["PIPELINE_CALL_LOG"] = str(call_log)
            result = subprocess.run(
                [str(pipeline), "--extreme-distance", "0.9"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )

            release = "kaikki-v20260902"
            self.assertIn(f"Kaikki release: {release}", result.stdout)
            calls = call_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 22)
            self.assertIn("build_reports.py downloading", calls[0])
            self.assertIn("extract_ipa.py", calls[1])
            self.assertNotIn("--latin-headwords-only", calls[1])
            self.assertIn("--reuse-if-current", calls[1])
            self.assertIn("generate_espeak_ipa.py", calls[2])
            for archive in ("de-extract.jsonl.gz", "tr-extract.jsonl.gz",
                            "raw-wiktextract-data.jsonl.gz"):
                self.assertIn(f"--source-archive {root / 'raw' / archive}", calls[2])
            self.assertEqual(sum("clean_rhyme_words.py" in call for call in calls), 6)
            self.assertEqual(sum("clean_rhyme_ipa.py" in call for call in calls), 3)
            self.assertEqual(
                calls[-1],
                f"{scripts_dir / 'package_release.py'} --release-version {release}",
            )
            self.assertTrue(all(
                "--replace-extreme-mismatches --extreme-distance 0.9" in call
                for call in calls if "clean_rhyme_ipa.py" in call
            ))
            for lang_code in ("en", "de", "tr"):
                self.assertTrue(
                    any(
                        f"/{lang_code}_{release}.db" in call
                        for call in calls
                    )
                )
                self.assertTrue(
                    any(
                        f"/{lang_code}_espeak_{release}.db" in call
                        for call in calls
                    )
                )
                self.assertIn(
                    f"{lang_code}_espeak_{release}.db.gz", result.stdout
                )
            self.assertEqual(len(list((root / "raw").glob("*.jsonl.gz"))), 3)

            call_log.write_text("", encoding="utf-8")
            subprocess.run(
                [str(pipeline), "--report-only", "--extreme-distance", "0.9"],
                check=True, capture_output=True, text=True, env=environment,
            )
            audit_calls = call_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum("clean_rhyme_ipa.py" in call for call in audit_calls), 3)
            self.assertTrue(all(
                "--replace-extreme-mismatches" not in call
                for call in audit_calls if "clean_rhyme_ipa.py" in call
            ))

    def test_skip_download_requires_an_explicit_release(self):
        result = subprocess.run(
            [str(PIPELINE), "--skip-download"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "--skip-download requires --release-version",
            result.stderr,
        )

    def test_rejects_an_invalid_release_before_starting_the_pipeline(self):
        result = subprocess.run(
            [str(PIPELINE), "--release-version", "../unsafe"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid release version", result.stderr)


if __name__ == "__main__":
    unittest.main()

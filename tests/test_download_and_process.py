import gzip
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
    if [[ "${DOWNLOAD_FAULT:-}" == "redirect" ]]; then
        printf 'HTTP/1.1 302 Found\r\nLast-Modified: %s\r\nETag: "redirect"\r\n\r\nHTTP/2 200\r\n\r\n' "$modified"
        exit 0
    fi
    printf 'HTTP/1.1 200 OK\r\nETag: "fixture"\r\nLast-Modified: %s\r\n\r\n' "$modified"
else
    printf 'fixture archive\n' | gzip -n > "$output"
    if [[ "${DOWNLOAD_FAULT:-}" == "truncated" ]]; then
        truncate -s -4 "$output"
    fi
    if [[ "${DOWNLOAD_FAULT:-}" == "changed" ]]; then
        printf '"updated"\n' > "$etag_save"
    else
        printf '"fixture"\n' > "$etag_save"
    fi
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
            self.assertEqual(len(calls), 2)
            self.assertIn("build_reports.py downloading", calls[0])
            self.assertIn(f"pipeline.py --release-version {release}", calls[1])
            self.assertIn("--replace-extreme-mismatches --extreme-distance 0.9", calls[1])
            self.assertEqual(calls[1].count("--archive"), 3)
            self.assertEqual(len(list((root / "raw").glob("*.jsonl.gz"))), 3)

            local = root / "raw/de-extract.jsonl.gz"
            local.write_bytes(b"content altered despite the same ETag")
            call_log.write_text("", encoding="utf-8")
            subprocess.run(
                [str(pipeline), "--report-only", "--extreme-distance", "0.9"],
                check=True, capture_output=True, text=True, env=environment,
            )
            audit_calls = call_log.read_text(encoding="utf-8").splitlines()
            self.assertNotIn("--replace-extreme-mismatches", audit_calls[-1])
            self.assertEqual(gzip.decompress(local.read_bytes()), b"fixture archive\n")
            self.assertIn("--status de=downloaded", audit_calls[0])
            self.assertIn("--status tr=reused", audit_calls[0])

    def test_invalid_download_preserves_previous_archive(self):
        for fault in ("truncated", "changed", "redirect"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                scripts = root / "scripts"
                scripts.mkdir()
                pipeline = scripts / PIPELINE.name
                shutil.copy2(PIPELINE, pipeline)
                for name, contents in (("curl", FAKE_CURL), ("python3", FAKE_PYTHON)):
                    fake = root / name
                    fake.write_text(contents)
                    fake.chmod(0o755)
                raw = root / "raw"
                raw.mkdir()
                # English supplies the release date, so verify its redirect
                # response cannot inherit a date from a different resource.
                filename = "raw-wiktextract-data.jsonl.gz" if fault == "redirect" else "de-extract.jsonl.gz"
                previous = raw / filename
                payload = gzip.compress(b"previous complete archive", mtime=0)
                previous.write_bytes(payload)
                env = dict(os.environ, DOWNLOAD_FAULT=fault, PATH=f"{root}:{os.environ['PATH']}",
                           PIPELINE_CALL_LOG=str(root / "calls"))
                result = subprocess.run([pipeline], env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(previous.read_bytes(), payload)
                self.assertEqual(list(raw.glob("*.part")), [])

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

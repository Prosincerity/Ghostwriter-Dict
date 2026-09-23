import gzip
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "package_release.py"
RELEASE = "kaikki-v20260909"


class PackageReleaseTest(unittest.TestCase):
    def test_packages_six_databases_and_writes_matching_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy2(SCRIPT, scripts / SCRIPT.name)
            for lang in ("en", "de", "tr"):
                directory = root / "out" / lang
                directory.mkdir(parents=True)
                for source in ("", "_espeak"):
                    database = directory / f"{lang}{source}_{RELEASE}.db"
                    content = (f"{lang}{source}".encode() * 1000)
                    database.write_bytes(content)

            result = subprocess.run(
                [sys.executable, str(scripts / SCRIPT.name), "--release-version", RELEASE],
                capture_output=True,
                text=True,
                check=True,
                cwd=temp_dir,
            )
            self.assertIn("SHA256SUMS", result.stderr)
            expected_lines = []
            for database in (root / "out").glob("*/*.db"):
                archive = database.with_name(database.name + ".gz")
                self.assertEqual(gzip.decompress(archive.read_bytes()), database.read_bytes())
                self.assertEqual(archive.read_bytes()[4:8], b"\0\0\0\0")
                expected_lines.append(
                    f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  "
                    f"{archive.relative_to(root / 'out')}"
                )
            self.assertEqual(
                (root / "out" / "SHA256SUMS").read_text(encoding="ascii").splitlines(),
                sorted(expected_lines, key=lambda line: line.split("  ", 1)[1]),
            )

    def test_missing_database_preserves_existing_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy2(SCRIPT, scripts / SCRIPT.name)
            directory = root / "out" / "en"
            directory.mkdir(parents=True)
            archive = directory / f"en_{RELEASE}.db.gz"
            archive.write_bytes(b"previous archive")
            manifest = root / "out" / "SHA256SUMS"
            manifest.write_text("previous manifest\n", encoding="ascii")
            result = subprocess.run(
                [sys.executable, str(scripts / SCRIPT.name), "--release-version", RELEASE],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing or empty databases", result.stderr)
            self.assertEqual(archive.read_bytes(), b"previous archive")
            self.assertEqual(manifest.read_text(encoding="ascii"), "previous manifest\n")


if __name__ == "__main__":
    unittest.main()

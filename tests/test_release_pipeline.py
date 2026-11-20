"""Run the real release entry points against a private three-edition fixture."""

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
VERSION = "kaikki-v20260902"


class ReleasePipelineTest(unittest.TestCase):
    def test_release_reuse_invalidation_and_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scripts = root / "scripts"
            shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
            raw = root / "raw"
            raw.mkdir()
            out = root / "out"
            engine = root / "espeak-ng"
            engine.write_text("""#!/bin/sh
if [ "$1" = "--version" ]; then echo fixture; exit; fi
while IFS= read -r word; do
    [ "$word" != fail ] || [ -f "$ALLOW_FILE" ] || exit 17
    echo 'ˈma'
done
""")
            engine.chmod(0o755)
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", ALLOW_FILE=str(root / "allow"))
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            entries = [
                ("en", "iPhone", ["/ˈma/"]), ("en", "Iphone", ["/ˈmi/", "/?bad/", "\t/ˈma/"]),
                ("en", "iphone", []), ("en", "A.B.D.", []), ("en", "bad\tword", []),
                ("en", "rock&roll", ["/ˈma/"]), ("en", "a/b", []), ("en", "<a,b>", []),
                ("de", "ÖPNV", []), ("de", "Straße", ["/ˈma/"]), ("de", "gru\u0308n", []),
                ("tr", "Işık", []), ("tr", "İzmir", ["?"]), ("tr", "İzmir", []),
            ]
            def archive(name, records):
                payload = "".join(json.dumps(dict(lang_code=lang, word=word,
                    sounds=[{"audio-ipa": ipa} for ipa in ipas])) + "\n" for lang, word, ipas in records)
                (raw / name).write_bytes(gzip.compress(payload.encode(), mtime=0))

            de = "de-extract.jsonl.gz"
            archive(de, entries)
            archive("tr-extract.jsonl.gz", [])
            archive("raw-wiktextract-data.jsonl.gz", [])

            def run(full=True, success=True):
                command = [scripts / ("download_and_process.sh" if full else "clean_and_build.sh"),
                           "--release-version", VERSION]
                if full:
                    command.append("--skip-download")
                result = subprocess.run(command, env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode == 0, success, result.stderr)
                return result.stdout

            run()
            projection = [json.loads(line) for line in (out / "en/raw_en_noipa.jsonl").read_text().splitlines()]
            self.assertIn(["bad\tword", []], projection)
            self.assertIn(["A.B.D.", []], projection)
            self.assertEqual((out / "tr/wordlist_tr_noipa.txt").read_text().splitlines(), ["izmir", "ışık"])
            self.assertEqual((out / "de/wordlist_de_noipa.txt").read_text(), "grün\n")
            word, ipas = (out / "en/wordlist_en_rhyme_eligible.txt").read_text().strip().split("\t")
            self.assertEqual((word, json.loads(ipas)), ("iphone", ["/ˈma/", "/ˈmi/"]))
            self.assertEqual((out / "en/wordlist_en_espeak_rhyme_eligible.txt").read_text(), 'iphone\t["ˈma"]\n')
            audit = out / "en/reports/cleaning"
            self.assertEqual(json.loads((audit / "entries_report.json").read_text())["counts"],
                             dict(input=8, removed=5, retained=1, merged=2))
            self.assertEqual(json.loads((audit / "entries_report.json").read_text())["affected_entries"]["embedded_control"], 1)
            summary = (audit / "summary.md").read_text()
            self.assertIn('## disallowed_headword_characters\n\n<p>"&lt;a,b&gt;", "a/b", "bad\\tword", "rock&amp;roll"</p>', summary)
            sums = (out / "SHA256SUMS").read_text().splitlines()
            self.assertEqual(len(sums), 6)
            for row in sums:
                digest, name = row.split("  ")
                data = (out / name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
                db = (out / name).with_suffix("")
                self.assertEqual(gzip.decompress(data), db.read_bytes())
                with sqlite3.connect(db) as connection:
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertNotIn("Building ", run())
            path = out / "tr/wordlist_tr_espeak_ipa.txt"
            path.write_text("damaged")
            repaired = run()
            self.assertIn("Building tr/generate", repaired)
            self.assertNotIn("Building tr/validate", repaired)
            with (scripts / "prepare_entries.py").open("a") as code:
                code.write("\n# fixture rule revision\n")
            changed = run()
            self.assertIn("Building en/clean", changed)
            self.assertNotIn("Building en/generate", changed)
            # A behavior change with unchanged archives should invalidate only
            # consumers of changed prepared lists, even with preserved mtimes.
            policy = scripts / "clean_rhyme_words.py"
            before = policy.read_bytes()
            stat = policy.stat()
            policy.write_bytes(before +
                               b'\nACCEPTED_HEADWORD_ALPHANUMERICS -= frozenset("g")\n')
            os.utime(policy, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            changed = run()
            self.assertIn("Building de/generate", changed)
            self.assertIn("Building de/validate", changed)
            self.assertNotIn("Building tr/generate", changed)
            # Change the source without changing the timestamp or byte length.
            policy.write_bytes(policy.read_bytes().replace(b'frozenset("g")', b'frozenset("z")'))
            os.utime(policy, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            run()
            self.assertEqual((out / "de/wordlist_de_noipa.txt").read_text(), "grün\n")
            policy.write_bytes(before)
            run()
            self.assertEqual((out / "SHA256SUMS").read_text().splitlines(), sums)
            archive(de, entries + [("en", "fail", [])])
            failed = run(success=False)
            self.assertIn("Building en/generate", failed)
            self.assertNotIn("Building tr/clean", failed)
            self.assertEqual((out / "SHA256SUMS").read_text().splitlines(), sums)
            (root / "allow").touch()
            recovered = run()
            self.assertIn("Building en/generate", recovered)
            self.assertIn("Building en/validate", recovered)
            self.assertNotIn("Building de/generate", recovered)
            self.assertNotIn("Building ", run())
            # The legacy rebuild accepts existing IPA lists, including empty lists.
            run(full=False)
            self.assertNotIn("Building ", run(full=False))

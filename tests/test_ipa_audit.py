import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generate_rhyme_db import build_database
from ipa_audit import audit_file
from ipa_reversal import restore_original, reverse_ipa_for_db


class DatabaseAuditTest(unittest.TestCase):
    def test_suffix_lookup_distinguishes_an_affricate_from_adjacent_consonants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, db = root / "words.tsv", root / "demo.db"
            source.write_text('cluster\t["/ast/"]\naffricate\t["/ats/"]\n')
            build_database(source, db, "de", "demo")
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("PRAGMA case_sensitive_like = ON")
                query = "SELECT word FROM dictionary WHERE ipa_reversed LIKE ?"
                self.assertEqual(connection.execute(query, ("/ t %",)).fetchall(), [("cluster",)])
                self.assertEqual(connection.execute(query, ("/ ts %",)).fetchall(), [("affricate",)])
                plan = connection.execute("EXPLAIN QUERY PLAN " + query, ("/ t %",)).fetchone()
                self.assertIn("idx_ipa_reversed", plan[-1])

    def test_sqlite_preserves_distinct_spellings_and_reports_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source = directory / "words.tsv"
            values = ["ã", "ã", " ã ", "[ˈã]", "a  b"]
            source.write_text("word\t" + json.dumps(values) + "\n", encoding="utf-8")
            db = directory / "en_demo.db"
            report = directory / "report.json"
            build_database(source, db, "en", "demo", report_path=report)
            with closing(sqlite3.connect(db)) as connection:
                rows = dict(connection.execute("SELECT ipa, ipa_reversed FROM dictionary"))
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone(), (2,))
            self.assertEqual(set(rows), set(values))
            self.assertEqual({ipa:restore_original(key) for ipa,key in rows.items()}, {v:v for v in values})
            data = json.loads(report.read_text())
            self.assertEqual(data["unique_ipa"], 5)
            self.assertEqual(data["round_trip_failures"], [])
            first = db.read_bytes(), report.read_bytes()
            build_database(source, db, "en", "demo", report_path=report)
            self.assertEqual(first, (db.read_bytes(), report.read_bytes()))
            with mock.patch("ipa_reversal.deserialize", return_value=("bad",)):
                with self.assertRaisesRegex(ValueError, "wiktionary.*word.*round-trip"):
                    build_database(source, db, "en", "demo", report_path=report)
            self.assertEqual(first, (db.read_bytes(), report.read_bytes()))
            self.assertFalse(list(directory.rglob("*.part")))

    def test_engine_boundaries_apply_only_to_exactly_matching_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wordlist, db, report = (root / name for name in ("words.tsv", "demo.db", "audit.json"))
            wordlist.write_text('apfel\t["ˈapfəl","ˈapfɛl"]\n', encoding="utf-8")
            engine = root / "espeak"
            engine.write_text("""#!/bin/sh
if [ "$1" = --version ]; then echo fixture; exit; fi
[ "$3" = '--sep=_' ] || exit 1
while read -r word; do echo 'ˈa_p_f_ə_l'; done
""")
            engine.chmod(0o755)
            build_database(wordlist, db, "de", "demo", "espeak", report, str(engine))
            with closing(sqlite3.connect(db)) as con:
                self.assertEqual(dict(con.execute("select ipa, ipa_reversed from dictionary")),
                                 {"ˈapfəl": "l ə f p ˈa", "ˈapfɛl": "l ɛ pf ˈa"})
            audit = json.loads(report.read_text())
            self.assertEqual(audit["boundary_evidence"], {"engine_exact_match":1, "engine_spelling_differs":1})
            self.assertEqual(reverse_ipa_for_db("ˈapfəl", "de", "espeak", reference="ˈa_p_f_ə_l"), "l ə f p ˈa")

    def test_raw_audit_includes_unknowns_and_nontext_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, report = (Path(tmp) / name for name in ("raw.jsonl", "report.json"))
            source.write_text(json.dumps(["word", [None, "☃", "☃", "\udfff"]]) + "\n")
            audit_file(source, report, "en", "wiktionary", True)
            data = json.loads(report.read_text())
            self.assertEqual((data["pronunciations"], data["unique_ipa"], data["nontext_fields"]), (3,2,1))
            self.assertEqual(data["diagnostics"]["unknown"]["☃"]["codepoints"], ["U+2603"])
            self.assertEqual(data["diagnostics"]["unknown"]["\udfff"]["codepoints"], ["U+DFFF"])
            self.assertEqual(data["round_trip_failures"], [])

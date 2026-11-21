import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
EXTRACTOR = PROJECT_DIR / "scripts" / "extract_ipa.py"
sys.path.insert(0, str(EXTRACTOR.parent))
import extract_ipa
import raw_entries


def write_gzip_jsonl(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


class ExtractIpaTest(unittest.TestCase):
    def test_release_projection_counts_invalid_utf8_but_preserves_json_escapes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "edition.jsonl.gz"
            archive.write_bytes(gzip.compress(
                b'{"lang_code":"en","word":"ghost","note":"\xff"}\n'
                b'{"lang_code":"en","word":"saved","sounds":[{"ipa":"\\udcff"}]}\n'
                b'{"lang_code":"en","word":"last"}\n'
            ))
            counts = raw_entries.extract([archive], {"en": root})
            self.assertEqual(counts["records_read"], 3)
            self.assertEqual(counts["bad_json_records"], 1)
            self.assertEqual(counts["en"], 2)
            ipa, missing = raw_entries.raw_paths(root, "en")
            self.assertEqual(json.loads(ipa.read_text()), ["saved", ["\udcff"]])
            self.assertEqual(json.loads(missing.read_text()), ["last", []])

    @unittest.skipUnless(hasattr(sys, "get_int_max_str_digits"), "requires Python's integer digit limit")
    def test_decoder_integer_limit_does_not_discard_later_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "edition.jsonl.gz"
            archive.write_bytes(gzip.compress(
                b'{"metadata":' + b"1" * 700 + b'}\n{"word":"kept","lang_code":"en"}\n'
            ))
            result = subprocess.run(
                [sys.executable, str(EXTRACTOR), str(archive), "--lang-code", "en", "--outdir", str(root)],
                env=dict(os.environ, PYTHONINTMAXSTRDIGITS="640"), text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Bad JSON records      : 1", result.stdout)
            self.assertEqual((root / "en/wordlist_en_noipa.txt").read_text(), "kept\n")

    def test_reuses_complete_wordlists_until_source_code_or_output_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scripts = root / "scripts"
            scripts.mkdir()
            extractor = scripts / "extract_ipa.py"
            shutil.copy2(EXTRACTOR, extractor)
            shutil.copy2(EXTRACTOR.parent / "build_reports.py", scripts / "build_reports.py")
            shutil.copy2(EXTRACTOR.parent / "progress.py", scripts)
            source = root / "entries.jsonl.gz"
            write_gzip_jsonl(source, [{
                "word": "Hello", "lang_code": "en", "sounds": [{"ipa": "/ˈhɛloʊ/"}],
            }])
            outdir = root / "out"
            command = [sys.executable, str(extractor), str(source), "--lang-code", "en",
                       "--outdir", str(outdir), "--reuse-if-current"]

            def run():
                return subprocess.run(command, check=True, capture_output=True, text=True)

            self.assertIn("JSONL records", run().stdout)
            ipa = outdir / "en" / "wordlist_en_ipa.txt"
            report = outdir / "en" / "reports" / "reading" / "report.json"
            first_mtime = ipa.stat().st_mtime_ns
            self.assertIn("Reusing canonical wordlists", run().stdout)
            self.assertEqual(ipa.stat().st_mtime_ns, first_mtime)
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["words_with_ipa"], 1)

            ipa.write_text("damaged\n", encoding="utf-8")
            self.assertIn("JSONL records", run().stdout)
            self.assertEqual(ipa.read_text(encoding="utf-8"), 'hello\t["/ˈhɛloʊ/"]\n')

            write_gzip_jsonl(source, [{
                "word": "New", "lang_code": "en", "sounds": [{"ipa": "/nuː/"}],
            }])
            self.assertIn("JSONL records", run().stdout)
            self.assertIn("new", ipa.read_text(encoding="utf-8"))

            with extractor.open("a", encoding="utf-8") as destination:
                destination.write("\n# changed extraction code\n")
            self.assertIn("JSONL records", run().stdout)

    def test_cache_recovers_from_nonobject_report_and_detects_same_size_edits(self):
        for damage in ("report", "archive"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "edition.jsonl"
                source.write_text('{"word":"one","lang_code":"en"}\n')
                command = [sys.executable, str(EXTRACTOR), str(source), "--lang-code", "en",
                           "--outdir", str(root), "--reuse-if-current"]
                subprocess.run(command, capture_output=True, check=True)
                if damage == "report":
                    (root / "en/reports/reading/report.json").write_text("[]")
                else:
                    stamp = source.stat()
                    source.write_text(source.read_text().replace("one", "two"))
                    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                subprocess.run(command, capture_output=True, check=True)
                expected = "two" if damage == "archive" else "one"
                self.assertEqual((root / "en/wordlist_en_noipa.txt").read_text(), expected + "\n")

    def test_default_output_is_script_relative(self):
        with patch.object(sys, "argv", [str(EXTRACTOR), "input.jsonl", "--lang-code", "en"]):
            self.assertEqual(extract_ipa.parse_args().outdir, PROJECT_DIR / "out")

    def test_malformed_lang_code_does_not_stop_later_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "entries.jsonl.gz"
            write_gzip_jsonl(source, [
                {"word": "broken", "lang_code": ["en"], "sounds": [{"ipa": "/ˈbroʊkən/"}]},
                {"word": "valid", "lang_code": "en", "sounds": [{"ipa": "/ˈvælɪd/"}]},
            ])
            result = subprocess.run(
                [sys.executable, str(EXTRACTOR), str(source), "--lang-code", "en",
                 "--outdir", str(temp_path / "out")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (temp_path / "out" / "en" / "wordlist_en_ipa.txt").read_text(encoding="utf-8"),
                'valid\t["/ˈvælɪd/"]\n',
            )

    def test_empty_ipa_wrappers_route_word_to_noipa(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "entries.jsonl.gz"
            write_gzip_jsonl(source, [
                {"word": "missing", "lang_code": "en", "sounds": [
                    {"ipa": "[ ]"}, {"audio-ipa": "/ /"},
                ]},
            ])
            result = subprocess.run(
                [sys.executable, str(EXTRACTOR), str(source), "--lang-code", "en",
                 "--outdir", str(temp_path / "out")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (temp_path / "out" / "en" / "wordlist_en_noipa.txt").read_text(encoding="utf-8"),
                "missing\n",
            )
            self.assertEqual(
                (temp_path / "out" / "en" / "wordlist_en_ipa.txt").read_text(encoding="utf-8"),
                "",
            )

    def test_invalid_utf8_record_is_counted_and_later_records_are_kept(self):
        valid = json.dumps(
            {"word": "valid", "lang_code": "en", "sounds": [{"ipa": "/ˈvælɪd/"}]}
        ).encode("utf-8")
        for compressed in (False, True):
            with self.subTest(compressed=compressed), tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                source = temp_path / ("entries.jsonl.gz" if compressed else "entries.jsonl")
                payload = b'{"word":"bad\xff","lang_code":"en"}\n' + valid + b"\n"
                if compressed:
                    with gzip.open(source, "wb") as output:
                        output.write(payload)
                else:
                    source.write_bytes(payload)
                result = subprocess.run(
                    [sys.executable, str(EXTRACTOR), str(source), "--lang-code", "en",
                     "--outdir", str(temp_path / "out")],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Bad JSON records      : 1", result.stdout)
                self.assertEqual(
                    (temp_path / "out" / "en" / "wordlist_en_ipa.txt").read_text(encoding="utf-8"),
                    'valid\t["/ˈvælɪd/"]\n',
                )

    def test_json_surrogate_does_not_corrupt_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "entries.jsonl"
            source.write_text(
                '{"word":"bad\\udcff","lang_code":"en"}\n'
                '{"word":"missing","lang_code":"en","sounds":[{"ipa":"/a\\udcff/"}]}\n'
                '{"word":"valid","lang_code":"en","sounds":[{"ipa":"/ˈvælɪd/"}]}\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(EXTRACTOR), str(source), "--lang-code", "en",
                 "--outdir", str(temp_path / "out")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (temp_path / "out" / "en" / "wordlist_en_ipa.txt").read_text(encoding="utf-8"),
                'valid\t["/ˈvælɪd/"]\n',
            )
            self.assertEqual(
                (temp_path / "out" / "en" / "wordlist_en_noipa.txt").read_text(encoding="utf-8"),
                "missing\n",
            )

    def test_merges_all_dumps_routes_languages_and_removes_duplicates(self):
        german_edition = [
            {
                "word": "Hallo",
                "lang": "Deutsch",
                "lang_code": "de",
                "sounds": [{"ipa": "/haˈloː/"}],
            },
            {"word": "Dings", "lang": "Deutsch", "lang_code": "de"},
            {
                "word": "kanka",
                "lang": "Türkisch",
                "lang_code": "tr",
                "sounds": [{"ipa": "/kanka/"}],
            },
            {
                "word": "شارع",
                "lang": "Türkisch",
                "lang_code": "tr",
                "sounds": [{"ipa": "/ʃaːriʕ/"}],
            },
            {"word": "привет", "lang": "Russisch", "lang_code": "ru"},
        ]
        turkish_edition = [
            {
                "word": "kanka",
                "lang": "Türkçe",
                "lang_code": "tr",
                "sounds": [{"ipa": "/kanka/"}, {"ipa": "[kaŋka]"}],
            },
            {
                "word": "internet argosu 😎",
                "lang": "Türkçe",
                "lang_code": "tr",
                "sounds": [{"ipa": "[...]"}],
            },
            {
                "word": "Hallo",
                "lang": "Almanca",
                "lang_code": "de",
                "sounds": [{"ipa": "/haˈloː/"}],
            },
            {
                "word": "hallo",
                "lang_code": "de",
                "sounds": [{"ipa": "[haˈloː]"}],
            },
            {
                "word": "Straße",
                "lang": "Almanca",
                "lang_code": "de",
                "sounds": [{"audio-ipa": "[ˈʃtʁaːsə]"}],
            },
            # A misleading human-readable field must not override lang_code.
            {"word": "yanlış", "lang": "tr", "lang_code": "el"},
        ]
        english_edition = [
            {
                "word": "hammer",
                "lang": "English",
                "lang_code": "en",
                "sounds": [
                    {"ipa": "/ˈhæmə/"},
                    {"ipa": "/ˈhæmɚ/"},
                    {"ipa": "/ˈhæmə/"},
                ],
            },
            {"word": "rizz 😎", "lang": "English", "lang_code": "en"},
            {
                "word": "kanka",
                "lang": "Turkish",
                "lang_code": "tr",
                "sounds": [{"ipa": "[kanˈka]"}],
            },
            {
                "word": "Hallo",
                "lang": "German",
                "lang_code": "de",
                "sounds": [{"ipa": "/haˈloː/"}],
            },
            {"word": "Apple", "lang_code": "en"},
            {
                "word": "apple",
                "lang_code": "en",
                "sounds": [{"ipa": "/ˈæpəl/"}, {"ipa": "/ˈæpəl/"}],
            },
            {
                "word": "Cat",
                "lang_code": "en",
                "sounds": [{"ipa": "/kæt/"}],
            },
            {
                "word": "cat",
                "lang_code": "en",
                "sounds": [{"ipa": "[kʰæt]"}, {"ipa": "/kæt/"}],
            },
            {
                "word": "ABD",
                "lang_code": "en",
                "sounds": [{"ipa": "/eɪ biː diː/"}],
            },
            {"word": "Abd", "lang_code": "en"},
            {"word": "abd", "lang_code": "en"},
            {"word": "ABd", "lang_code": "en"},
            {"word": "iPhone", "lang_code": "en"},
            {"word": "شارع", "lang": "English", "lang_code": "en"},
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            de_input = temp_path / "de.jsonl.gz"
            tr_input = temp_path / "tr.jsonl.gz"
            en_input = temp_path / "en.jsonl.gz"
            write_gzip_jsonl(de_input, german_edition)
            write_gzip_jsonl(tr_input, turkish_edition)
            write_gzip_jsonl(en_input, english_edition)

            result = subprocess.run(
                [
                    sys.executable,
                    str(EXTRACTOR),
                    str(de_input),
                    str(tr_input),
                    str(en_input),
                    "--lang-code",
                    "en",
                    "--lang-code",
                    "de",
                    "--lang-code",
                    "tr",
                    "--latin-headwords-only",
                    "--outdir",
                    str(temp_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            de_ipa = (temp_path / "de" / "wordlist_de_ipa.txt").read_text(
                encoding="utf-8"
            )
            de_noipa = (temp_path / "de" / "wordlist_de_noipa.txt").read_text(
                encoding="utf-8"
            )
            tr_ipa = (temp_path / "tr" / "wordlist_tr_ipa.txt").read_text(
                encoding="utf-8"
            )
            tr_noipa = (temp_path / "tr" / "wordlist_tr_noipa.txt").read_text(
                encoding="utf-8"
            )
            en_ipa = (temp_path / "en" / "wordlist_en_ipa.txt").read_text(
                encoding="utf-8"
            )
            en_noipa = (temp_path / "en" / "wordlist_en_noipa.txt").read_text(
                encoding="utf-8"
            )

        self.assertEqual(
            de_ipa,
            'hallo\t["/haˈloː/","[haˈloː]"]\nstraße\t["[ˈʃtʁaːsə]"]\n',
        )
        self.assertEqual(de_noipa, "dings\n")
        self.assertEqual(tr_ipa, 'kanka\t["/kanka/","[kaŋka]","[kanˈka]"]\n')
        self.assertEqual(tr_noipa, "internet argosu 😎\n")
        self.assertEqual(
            en_ipa,
            'hammer\t["/ˈhæmə/","/ˈhæmɚ/"]\n'
            'apple\t["/ˈæpəl/"]\n'
            'cat\t["/kæt/","[kʰæt]"]\n'
            'ABD\t["/eɪ biː diː/"]\n',
        )
        self.assertEqual(en_noipa, "rizz 😎\nabd\nABd\niphone\n")
        self.assertNotIn("شارع", tr_ipa + tr_noipa)
        self.assertNotIn("شارع", en_ipa + en_noipa)
        self.assertNotIn("привет", de_ipa + de_noipa + tr_ipa + tr_noipa)
        self.assertNotIn("yanlış", de_ipa + de_noipa + tr_ipa + tr_noipa)
        self.assertIn("Non-Latin words rejected  : 1", result.stdout)


if __name__ == "__main__":
    unittest.main()

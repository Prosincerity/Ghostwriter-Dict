import gzip
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
EXTRACTOR = PROJECT_DIR / "scripts" / "extract_ipa.py"


def write_gzip_jsonl(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


class ExtractIpaTest(unittest.TestCase):
    def test_reuses_complete_wordlists_until_source_code_or_output_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scripts = root / "scripts"
            scripts.mkdir()
            extractor = scripts / "extract_ipa.py"
            shutil.copy2(EXTRACTOR, extractor)
            shutil.copy2(EXTRACTOR.parent / "build_reports.py", scripts / "build_reports.py")
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

    def test_default_output_is_script_relative(self):
        spec = importlib.util.spec_from_file_location("extract_ipa", EXTRACTOR)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            with patch.object(sys, "argv", [str(EXTRACTOR), "input.jsonl", "--lang-code", "en"]):
                self.assertEqual(module.parse_args().outdir, PROJECT_DIR / "out")
        finally:
            del sys.modules[spec.name]

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

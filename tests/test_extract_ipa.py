import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
EXTRACTOR = PROJECT_DIR / "scripts" / "extract_ipa.py"


def write_gzip_jsonl(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


class ExtractIpaTest(unittest.TestCase):
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

            de_ipa = (temp_path / "wordlist_de_ipa.txt").read_text(encoding="utf-8")
            de_noipa = (temp_path / "wordlist_de_noipa.txt").read_text(encoding="utf-8")
            tr_ipa = (temp_path / "wordlist_tr_ipa.txt").read_text(encoding="utf-8")
            tr_noipa = (temp_path / "wordlist_tr_noipa.txt").read_text(encoding="utf-8")
            en_ipa = (temp_path / "wordlist_en_ipa.txt").read_text(encoding="utf-8")
            en_noipa = (temp_path / "wordlist_en_noipa.txt").read_text(encoding="utf-8")

        self.assertEqual(
            de_ipa,
            'Hallo\t["/haˈloː/"]\nStraße\t["[ˈʃtʁaːsə]"]\n',
        )
        self.assertEqual(de_noipa, "Dings\n")
        self.assertEqual(tr_ipa, 'kanka\t["/kanka/","[kaŋka]","[kanˈka]"]\n')
        self.assertEqual(tr_noipa, "internet argosu 😎\n")
        self.assertEqual(en_ipa, 'hammer\t["/ˈhæmə/","/ˈhæmɚ/"]\n')
        self.assertEqual(en_noipa, "rizz 😎\n")
        self.assertNotIn("شارع", tr_ipa + tr_noipa)
        self.assertNotIn("شارع", en_ipa + en_noipa)
        self.assertNotIn("привет", de_ipa + de_noipa + tr_ipa + tr_noipa)
        self.assertNotIn("yanlış", de_ipa + de_noipa + tr_ipa + tr_noipa)
        self.assertIn("Non-Latin words rejected  : 1", result.stdout)


if __name__ == "__main__":
    unittest.main()

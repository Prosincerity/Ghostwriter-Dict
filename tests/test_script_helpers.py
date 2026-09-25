import gzip
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import unicodedata
from collections import Counter
from pathlib import Path
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXTRACT = load_script("extract_ipa_helpers", SCRIPTS_DIR / "extract_ipa.py")
ESPEAK = load_script(
    "generate_espeak_ipa_helpers", SCRIPTS_DIR / "generate_espeak_ipa.py"
)
RHYME = load_script("generate_rhyme_db_helpers", SCRIPTS_DIR / "generate_rhyme_db.py")
WORDS = load_script("clean_rhyme_words_helpers", SCRIPTS_DIR / "clean_rhyme_words.py")
IPA = load_script("clean_rhyme_ipa_helpers", SCRIPTS_DIR / "clean_rhyme_ipa.py")
SMOKE = load_script(
    "run_rhyme_smoke_test_helpers",
    PROJECT_DIR / "tests" / "scripts" / "run_rhyme_smoke_test.py",
)


class TtyBuffer(io.StringIO):
    def isatty(self):
        return True


class ProgressBarTest(unittest.TestCase):
    def test_progress_bars_render_and_finish_on_tty_streams(self):
        cases = [
            (EXTRACT.ProgressBar(10), (5, 2, {"en": 1}), "Reading"),
            (ESPEAK.ProgressBar("Generating en", 10), (5,), "Generating en"),
            (RHYME.ProgressBar(10), (5, 2, 3), "Building"),
            (WORDS.ProgressBar(10), (5, 2, 1), "Cleaning"),
            (IPA.ProgressBar("Regenerating en", 10), (5,), "Regenerating en"),
            (SMOKE.ProgressBar("Sampling en", 10), (5, 2), "Sampling en"),
        ]
        for progress, update_args, label in cases:
            with self.subTest(label=label):
                stream = TtyBuffer()
                progress.stream = stream
                progress.update(*update_args)
                progress.finish()
                self.assertIn(label, stream.getvalue())
                self.assertTrue(stream.getvalue().endswith("\n"))

    def test_progress_bars_are_silent_for_non_tty_streams(self):
        stream = io.StringIO()
        progress = WORDS.ProgressBar(0, stream=stream)
        progress.update(0, 0, 0)
        progress.finish()
        self.assertEqual(stream.getvalue(), "")


class ExtractionHelperTest(unittest.TestCase):
    def test_usable_ipa_rejects_only_complete_placeholders(self):
        for value in (None, 7, "?", " [ ... ] ", "/…/", "[]", "//", "[ ]", "/ /"):
            with self.subTest(value=value):
                self.assertFalse(EXTRACT.is_usable_ipa(value))
        for value in ("/a/", "[a]", "prefix …", "[a?]"):
            with self.subTest(value=value):
                self.assertTrue(EXTRACT.is_usable_ipa(value))

    def test_iter_ipa_fields_ignores_malformed_sound_entries(self):
        sounds = [
            None,
            "bad",
            {"ipa": "/a/", "audio-ipa": "[a]"},
            {"audio-ipa": "[b]"},
        ]
        self.assertEqual(list(EXTRACT.iter_ipa_fields(sounds)), ["/a/", "[a]", "[b]"])
        self.assertEqual(list(EXTRACT.iter_ipa_fields({"ipa": "/a/"})), [])

    def test_non_latin_detection_allows_modifiers_and_nonletters(self):
        self.assertFalse(EXTRACT.contains_non_latin_letter("Hawaiʻian 2026!"))
        self.assertFalse(EXTRACT.contains_non_latin_letter("café 😎"))
        self.assertTrue(EXTRACT.contains_non_latin_letter("StraßeЖ"))

    def test_open_jsonl_and_input_position_support_plain_and_gzip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plain = root / "words.jsonl"
            compressed = root / "words.jsonl.gz"
            plain.write_text('{"word":"plain"}\n', encoding="utf-8")
            with gzip.open(compressed, "wt", encoding="utf-8") as output:
                output.write('{"word":"gzip"}\n')

            for path, expected in ((plain, "plain"), (compressed, "gzip")):
                with self.subTest(path=path.name), EXTRACT.open_jsonl(path) as source:
                    self.assertEqual(json.loads(source.readline())["word"], expected)
                    self.assertGreater(EXTRACT.input_position(source, path), 0)

    def test_wordlist_write_failure_preserves_existing_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            language_dir = Path(temp_dir) / "en"
            language_dir.mkdir()
            ipa_path = language_dir / "wordlist_en_ipa.txt"
            noipa_path = language_dir / "wordlist_en_noipa.txt"
            ipa_path.write_text("previous IPA\n", encoding="utf-8")
            noipa_path.write_text("previous no-IPA\n", encoding="utf-8")
            words = {"word": None, "missing": None}
            word_ipas = {"word": {"/wɜːd/": None}}
            real_replace = os.replace

            def fail_first_replace(source, destination):
                if Path(destination) == ipa_path:
                    raise OSError("replace failed")
                return real_replace(source, destination)

            with mock.patch.object(EXTRACT.os, "replace", side_effect=fail_first_replace):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    EXTRACT.write_language_wordlists(
                        language_dir, "en", words, word_ipas
                    )

            self.assertEqual(ipa_path.read_text(encoding="utf-8"), "previous IPA\n")
            self.assertEqual(
                noipa_path.read_text(encoding="utf-8"), "previous no-IPA\n"
            )
            self.assertEqual(list(language_dir.glob("*.part")), [])


class CleanupHelperTest(unittest.TestCase):
    def test_output_paths_stay_under_reports(self):
        output = Path("build/en/wordlist_en_rhyme_eligible.txt")
        paths = WORDS.output_paths(output)

        self.assertEqual(paths["wordlist"], output)
        for name in ("rejected_words", "word_changes", "report"):
            self.assertEqual(paths[name].parent, output.parent / "reports" / "cleaning")
        ipa_paths = IPA.output_paths(output, output.parent / "espeak.txt")
        for name in ("rejected", "changes", "report"):
            self.assertEqual(ipa_paths[name].parent, output.parent / "reports" / "cleaning")

    def test_headword_normalization_reports_each_transformation_once(self):
        normalized, transformations = WORDS.normalize_headword(
            "soft\N{SOFT HYPHEN}—quote’s₃"
        )

        self.assertEqual(normalized, "soft-quote's3")
        self.assertEqual(
            transformations,
            [
                "remove_soft_hyphen",
                "normalize_apostrophe",
                "normalize_dash",
                "normalize_subscript_digit",
            ],
        )

    def test_notation_helpers_cover_wrappers_splits_and_delimiters(self):
        self.assertEqual(IPA.wrapper("/a/"), ("/", "/", "a"))
        self.assertIsNone(IPA.wrapper("/a]"))
        self.assertEqual(
            IPA.split_alternatives("/a/, [b]"),
            (["/a/", "[b]"], ["split_wrapped_comma_alternatives"]),
        )
        self.assertEqual(IPA.expand_optional_groups("/a/"), (["/a/"], []))
        self.assertTrue(IPA.validate_delimiters("[a]"))
        self.assertFalse(IPA.validate_delimiters("a/b"))

    def test_json_line_writer_is_compact_unicode_json(self):
        output = io.StringIO()
        WORDS.write_json_line(output, {"word": "Hawaiʻian", "reason": "test"})
        self.assertEqual(
            output.getvalue(),
            '{"reason":"test","word":"Hawaiʻian"}\n',
        )

    def test_group_writer_handles_empty_and_detailed_groups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "rejected.json"
            connection = sqlite3.connect(":memory:")
            try:
                connection.execute(
                    "CREATE TABLE rejected (source TEXT, word TEXT, ipa TEXT, "
                    "reason TEXT, details TEXT, position INTEGER)"
                )
                IPA.write_rejections(output, connection)
                self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["groups"], [])
                connection.execute(
                    "INSERT INTO rejected VALUES (?, ?, ?, ?, ?, ?)",
                    ("wiktionary", "bad", "/…/", "incomplete", "{}", 1),
                )
                IPA.write_rejections(output, connection)
                group = json.loads(output.read_text(encoding="utf-8"))["groups"][0]
                self.assertEqual(group["ipas"], ["/…/"])
                self.assertEqual(group["entries"][0]["word"], "bad")
                self.assertEqual(group["entries"][0]["source"], "wiktionary")
            finally:
                connection.close()

    def test_staging_database_closes_when_initialization_fails(self):
        connection = mock.Mock()
        connection.executescript.side_effect = sqlite3.OperationalError("full")
        with mock.patch.object(IPA.sqlite3, "connect", return_value=connection):
            with self.assertRaisesRegex(sqlite3.OperationalError, "full"):
                IPA.create_staging_database(Path("staging.db"))
        connection.close.assert_called_once_with()


class EspeakHelperTest(unittest.TestCase):
    def test_validate_input_counts_words_and_rejects_invalid_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "words.txt"
            path.write_text("one\ntwo words\n", encoding="utf-8")
            self.assertEqual(ESPEAK.validate_input(path), 2)

            for contents, message in (("one\n\n", "empty word"), ("a\tb\n", "tab")):
                with self.subTest(contents=contents):
                    path.write_text(contents, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, message):
                        ESPEAK.validate_input(path)

    def test_resolve_espeak_accepts_executable_and_rejects_missing_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "fake-espeak"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            self.assertEqual(ESPEAK.resolve_espeak(str(executable)), str(executable))
        with self.assertRaisesRegex(SystemExit, "not found"):
            ESPEAK.resolve_espeak("definitely-missing-ghostwriter-espeak")

    def test_call_espeak_reports_process_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "failing-espeak"
            executable.write_text(
                "#!/bin/sh\necho broken >&2\nexit 3\n", encoding="utf-8"
            )
            executable.chmod(0o755)
            with self.assertRaisesRegex(
                RuntimeError, "exit code 3: broken"
            ):
                ESPEAK.call_espeak(str(executable), "en", ["word"])

    def test_write_batch_normalizes_unicode_and_skips_empty_ipa(self):
        output = io.StringIO()
        generated, empty = ESPEAK.write_batch(
            ["Cafe\N{COMBINING ACUTE ACCENT}", "empty"],
            [" a\N{COMBINING ACUTE ACCENT} ", "   "],
            output,
        )
        self.assertEqual((generated, empty), (1, 1))
        word, encoded = output.getvalue().strip().split("\t")
        self.assertEqual(word, unicodedata.normalize("NFC", "Café"))
        self.assertEqual(json.loads(encoded), ["á"])

    def test_write_batch_rejects_a_mismatched_result_count(self):
        with self.assertRaisesRegex(ValueError, "1 results for 2 words"):
            ESPEAK.write_batch(["one", "two"], ["wʌn"], io.StringIO())

    def test_generate_language_failure_preserves_output_and_removes_part(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "words.txt"
            output = root / "output.txt"
            source.write_text("word\n", encoding="utf-8")
            output.write_text("previous\n", encoding="utf-8")
            with mock.patch.object(
                ESPEAK, "call_espeak", side_effect=RuntimeError("failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    ESPEAK.generate_language("fake", source, output, "en", 1, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous\n")
            self.assertFalse(Path(f"{output}.part").exists())

    def test_process_language_validates_and_writes_the_expected_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outdir = Path(temp_dir)
            language_dir = outdir / "en"
            language_dir.mkdir()
            source = language_dir / "wordlist_en_noipa.txt"
            source.write_text("one\ntwo\n", encoding="utf-8")

            with mock.patch.object(
                ESPEAK, "call_espeak", return_value=["wʌn", "tuː"]
            ) as call:
                ESPEAK.process_language("fake-espeak", outdir, "en", 10)

            call.assert_called_once_with("fake-espeak", "en", ["one", "two"])
            output = language_dir / "wordlist_en_espeak_ipa.txt"
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                'one\t["wʌn"]\ntwo\t["tuː"]\n',
            )
            self.assertFalse(Path(f"{output}.part").exists())


class RhymeDatabaseHelperTest(unittest.TestCase):
    def test_inventory_modifiers_and_vowel_classification(self):
        vowels, candidates = RHYME.inventory("en")
        self.assertIn("aɪ", vowels)
        self.assertGreaterEqual(len(candidates[0]), len(candidates[-1]))
        self.assertTrue(RHYME.is_postfix_modifier("̃"))
        self.assertFalse(RHYME.is_postfix_modifier("k"))
        self.assertTrue(RHYME.token_is_vowel("ʷáː", "en"))
        self.assertFalse(RHYME.token_is_vowel("kʰ", "en"))

    def test_parse_wordlist_line_normalizes_and_rejects_invalid_shapes(self):
        path = Path("fixture.txt")
        word, ipas = RHYME.parse_wordlist_line(
            path, 3, 'Café\t["/kafé/", "/kafé/"]\n'
        )
        self.assertEqual(word, "Café")
        self.assertEqual(ipas, ["/kafé/", "/kafé/"])

        invalid = ("\n", "word only\n", "word\t{}\n", "word\t[]\n", 'word\t[""]\n')
        for line in invalid:
            with self.subTest(line=line):
                with self.assertRaises(ValueError):
                    RHYME.parse_wordlist_line(path, 4, line)

    def test_iter_rows_rejects_unknown_tokens_after_valid_sibling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "words.txt"
            path.write_text('word\t["/ˈkæt/","/a☃/"]\n', encoding="utf-8")
            unknown = Counter()
            progress = mock.Mock()
            rows = RHYME.iter_rows(path, "en", unknown, progress)
            self.assertEqual(next(rows)[:2], ("word", "/ˈkæt/"))
            with self.assertRaisesRegex(ValueError, "unrecognized IPA tokens.*☃"):
                next(rows)
            self.assertEqual(unknown, Counter({"☃": 1}))

    def test_release_version_validation_checks_syntax_and_filename(self):
        RHYME.validate_release_version(
            "kaikki-v20260902", Path("en_kaikki-v20260902.db")
        )
        with self.assertRaisesRegex(ValueError, "only letters"):
            RHYME.validate_release_version("bad release", Path("bad release.db"))
        with self.assertRaisesRegex(ValueError, "must include"):
            RHYME.validate_release_version("release-1", Path("en.db"))
        with self.assertRaisesRegex(ValueError, "must include"):
            RHYME.validate_release_version("release-1", Path("en_release-10.db"))


class SmokeHelperTest(unittest.TestCase):
    def test_source_names_artifact_stems_and_scores_are_stable(self):
        self.assertEqual(
            SMOKE.source_name("en", "wiktionary"),
            "wordlist_en_rhyme_eligible.txt",
        )
        self.assertEqual(
            SMOKE.source_name("de", "espeak"),
            "wordlist_de_espeak_rhyme_eligible.txt",
        )
        self.assertEqual(
            SMOKE.artifact_stem("tr", "espeak", 50, "release-1"),
            "tr_espeak_sample-50_release-1",
        )
        score = SMOKE.selection_score("seed", "en", "wiktionary", "row\n")
        self.assertEqual(
            score,
            SMOKE.selection_score("seed", "en", "wiktionary", "row\n"),
        )
        self.assertNotEqual(
            score,
            SMOKE.selection_score("other", "en", "wiktionary", "row\n"),
        )

    def test_create_sample_selects_lowest_scores_in_source_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.txt"
            sample = root / "nested" / "sample.txt"
            lines = [f"word-{index}\t[\"/a/\"]\n" for index in range(8)]
            source.write_text("".join(lines), encoding="utf-8")
            input_words, sampled_words = SMOKE.create_sample(
                source, sample, "en", "wiktionary", 3, "seed"
            )
            selected = set(
                sorted(
                    lines,
                    key=lambda line: SMOKE.selection_score(
                        "seed", "en", "wiktionary", line
                    ),
                )[:3]
            )
            expected = "".join(line for line in lines if line in selected)
            self.assertEqual((input_words, sampled_words), (8, 3))
            self.assertEqual(sample.read_text(encoding="utf-8"), expected)

    def test_create_sample_rejects_empty_rows_without_replacing_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.txt"
            sample = root / "sample.txt"
            source.write_text('word\t["/a/"]\n\n', encoding="utf-8")
            sample.write_text("previous\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "empty row"):
                SMOKE.create_sample(source, sample, "en", "wiktionary", 1, "seed")
            self.assertEqual(sample.read_text(encoding="utf-8"), "previous\n")

    def test_write_manifest_is_atomic_and_pretty_printed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = SMOKE.write_manifest(root, {"language": "en", "rows": 2})
            self.assertEqual(path, root / "reports" / "smoke_manifest.json")
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"language": "en", "rows": 2},
            )
            self.assertFalse(Path(f"{path}.part").exists())

    def test_validate_database_accepts_the_production_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "sample.db"
            connection = sqlite3.connect(database)
            try:
                connection.executescript(RHYME.SCHEMA)
                connection.execute(
                    "INSERT INTO dictionary VALUES (?, ?, ?, ?)",
                    ("cat", "/kæt/", "tæk", "æ"),
                )
                connection.executescript(RHYME.INDEXES)
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(
                SMOKE.validate_database(database, 1),
                {"integrity": "ok", "rows": 1, "unique_words": 1},
            )

    def test_validate_database_is_read_only_and_rejects_extra_indexes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing = root / "missing.db"
            with self.assertRaisesRegex(FileNotFoundError, "missing database"):
                SMOKE.validate_database(missing, 0)
            self.assertFalse(missing.exists())

            database = root / "extra-index.db"
            connection = sqlite3.connect(database)
            try:
                connection.executescript(RHYME.SCHEMA)
                connection.executescript(RHYME.INDEXES)
                connection.execute(
                    "CREATE INDEX unexpected_word_index ON dictionary(word)"
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(RuntimeError, "unexpected dictionary indexes"):
                SMOKE.validate_database(database, 0)


if __name__ == "__main__":
    unittest.main()

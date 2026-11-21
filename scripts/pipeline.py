"""Run the six release stages with content-verified checkpoints."""

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import zlib
from pathlib import Path

# The manifest hashes source bytes. Execute those same bytes even when an edit
# preserves both the file size and the timestamp used by ordinary .pyc caches.
if __name__ == "__main__":
    sys.pycache_prefix = "/dev/null"
    sys.dont_write_bytecode = True

import clean_rhyme_ipa as ipa
import clean_rhyme_words as words
import generate_espeak_ipa as g2p
from build_reports import sha256_file, write_json
from generate_rhyme_db import build_database
from ipa_audit import audit_file
from prepare_entries import prepare, prepared_paths
from raw_entries import extract, raw_paths

SCRIPTS = Path(__file__).resolve().parent
OUT = SCRIPTS.parent / "out"
LANGUAGES = ("en", "de", "tr")


class Stages:
    """Within a locked build, hash each immutable input only once."""

    def __init__(self):
        self.digests = {}

    def hashes(self, paths):
        result = {}
        for path in paths:
            if path not in self.digests:
                self.digests[path] = sha256_file(path)
            result[str(path)] = self.digests[path]
        return result

    def checkpoint(self, directory, name, inputs, outputs, modules, options=None):
        path = directory / "reports" / "state" / f"{name}.json"
        files = [SCRIPTS / f"{module}.py" for module in ("pipeline", "build_reports", *modules)]
        signature = dict(inputs=self.hashes([*inputs, *files]), options=options,
                         python=sys.version, sqlite=sqlite3.sqlite_version)
        try:
            saved = json.loads(path.read_text())
            if saved["signature"] == signature and saved["outputs"] == self.hashes(outputs):
                print(f"Reusing {directory.name}/{name}", flush=True)
                return None
        except (OSError, ValueError, KeyError, TypeError):
            pass
        print(f"Building {directory.name}/{name}", flush=True)

        def commit():
            for output in outputs:
                self.digests.pop(output, None)
            write_json(path, dict(signature=signature, outputs=self.hashes(outputs)))

        return commit

    def run(self, action, directory, name, *args):
        try:
            finish = self.checkpoint(directory, name, *args)
            if finish:
                action()
                finish()
        except Exception as exc:
            raise RuntimeError(f"{directory.name}/{name}: {exc}") from exc


def engine_inputs(executable):
    version = subprocess.check_output([executable, "--version"], text=True, stdin=subprocess.DEVNULL)
    files = [Path(executable)]
    # Distribution builds commonly keep the synthesis implementation in a
    # shared library while shipping the same small CLI across updates.
    with Path(executable).open("rb") as binary:
        elf = binary.read(4) == b"\x7fELF"
    if elf and shutil.which("ldd"):
        linked = subprocess.run(["ldd", executable], text=True, capture_output=True)
        for path in re.findall(r"(/[^\n]+?)\s+\(0x[0-9a-fA-F]+\)", linked.stdout):
            library = Path(path)
            if library.is_file():
                files.append(library)
    data = re.search(r"Data at:\s*(.+)", version)
    if data:
        directory = Path(data[1].strip())
        if not directory.is_dir():
            raise ValueError(f"missing eSpeak data directory: {directory}")
        files.extend(sorted(path for path in directory.rglob("*") if path.is_file()))
    return files, version


def build(args):
    cache = Stages()
    dirs = {lang: OUT / lang for lang in LANGUAGES}
    cleaning_code = ["prepare_entries", "clean_rhyme_words", "clean_rhyme_ipa",
                     "ipa_tokens", "extract_ipa", "raw_entries", "progress"]
    if args.verified_archive_sha256:
        # The shell hashes the actual files under the build lock. Reuse that
        # in-process evidence to avoid a second full read of every archive.
        if len(args.verified_archive_sha256) != len(args.archive or []):
            raise ValueError("one verified digest per downloaded archive is required")
        cache.digests.update(zip(args.archive, args.verified_archive_sha256))
    if args.archive:
        pending = {}
        for lang, directory in dirs.items():
            directory.mkdir(parents=True, exist_ok=True)
            report = directory / "reports/reading/report.json"
            finish = cache.checkpoint(directory, "extract", args.archive,
                                      [*raw_paths(directory, lang), report], ["raw_entries", "extract_ipa", "progress"],
                                      [str(path) for path in args.archive])
            if finish:
                pending[lang] = finish
        if pending:
            counts = extract(args.archive, {lang: dirs[lang] for lang in pending})
            for lang, finish in pending.items():
                write_json(dirs[lang] / "reports/reading/report.json",
                           dict(records_read=counts["records_read"], bad_json_records=counts["bad_json_records"],
                                invalid_records=counts["invalid_records"], matching_records=counts[lang],
                                archives=cache.hashes(args.archive)))
                finish()

    executable = g2p.resolve_espeak("espeak-ng")
    engine, version = engine_inputs(executable)
    for lang, directory in dirs.items():
        paths = prepared_paths(directory, lang)
        if args.archive:
            cache.run(lambda: prepare(directory, lang), directory, "clean", raw_paths(directory, lang),
                      list(paths.values()), cleaning_code)
        else:
            for source, name in (("ipa", "wiktionary"), ("espeak_ipa", "espeak")):
                original = directory / f"wordlist_{lang}_{source}.txt"
                cleaned = directory / f"wordlist_{lang}_{name}_words_cleaned.txt"
                cache.run(lambda: words.clean_wordlist(original, cleaned, lang), directory, f"words_{name}",
                          [original], list(words.output_paths(cleaned).values()), ["clean_rhyme_words", "ipa_tokens", "progress"])

    if args.archive:
        for lang, directory in dirs.items():
            missing = prepared_paths(directory, lang)["missing"]
            generated = directory / f"wordlist_{lang}_espeak_ipa.txt"
            report = directory / "reports/ipa_generation/report.json"

            empty_words = report.with_name("empty_words.txt")

            def generate():
                count = g2p.validate_input(missing)
                report.parent.mkdir(parents=True, exist_ok=True)
                part = empty_words.with_suffix(".txt.part")
                try:
                    with part.open("w", encoding="utf-8") as audit:
                        made, empty = g2p.generate_language(executable, missing, generated, lang, 10000, count, audit)
                    part.replace(empty_words)
                finally:
                    part.unlink(missing_ok=True)
                write_json(report, dict(input_words=count, generated_words=made, empty_ipa_words=empty))

            cache.run(generate, directory, "generate", [missing, *engine], [generated, report, empty_words],
                      ["generate_espeak_ipa", "progress"], version)

    databases = []
    for lang, directory in dirs.items():
        paths = prepared_paths(directory, lang)
        stem = f"wordlist_{lang}_"
        machine = directory / (stem + ("espeak_ipa.txt" if args.archive else "espeak_words_cleaned.txt"))
        source = directory / (stem + "rhyme_eligible.txt")
        generated = directory / (stem + "espeak_rhyme_eligible.txt")
        repairs = paths["repairs"] if args.archive else None
        inputs = [paths["clean"], machine, *engine]
        if repairs:
            inputs.append(repairs)
        cache.run(lambda: ipa.clean_ipa_wordlists(paths["clean"], machine, source, generated, lang, executable,
                  replace_extreme_mismatches=args.replace_extreme_mismatches, extreme_distance=args.extreme_distance,
                  repair_requests=repairs), directory, "validate", inputs,
                  list(ipa.output_paths(source, generated).values()),
                  ["clean_rhyme_ipa", "clean_rhyme_words", "ipa_tokens", "generate_espeak_ipa", "progress"],
                  [version, args.extreme_distance, args.replace_extreme_mismatches])

        report_dir = directory / "reports/cleaning"
        reports = [ipa.output_paths(source, generated)["report"]]
        if args.archive:
            reports.append(report_dir / "entries_report.json")
        else:
            for name in ("wiktionary", "espeak"):
                reports.append(words.output_paths(directory / (stem + name + "_words_cleaned.txt"))["report"])

        def summarize():
            write_json(report_dir / "report.json", dict(
                release_version=args.release_version, extreme_distance=args.extreme_distance,
                comparison_mode="replace_extreme" if args.replace_extreme_mismatches else "report_only",
                step_reports={path.name: json.loads(path.read_text()) for path in reports},
            ))

        cache.run(summarize, directory, "summary", reports, [report_dir / "report.json"], [],
                  [args.release_version, args.extreme_distance, args.replace_extreme_mismatches])

    for lang, directory in dirs.items():
        audit_dir = directory / "reports" / "ipa_reversal"
        for origin in ("wiktionary", "espeak"):
            raw = bool(args.archive) and origin == "wiktionary"
            if raw:
                original = raw_paths(directory, lang)[0]
            else:
                suffix = "_espeak" if origin == "espeak" else ""
                original = directory / f"wordlist_{lang}{suffix}_ipa.txt"
            scope = "canonical" if origin == "wiktionary" and not raw else "source"
            report = audit_dir / f"{origin}_{scope}.json"
            cache.run(lambda: audit_file(original, report, lang, origin, raw), directory,
                      f"audit_{origin}_{scope}", [original], [report],
                      ["ipa_reversal", "ipa_audit", "ipa_tokens", "progress"], [origin, raw])
        for suffix in ("", "_espeak"):
            source = directory / f"wordlist_{lang}{suffix}_rhyme_eligible.txt"
            database = directory / f"{lang}{suffix}_{args.release_version}.db"
            source_format = "espeak" if suffix else "wiktionary"
            audit = audit_dir / f"{source_format}_eligible.json"
            cache.run(lambda: build_database(source, database, lang, args.release_version, source_format, audit,
                                             executable if suffix else None), directory,
                      f"database{suffix}", [source, *(engine if suffix else [])], [database, audit],
                      ["generate_rhyme_db", "ipa_reversal", "ipa_audit", "ipa_tokens", "progress",
                       *(["generate_espeak_ipa"] if suffix else [])],
                      [args.release_version, source_format, version if suffix else None])
            databases.append(database)

    cache.run(lambda: subprocess.run([sys.executable, SCRIPTS / "package_release.py",
                                      "--release-version", args.release_version], check=True),
              OUT, "package", databases, [*(Path(f"{db}.gz") for db in databases), OUT / "SHA256SUMS"],
              ["package_release"], [args.release_version, zlib.ZLIB_RUNTIME_VERSION])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, action="append")
    parser.add_argument("--verified-archive-sha256", action="append", help=argparse.SUPPRESS)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--replace-extreme-mismatches", action="store_true")
    parser.add_argument("--extreme-distance", type=float, default=0.8)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", args.release_version):
        parser.error("invalid release version")
    if not 0 < args.extreme_distance <= 1:
        parser.error("extreme-distance must be in (0, 1]")
    build(args)

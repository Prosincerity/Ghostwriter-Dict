"""Prepare normalized source, missing-IPA and repair inputs before invoking eSpeak."""

import json
import os
import sqlite3
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

from clean_rhyme_ipa import clean_pronunciation
from clean_rhyme_words import POLICY_VERSION, clean_headword
from extract_ipa import is_usable_ipa
from generate_rhyme_db import STRESS_MARKERS
from raw_entries import raw_paths


def prepared_paths(directory, lang):
    reports = directory / "reports" / "cleaning"
    stem = directory / f"wordlist_{lang}"
    return {
        "source": Path(f"{stem}_ipa.txt"),
        "clean": Path(f"{stem}_wiktionary_words_cleaned.txt"),
        "missing": Path(f"{stem}_noipa.txt"),
        "repairs": reports / "repair_requests.jsonl",
        "audit": reports / "entries.jsonl",
        "summary": reports / "summary.md",
    }


def prepare(directory, lang):
    paths = prepared_paths(directory, lang)
    paths["audit"].parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(input=0, removed=0, retained=0, merged=0)
    reasons = Counter()
    with ExitStack() as cleanup:
        staging = directory / "prepare.sqlite.part"
        staging.unlink(missing_ok=True)
        cleanup.callback(staging.unlink, missing_ok=True)
        db = sqlite3.connect(staging)
        cleanup.callback(db.close)
        db.executescript("""
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE words(word TEXT PRIMARY KEY) WITHOUT ROWID;
            CREATE TABLE ipa(word TEXT, kind TEXT, value TEXT,
                             PRIMARY KEY(word,kind,value)) WITHOUT ROWID;
        """)
        with ExitStack() as streams:
            out = {}
            for name, path in paths.items():
                part = Path(f"{path}.part")
                cleanup.callback(part.unlink, missing_ok=True)
                out[name] = streams.enter_context(part.open("w", encoding="utf-8"))

            def audit(word, reason, **details):
                print(json.dumps(dict(word=word, reason=reason, **details)), file=out["audit"])

            for path in raw_paths(directory, lang):
                with path.open(encoding="ascii") as entries:
                    for line in entries:
                        original, fields = json.loads(line)
                        counts["input"] += 1
                        if not isinstance(original, str):
                            audit(original, "missing_text_headword")
                            reasons["missing_text_headword"] += 1
                            counts["removed"] += 1
                            continue
                        word, changes, rejected = clean_headword(original, lang)
                        reasons.update(changes)
                        if changes:
                            audit(original, "normalized", normalized=word, changes=changes)
                        if rejected:
                            audit(original, **rejected, normalized=word)
                            reasons[rejected["reason"]] += 1
                            counts["removed"] += 1
                            continue
                        inserted = db.execute("INSERT OR IGNORE INTO words VALUES (?)", (word,)).rowcount
                        counts["retained" if inserted else "merged"] += 1
                        if not inserted:
                            audit(original, "merged_duplicate", normalized=word)
                        for ipa in fields:
                            if not is_usable_ipa(ipa):
                                reasons["unusable_ipa_field"] += 1
                                audit(word, "unusable_ipa_field", ipa=ipa)
                                continue
                            inserted = db.execute("INSERT OR IGNORE INTO ipa VALUES (?, 'source', ?)",
                                                  (word, ipa)).rowcount
                            if not inserted:
                                audit(word, "duplicate_ipa", ipa=ipa)
                                reasons["duplicate_ipa"] += 1
                                continue
                            valid, transformations, rejects = clean_pronunciation(ipa, lang)
                            if transformations or (valid and valid != [ipa]):
                                audit(word, "normalize_ipa", ipa=ipa, variants=valid, changes=transformations)
                                reasons.update(transformations or ["normalize_ipa"])
                            for variant in valid:
                                if any(mark in variant for mark in STRESS_MARKERS):
                                    db.execute("INSERT OR IGNORE INTO ipa VALUES (?, 'clean', ?)", (word, variant))
                                else:
                                    rejects.append(dict(reason="missing_stress_mark", details=dict(candidate=variant)))
                            for rejection in rejects:
                                audit(word, ipa=ipa, **rejection)
                                reasons[rejection["reason"]] += 1
                                print(json.dumps([word, ipa, rejection]), file=out["repairs"])

            for word, in db.execute("SELECT word FROM words ORDER BY word"):
                groups = {}
                for kind, value in db.execute("SELECT kind,value FROM ipa WHERE word=? ORDER BY kind,value", (word,)):
                    groups.setdefault(kind, []).append(value)
                if not groups:
                    print(word, file=out["missing"])
                for kind, values in groups.items():
                    print(word, json.dumps(values, ensure_ascii=False, separators=(",", ":")),
                          sep="\t", file=out[kind])
            print(f"# {lang} cleaning summary\n\nPolicy `{POLICY_VERSION}`\n", file=out["summary"])
            for key, value in (*counts.items(), *sorted(reasons.items())):
                print(f"- {key}: {value}", file=out["summary"])
            print("\nSee [entry decisions](entries.jsonl) and [repair requests](repair_requests.jsonl).",
                  file=out["summary"])
        for path in paths.values():
            os.replace(f"{path}.part", path)

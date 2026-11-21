"""Prepare normalized source, missing-IPA and repair inputs before invoking eSpeak."""

import html
import json
import sqlite3
from collections import Counter
from contextlib import ExitStack
from itertools import groupby
from pathlib import Path

from build_reports import publish_files
from clean_rhyme_ipa import clean_pronunciation
from clean_rhyme_words import POLICY_VERSION, WordCleanupProgress, clean_headword
from extract_ipa import is_usable_ipa
from ipa_tokens import STRESS_MARKERS
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
        "report": reports / "entries_report.json",
        "summary": reports / "summary.md",
    }


def prepare(directory, lang):
    paths = prepared_paths(directory, lang)
    paths["audit"].parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(input=0, removed=0, retained=0, merged=0)
    reasons = Counter()
    affected_entries = Counter()
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
            CREATE TABLE excluded(reason TEXT, original TEXT);
            CREATE TABLE ipa(word TEXT, kind TEXT, value TEXT,
                             PRIMARY KEY(word,kind,value)) WITHOUT ROWID;
        """)
        with ExitStack() as streams:
            out = {}
            for name, path in paths.items():
                part = Path(f"{path}.part")
                cleanup.callback(part.unlink, missing_ok=True)
                out[name] = streams.enter_context(part.open("w", encoding="utf-8"))

            entry_rules = set()

            def audit(word, reason, **details):
                for rule in details.get("changes") or [reason]:
                    if rule not in entry_rules:
                        affected_entries[rule] += 1
                        entry_rules.add(rule)
                print(json.dumps(dict(word=word, reason=reason, **details)), file=out["audit"])

            inputs = raw_paths(directory, lang)
            progress = WordCleanupProgress(sum(path.stat().st_size for path in inputs))
            cleanup.callback(progress.finish)
            consumed = 0
            for path in inputs:
                with path.open(encoding="ascii") as entries:
                    for line in entries:
                        entry_rules.clear()
                        consumed += len(line)
                        if counts["input"] % 1000 == 0:
                            progress.update(consumed, counts["input"], counts["retained"])
                        original, fields = json.loads(line)
                        counts["input"] += 1
                        if not isinstance(original, str):
                            db.execute("INSERT INTO excluded VALUES (?, ?)", ("missing_text_headword", json.dumps(original)))
                            audit(original, "missing_text_headword")
                            reasons["missing_text_headword"] += 1
                            counts["removed"] += 1
                            continue
                        word, changes, rejected = clean_headword(original, lang)
                        reasons.update(changes)
                        if changes:
                            audit(original, "normalized", normalized=word, changes=changes)
                        if rejected:
                            db.execute("INSERT INTO excluded VALUES (?, ?)", (rejected["reason"], json.dumps(original)))
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
                                    new_variant = db.execute("INSERT OR IGNORE INTO ipa VALUES (?, 'clean', ?)",
                                                             (word, variant)).rowcount
                                    if not new_variant:
                                        audit(word, "duplicate_normalized_ipa", ipa=ipa, normalized=variant)
                                        reasons["duplicate_normalized_ipa"] += 1
                                else:
                                    rejects.append(dict(reason="missing_stress_mark", details=dict(candidate=variant)))
                            for rejection in rejects:
                                audit(word, ipa=ipa, **rejection)
                                reasons[rejection["reason"]] += 1
                                print(json.dumps([word, ipa, rejection]), file=out["repairs"])

            rows = db.execute("""
                SELECT words.word, ipa.kind, ipa.value FROM words LEFT JOIN ipa USING(word)
                ORDER BY words.word, ipa.kind, ipa.value
            """)
            for (word, kind), variants in groupby(rows, key=lambda row: row[:2]):
                if kind is None:
                    print(word, file=out["missing"])
                else:
                    values = [row[2] for row in variants]
                    print(word, json.dumps(values, ensure_ascii=False, separators=(",", ":")),
                          sep="\t", file=out[kind])
            print(f"# {lang} cleaning summary\n\nPolicy `{POLICY_VERSION}`\n", file=out["summary"])
            for key, value in counts.items():
                print(f"- {key}: {value}", file=out["summary"])
            print("\n## Input entries affected by each rule\n", file=out["summary"])
            for rule, number in sorted(affected_entries.items()):
                print(f"- {rule}: {number}", file=out["summary"])
            print("\nSee [entry decisions](entries.jsonl) and [repair requests](repair_requests.jsonl).",
                  file=out["summary"])
            json.dump(dict(policy_version=POLICY_VERSION, counts=counts, rule_counts=reasons, affected_entries=affected_entries),
                      out["report"], indent=2, sort_keys=True)
            excluded = db.execute("SELECT reason,original FROM excluded ORDER BY reason,original")
            for reason, entries in groupby(excluded, key=lambda row: row[0]):
                print(f"\n## {reason}\n", file=out["summary"])
                out["summary"].write("<p>")
                for index, (_, encoded) in enumerate(entries):
                    if index:
                        out["summary"].write(", ")
                    value = json.dumps(json.loads(encoded), ensure_ascii=False)
                    printable = value.encode("utf-8", "backslashreplace").decode()
                    out["summary"].write(html.escape(printable, quote=False))
                print("</p>", file=out["summary"])
        publish_files([(Path(f"{path}.part"), path) for path in paths.values()])

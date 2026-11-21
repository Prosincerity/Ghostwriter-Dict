"""Audit raw Kaikki projections or generated IPA wordlists without changing them."""

import argparse
import json
from collections import Counter
from pathlib import Path

from build_reports import write_json
from ipa_reversal import SOURCES, RoundTripError, analyze_source, verified_key
from ipa_tokens import LANGUAGES
from progress import ProgressBar


class Audit:
    def __init__(self, lang, source, input_path, scope):
        self.input_name = input_path.name
        self.scope = scope
        self.lang = lang
        self.source = source
        self.records = 0
        self.unique = set()
        self.tokens = Counter()
        self.segments = Counter()
        self.notation = Counter()
        self.issues = {}
        self.records_with_issues = 0
        self.nontext = 0
        self.failures = []
        self.evidence = Counter()
        self.evidence_examples = {}
        self.engine = None

    def record(self, word, ipa, analysis, evidence, reference=None):
        self.evidence[evidence] += 1
        if evidence not in self.evidence_examples:
            self.evidence_examples[evidence] = {"word": word, "ipa": ipa, "tokens": analysis.tokens}
            if reference is not None:
                self.evidence_examples[evidence]["engine_ipa"] = reference.strip()
        self.records += 1
        self.unique.add(ipa)
        self.tokens.update(analysis.tokens)
        self.segments.update(analysis.segments)
        if analysis.issues or evidence == "engine_spelling_differs":
            self.records_with_issues += 1
        for reason, spelling in analysis.issues:
            group = self.issues.setdefault(reason, {})
            if spelling not in group:
                group[spelling] = {"count": 0, "codepoints": [f"U+{ord(c):04X}" for c in spelling],
                                   "example": [word, ipa]}
            group[spelling]["count"] += 1
        self.notation.update(c for c in ipa if c.isspace() or c in "/[]()ˈˌ.‿͜͡\u200d_")

    def report(self):
        return {
            "key_format": 2, "language": self.lang, "source": self.source,
            "input_file": self.input_name, "scope": self.scope,
            "pronunciations": self.records, "unique_ipa": len(self.unique),
            "nontext_fields": self.nontext, "records_with_diagnostics": self.records_with_issues,
            "serialized_tokens": sum(self.tokens.values()), "unique_tokens": len(self.tokens),
            "engine": self.engine, "boundary_evidence": self.evidence,
            "boundary_examples": self.evidence_examples,
            "phonetic_segments": sum(self.segments.values()), "unique_segments": len(self.segments),
            "token_frequencies": self.tokens, "segment_frequencies": self.segments,
            "multicodepoint_token_frequencies": {t: n for t, n in self.tokens.items() if len(t) > 1},
            "notation_frequencies": self.notation, "diagnostics": self.issues,
            "round_trip_failures": self.failures,
        }


def audit_file(path, destination, lang, source, raw):
    if path.resolve() in (destination.resolve(), Path(f"{destination}.part").resolve()):
        raise ValueError("audit report and input must have distinct paths")
    audit = Audit(lang, source, path, "raw_fields" if raw else "source_wordlist")
    progress = ProgressBar(f"Auditing {lang}/{source}", path.stat().st_size, unit="bytes")
    current = 0
    try:
        with path.open("rb") as lines:
            for number, line in enumerate(lines, 1):
                current += len(line)
                if number % 10000 == 0:
                    progress.show(current)
                if raw:
                    word, values = json.loads(line)
                else:
                    word, encoded = line.decode("utf-8").rstrip("\r\n").split("\t")
                    values = json.loads(encoded)
                for ipa in values:
                    if not isinstance(ipa, str):
                        audit.nontext += 1
                        continue
                    parsed, evidence = analyze_source(ipa, lang, source)
                    audit.record(word, ipa, parsed, evidence)
                    try:
                        verified_key(ipa, parsed)
                    except RoundTripError as error:
                        audit.failures.append(dict(language=lang, source=source, word=word,
                                                   line=number, **error.details))
        progress.show(current)
    finally:
        progress.finish()
    report = audit.report()
    try:
        write_json(destination, report)
    except UnicodeEncodeError:
        # Raw JSON fields can contain lone surrogate escapes. Keep their code
        # points inspectable even when the field cannot be encoded as UTF-8.
        write_json(destination, report, ensure_ascii=True)
    if audit.failures:
        raise ValueError(f"{len(audit.failures)} round-trip failures; see {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--lang-code", required=True, choices=LANGUAGES)
    parser.add_argument("--source", required=True, choices=SOURCES)
    parser.add_argument("--raw-jsonl", action="store_true")
    args = parser.parse_args()
    audit_file(args.input, args.report, args.lang_code, args.source, args.raw_jsonl)

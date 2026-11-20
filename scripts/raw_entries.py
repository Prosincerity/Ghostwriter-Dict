"""Lossless projection of selected Wiktextract entries, streamed once across editions."""

import json
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

from build_reports import publish_files
from extract_ipa import ProgressBar, input_position, iter_ipa_fields, open_jsonl


def raw_paths(directory, lang):
    return [directory / f"raw_{lang}_{kind}.jsonl" for kind in ("ipa", "noipa")]


def extract(archives, directories):
    paths = {lang: raw_paths(directory, lang) for lang, directory in directories.items()}
    counts = Counter()
    words = {lang: set() for lang in paths}
    progress = ProgressBar(sum(path.stat().st_size for path in archives))
    completed = 0
    with ExitStack() as cleanup:
        with ExitStack() as streams:
            outputs = {}
            for lang, pair in paths.items():
                outputs[lang] = []
                for path in pair:
                    part = Path(f"{path}.part")
                    cleanup.callback(part.unlink, missing_ok=True)
                    outputs[lang].append(streams.enter_context(part.open("w", encoding="ascii")))
            for archive in archives:
                with open_jsonl(archive) as source:
                    for line in source:
                        counts["records_read"] += 1
                        if counts["records_read"] % 25000 == 0:
                            progress.update(completed + input_position(source, archive),
                                            counts["records_read"], {k: len(v) for k, v in words.items()})
                        try:
                            entry = json.loads(line)
                        except ValueError:
                            counts["bad_json_records"] += 1
                            continue
                        if not isinstance(entry, dict):
                            counts["invalid_records"] += 1
                            continue
                        lang = entry.get("lang_code")
                        if not isinstance(lang, str) or lang not in outputs:
                            continue
                        word = entry.get("word")
                        fields = list(iter_ipa_fields(entry.get("sounds")))
                        if progress.stream.isatty() and isinstance(word, str):
                            words[lang].add(word)
                        print(json.dumps([word, fields], separators=(",", ":")),
                              file=outputs[lang][int(not fields)])
                        counts[lang] += 1
                completed += archive.stat().st_size
                progress.update(completed, counts["records_read"], {k: len(v) for k, v in words.items()})
        publish_files([(Path(f"{path}.part"), path) for pair in paths.values() for path in pair])
    progress.finish()
    return counts

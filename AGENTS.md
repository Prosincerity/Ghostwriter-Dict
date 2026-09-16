# AGENTS.md

## Purpose and layout

This repository builds English, German, and Turkish pronunciation data for
Ghostwriter from Kaikki/Wiktextract, fills missing IPA with eSpeak NG, cleans
the data for product use, and creates versioned SQLite rhyme indexes. Read
`README.md` before substantial changes.

- `scripts/download_and_process.sh`: update Kaikki archives and extract IPA.
- `scripts/extract_ipa.py`: merge languages across dumps and write canonical
  Wiktionary IPA/no-IPA lists.
- `scripts/generate_espeak_ipa.py`: generate IPA for no-IPA words.
- `scripts/clean_rhyme_wordlist.py`: create audited rhyme-eligible lists.
- `scripts/generate_rhyme_db.py`: build per-language, per-source SQLite files.
- `tests/`: small synthetic tests; `tests/scripts/run_rhyme_smoke_test.py` is
  the opt-in deterministic real-data smoke test.
- `raw/`: large archives and ETag sidecars.
- `out/<lang>/`: generated wordlists, databases, and smoke artifacts;
  reports and JSON/JSONL audit files live in `out/<lang>/reports/`.

## Safety and implementation conventions

- Do not inspect, search, print, hash, or copy `raw/` or `out/` unless the user
  explicitly requests it. Exclude both from recursive searches.
- Stream `.jsonl.gz` archives; never decompress them to disk.
- Do not commit `raw/`, `out/`, partial files, generated data, or Python caches.
- Keep only current raw archives and wordlists. Only finished SQLite releases
  are intended for versioning.
- Validate changes with synthetic fixtures, not the full pipeline, unless the
  user explicitly requests a real build or audit.
- Use the Python standard library; no third-party Python dependencies.
- Resolve repository paths relative to scripts so commands work from any CWD.
- Write downloads and generated artifacts through `.part` files, then replace
  atomically. Failures must preserve the previous complete artifact.
- Long operations use a single updating progress line. Extraction reports
  compressed bytes, records, and unique words without a pre-count pass; eSpeak
  reports exact processed/total words.

## Canonical extraction rules

- Search every supplied Wiktionary dump for every requested language. Route
  only by top-level `lang_code` (`en`, `de`, `tr`), never human-readable `lang`.
- Normalize words and IPA to Unicode NFC before deduplication; capitalization
  is significant.
- Canonical lists preserve slang, phrases, punctuation, digits, and emoji.
  `--latin-headwords-only` rejects non-Latin letters but is not an
  alphabet-only product filter.
- Read `sounds[].ipa` and `sounds[].audio-ipa`; retain distinct variants and
  remove exact duplicates.
- Reject empty IPA and complete placeholders such as `[...]`, `[…]`, `?`,
  `/.../`, and `/…/`. Valid square-bracket and slash delimiters remain.
- Preserve eSpeak's adaptive batch isolation: anomalous multi-line output must
  not shift later pronunciations.

Canonical IPA files use one UTF-8 TSV row per word and a compact JSON array:

```text
out/<lang>/wordlist_<lang>_ipa.txt
out/<lang>/wordlist_<lang>_espeak_ipa.txt
hammer\t["/ˈhæmə/","/ˈhæmɚ/"]
```

Do not replace the JSON array with repeated word rows or a custom IPA
delimiter.

`out/<lang>/wordlist_<lang>_noipa.txt` contains one unique word per line only
when no supplied dump provides usable IPA. Keep Wiktionary and eSpeak outputs
separate to preserve provenance.

## Rhyme-product cleanup

Never modify canonical lists in place. `rhyme-cleanup-v8` creates
`wordlist_<lang>_rhyme_eligible.txt` or
`wordlist_<lang>_espeak_rhyme_eligible.txt` and applies these rules:

- English and German allow Latin-script letters, accents, and ligatures except
  click letters `ǀǁǂǃ` and r rotunda `ꝛ`/`Ꝛ`. Turkish allows its 29 letters,
  `ÂâÎîÛû`, and `QqWwXx`. All languages allow ASCII digits.
- Normalize `’`, `‘`, and `ʼ` to `'`; Unicode dash connectors to `-`;
  subscript digits to ASCII; and remove soft hyphens.
- The normalized headword must be one token containing only the language's
  accepted letters, ASCII digits, printable ASCII keyboard punctuation, or
  the Hawaiian ʻokina (`U+02BB`). Accept forms such as `'cause`, `Hawaiʻian`,
  `AC/DC`, `*NSYNC`, and `100%`. Reject all spaces and other spacing
  characters, Braille, dotted-circle notation, enclosed letters, other
  non-ASCII symbols, and emoji.
- Split unambiguous `~` IPA alternatives; expand balanced non-nested optional
  groups to at most eight variants; normalize IPA `'` to `ˈ` and `·` to `.`.
- Reject IPA containing controls, incomplete ellipses, ambiguous commas,
  malformed delimiters, mixed uppercase/SAMPA or orthographic notation, Greek
  `α`/`ε`, Turkish dotless `ı`, or remaining unknown tokens.
- Validate each pronunciation independently. Keep valid siblings and omit a
  word only if none survive. Merge and deduplicate IPA when normalized
  headwords collide.

Every cleanup output has atomic audit files in `out/<lang>/reports/`: grouped
pronunciation rejections (`*_rejected.json`), grouped word rejections
(`*_rejected_words.json`), IPA changes (`*_changes.jsonl`), word changes
(`*_word_changes.jsonl`), and a count report (`*_report.json`). Rejection JSON
is pretty-printed with one IPA or word per line under each reason. IPA groups
also retain detailed entries with original values, normalized values where
applicable, reasons, and rejected character code points. Record the cleanup
policy version in reports and release metadata.

## SQLite rhyme indexes

Build one database per language and source. Encode the traceable Kaikki dates
for all three merged editions in the filename, for example:

```text
en_kaikki-en20260902-de20260901-tr20260901.db
en_espeak_kaikki-en20260902-de20260901-tr20260901.db
```

An eSpeak database inherits the release of its no-IPA source. Do not add
language or release columns; provenance and version belong in the filename.
Production databases consume only rhyme-eligible lists and use exactly:

```sql
CREATE TABLE dictionary (
    word TEXT NOT NULL,
    ipa TEXT NOT NULL,
    ipa_reversed TEXT NOT NULL,
    assonance_reversed TEXT NOT NULL,
    PRIMARY KEY (word, ipa)
) WITHOUT ROWID;

CREATE INDEX idx_ipa_reversed ON dictionary(ipa_reversed);
CREATE INDEX idx_assonance_reversed ON dictionary(assonance_reversed);
```

Expand each IPA array to one `(word, ipa)` row. Tokenize with the audited
language inventory; never reverse raw characters or silently accept/drop
unknown symbols. Derived values are space-delimited token sequences:

- `ipa_reversed`: complete IPA tokens reversed.
- `assonance_reversed`: vowel-only tokens reversed.

Consumers derive rhyme prefixes from `ipa_reversed`; do not persist a separate
rhyme key. Do not add consonance keys, fixed tails, syllable data, or
syllabification. Create indexes after bulk insertion.

Compound identity rhymes remain a documented limitation. A future on-device
filter may reject long matching tails that are standalone dictionary words;
do not implement that policy in this build stage.

## Validation and licensing

Run:

```bash
python3 -B -m unittest discover -s tests -v
bash -n scripts/download_and_process.sh  # when shell code changes
```

Tests must not require real datasets or eSpeak; use a fake executable.
Original code and tests are MIT (`LICENSE-CODE`). Downloaded, transformed, and
generated dictionary data is CC BY-SA 4.0 (`LICENSE-DATA.md`). Keep attribution
and transformation notices current, and never place dictionary data under MIT.

Update documentation, tests, and provenance when behavior, formats, sources,
or generated artifacts change. Preserve existing CLI behavior unless a change
is documented and tested.

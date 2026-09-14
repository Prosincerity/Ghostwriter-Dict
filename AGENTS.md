# AGENTS.md

## Project purpose

This repository builds English, German, and Turkish word/pronunciation data for
the Ghostwriter application. It downloads Kaikki/Wiktextract JSONL dumps,
extracts and deduplicates Wiktionary pronunciations, generates missing IPA with
eSpeak NG, and builds versioned SQLite rhyme indexes.

Read `README.md` before making substantial changes.

## Repository layout

- `scripts/download_and_process.sh` checks Kaikki `ETag` values, downloads or
  updates the current raw archives, and runs the Wiktionary extractor.
- `scripts/extract_ipa.py` merges selected language entries from all supplied
  dumps and creates Wiktionary IPA/no-IPA wordlists.
- `scripts/generate_espeak_ipa.py` calls eSpeak NG for words without a
  Wiktionary pronunciation.
- `scripts/generate_rhyme_db.py` builds per-language, per-source SQLite rhyme
  indexes from the pronunciation wordlists.
- `tests/` contains synthetic tests that do not require the real datasets or an
  installed eSpeak NG binary.
- `raw/` contains multi-gigabyte source archives and HTTP `ETag` sidecars.
- `out/` contains generated wordlists and, eventually, the SQLite database.

## Large-file safety

- Do not read, search, print, hash, copy, or inspect files under `raw/` or
  `out/` unless the user explicitly asks for that operation.
- Never use unrestricted recursive searches that can enter `raw/` or `out/`.
  Use exclusions such as `rg -g '!raw/**' -g '!out/**'`.
- Do not decompress the Kaikki archives to disk. Process `.jsonl.gz` files as
  streams.
- Do not commit `raw/`, `out/`, partial downloads, generated wordlists, or
  local Python caches.
- Tests must use small synthetic fixtures in temporary directories.
- Do not run the full download or extraction pipeline merely to validate a code
  change. Use the test suite unless the user explicitly requests a real build.
- Keep only the current raw archives and current outputs. Do not introduce raw
  or wordlist release archives; only the finished SQLite database is intended
  to be versioned.

## Data-routing rules

- A Wiktionary edition is not a language filter. Search every supplied dump for
  every requested language.
- Route entries exclusively by the top-level Wiktextract `lang_code` field:
  `en`, `de`, or `tr`.
- Do not route using the human-readable `lang` field.
- Preserve slang, internet language, abbreviations, phrases, punctuation,
  digits, and emoji.
- The current English/German/Turkish build rejects headwords containing
  non-Latin letters to remove incorrectly tagged Arabic, Cyrillic, Greek, and
  CJK entries. Do not replace this with an alphabet-only filter.
- Normalize words and IPA to Unicode NFC before deduplication.
- Treat capitalization as significant.

## Wordlist formats

Wiktionary IPA files are named:

```text
out/wordlist_en_ipa.txt
out/wordlist_de_ipa.txt
out/wordlist_tr_ipa.txt
```

They contain exactly one UTF-8 TSV row per word. The second field is a compact
JSON array containing all unique pronunciations:

```text
hammer\t["/ˈhæmə/","/ˈhæmɚ/"]
```

Do not change this to repeated word rows or a custom IPA delimiter.

Missing-IPA files are named `wordlist_<lang>_noipa.txt` and contain one unique
word per line. A word belongs there only if no supplied dump provides usable
IPA for it.

eSpeak-generated files are named `wordlist_<lang>_espeak_ipa.txt` and use the
same one-word/JSON-array TSV format. Keep them separate from Wiktionary IPA so
the pronunciation provenance remains identifiable.

## IPA handling

- Read both `sounds[].ipa` and `sounds[].audio-ipa`.
- Preserve distinct IPA variants for a word.
- Remove exact duplicate IPA values.
- Reject empty values and complete placeholders such as `[...]`, `[…]`, `?`,
  `/.../`, and `/…/`.
- Square brackets and slashes are valid IPA delimiters; do not remove them from
  legitimate pronunciations.
- eSpeak NG may emit trailing blank lines or multiple lines for one
  punctuation-heavy entry. Keep the adaptive batch-isolation behavior so an
  anomalous word cannot shift every subsequent pronunciation.

## Progress and atomicity

- Long-running commands should retain single-line terminal progress reporting.
- Extraction progress uses compressed bytes/total plus record and running
  unique-word counts. Do not add a full pre-count pass over the dumps.
- eSpeak progress uses exact processed/total word counts.
- Download replacements through `.part` files and replace the previous archive
  only after successful completion.
- Generate output through `.part` files and atomically replace existing output
  after successful completion.
- A failed update must preserve the previous complete archive or output.

## SQLite rhyme indexes

Build one database per language and pronunciation source, with the Kaikki
release encoded in every filename. Because each wordlist merges three
Wiktionary editions, use a composite release slug containing the English,
German, and Turkish edition dump dates, for example
`en_kaikki-en20260902-de20260901-tr20260901.db` and
`en_espeak_kaikki-en20260902-de20260901-tr20260901.db`.
Keeping the sources separate preserves pronunciation provenance and permits
each file to be shipped independently. An eSpeak database inherits the Kaikki
release version of the no-IPA wordlist used as its input. Do not add a
`language` or release column; the filename identifies the language, source,
and release. Never label an output with a release that cannot be traced to the
source archives' recorded Kaikki metadata.

Each database uses this exact schema:

```sql
CREATE TABLE dictionary (
    word TEXT NOT NULL,
    ipa TEXT NOT NULL,
    ipa_reversed TEXT NOT NULL,
    rhyme_key_reversed TEXT NOT NULL,
    assonance_reversed TEXT NOT NULL,
    PRIMARY KEY (word, ipa)
) WITHOUT ROWID;

CREATE INDEX idx_ipa_reversed ON dictionary(ipa_reversed);
CREATE INDEX idx_rhyme_key_reversed ON dictionary(rhyme_key_reversed);
CREATE INDEX idx_assonance_reversed ON dictionary(assonance_reversed);
```

The wordlist's compact JSON array is expanded into one database row per
`(word, ipa)` pair. Tokenize IPA with the inventory for that language before
deriving the indexed values; never reverse raw characters. Unknown symbols
must be counted and reported rather than silently discarded or accepted as
single-character phonemes.

- `ipa_reversed` is the complete phoneme-token sequence in reverse order,
  joined with one space.
- `rhyme_key_reversed` starts at the vowel immediately after the last primary
  stress marker (`ˈ`) and runs through the end, then is reversed and joined
  with one space. If there is no primary stress, it starts at the last vowel.
  This stress anchoring is required: fixed-length trailing-phoneme matches can
  produce false positives from long shared unstressed suffixes.
- `assonance_reversed` retains only vowel phonemes in their original order,
  then reverses and space-joins them.

Do not add consonance keys, fixed-length tail columns, syllable counts,
syllable boundaries, or inferred syllabification. Indexes are created after
the bulk insert, and database replacement must use a `.part` file followed by
an atomic replace.

Identity rhymes caused by compounds are a known limitation. For example,
German compounds ending in the same standalone morpheme may match across that
entire tail. Do not filter these in the database builder. A future on-device
post-match filter can test whether a long matching tail is itself a standalone
dictionary word.

## Validation

Run the complete lightweight test suite after changes:

```bash
python3 -B -m unittest discover -s tests -v
```

For shell changes, also run:

```bash
bash -n scripts/download_and_process.sh
```

The local development environment may not have eSpeak NG installed. Tests for
the generator must continue to work through a fake executable.

## Licensing

- Original scripts, tests, and software-specific files are MIT licensed. See
  `LICENSE-CODE`.
- Downloaded, transformed, and generated dictionary data is CC BY-SA 4.0. See
  `LICENSE-DATA.md`.
- Keep source attribution and transformation notices accurate when adding new
  datasets or G2P systems.
- Do not place Wiktionary/Kaikki-derived data under the MIT license.

## Change discipline

- Prefer Python standard-library solutions; no third-party Python dependency is
  currently required.
- Keep scripts usable from any working directory by resolving repository paths
  relative to the script location.
- Preserve existing command-line behavior unless a change is documented and
  tested.
- Update `README.md`, tests, and licensing/provenance notes when behavior,
  formats, data sources, or generated artifacts change.

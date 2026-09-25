# Ghostwriter Dictionary Data

Builds English, German, and Turkish pronunciation indexes from
[Kaikki/Wiktextract](https://kaikki.org/dictionary/) data. The pipeline fills
missing IPA with eSpeak NG, cleans words and pronunciations for rhyme search,
and creates SQLite databases.

## Requirements

- Python 3.10+, Bash, and `curl`
- eSpeak NG for IPA generation and cleanup
- Disk space for the archives and generated files; extraction can use several
  gigabytes of memory

The scripts use only the Python standard library. Run them from any directory.
Generated `raw/` and `out/` files are ignored by Git.

## Build

For a complete build, including downloads and six packaged databases:

```bash
./scripts/download_and_process.sh
```

The release name defaults to `kaikki-vYYYYMMDD`, using the English archive's
HTTP `Last-Modified` date. To build from local archives without downloading:

```bash
./scripts/download_and_process.sh \
  --skip-download --release-version kaikki-v20260902
```

On repeat runs, the script checks remote ETags unless downloads are skipped.
It reuses canonical wordlists when the archives, extractor code, options, and
output checksums match. The eSpeak stage similarly checks its input, archives,
generator code, and output before reusing generated IPA.

To clean existing canonical IPA lists and rebuild databases without downloading
or extracting, provide their release name:

```bash
./scripts/clean_and_build.sh --release-version kaikki-v20260902
```

Both scripts repair malformed or unstressed Wiktionary IPA with eSpeak and move
extreme mismatches to the eSpeak output by default. Add `--report-only` to
audit mismatches without moving them. `--extreme-distance` changes the mismatch
threshold. Run either script with `--help` for all options.

The complete build also writes six `*.db.gz` files and `out/SHA256SUMS`. After
`clean_and_build.sh`, package the databases separately if needed:

```bash
python3 scripts/package_release.py --release-version kaikki-v20260902
```

Verify the package from `out/` with `sha256sum -c SHA256SUMS`.

## Outputs

Each `out/<lang>/` directory (`en`, `de`, or `tr`) contains:

| File | Contents |
| --- | --- |
| `wordlist_<lang>_ipa.txt` | Canonical Wiktionary IPA |
| `wordlist_<lang>_noipa.txt` | Words with no usable Wiktionary IPA |
| `wordlist_<lang>_espeak_ipa.txt` | Generated eSpeak IPA |
| `wordlist_<lang>_wiktionary_words_cleaned.txt`, `wordlist_<lang>_espeak_words_cleaned.txt` | Inputs after headword cleanup |
| `wordlist_<lang>_rhyme_eligible.txt`, `wordlist_<lang>_espeak_rhyme_eligible.txt` | Cleaned inputs for SQLite |
| `<lang>_kaikki-vYYYYMMDD.db`, `<lang>_espeak_kaikki-vYYYYMMDD.db` | Versioned SQLite indexes |

Reports live in `out/<lang>/reports/`:

| Directory | Contents |
| --- | --- |
| `downloading/` | Archive fingerprints and download status (complete build) |
| `reading/` | Extraction counts, source and code fingerprints, output checksums |
| `ipa_generation/` | eSpeak counts, reuse status, and source fingerprints |
| `cleaning/` | Word and IPA counts, changes, rejections, comparisons, and summary |

IPA wordlists are UTF-8 TSV with one JSON array per word:

```text
hammer	["/ˈhæmə/","/ˈhæmɚ/"]
```

No-IPA lists contain one word per line. Wiktionary and eSpeak pronunciations
remain in separate files.

## How the data is processed

**Extraction.** Every supplied English, German, and Turkish Wiktionary edition
is searched for all three target languages. Entries are routed by top-level
`lang_code`. Words and IPA are normalized to Unicode NFC and deduplicated.
Headwords with fewer than two uppercase letters are lowercased; multi-capital
spellings are preserved. Both `sounds[].ipa` and `sounds[].audio-ipa` are read.
Canonical lists retain phrases, punctuation, digits, slang, and emoji so the
cleanup stage can audit them.

**IPA generation.** Words without usable Wiktionary IPA go to the matching
eSpeak NG voice. Adaptive batches isolate words that produce multiple output
lines, keeping later pronunciations aligned.

**Product cleanup.** Policy `rhyme-cleanup-v14` writes separate eligible lists
and never edits the canonical lists. It uses a shared curated Latin alphabet
for all three languages. It normalizes apostrophes, dashes, subscript digits,
and soft hyphens, then rejects spaces, unsupported characters, dots, dashes,
and single-letter headwords. Apostrophes may attach to letters or digits;
ampersands and slashes must lie between letters; terminal numbers may take `%`
and terminal letters may take one or more `+` signs.

IPA cleanup validates each variant independently. It expands supported
alternatives and up to eight optional variants, rejects malformed or unknown
phoneme notation, and sends faulty or unstressed Wiktionary variants to eSpeak
for repair. Valid sibling variants stay in the Wiktionary list. Valid
Wiktionary IPA is also compared with one eSpeak result per word; the comparison
report records extreme mismatches and distances above `0.5`. Direct calls to
`clean_rhyme_ipa.py` default to report-only. An extreme mismatch requires at
least five phonemes on each side, four edits, and a normalized distance of at
least `0.8` by default.

**SQLite.** Each `(word, ipa)` pair becomes one row with `ipa_reversed` and
`assonance_reversed` search values. Complete audited phoneme tokens are
reversed; vowels in `assonance_reversed` remain space-delimited. Unknown IPA
tokens stop the build and preserve the previous database. For SQLite prefix
queries, enable `PRAGMA case_sensitive_like = ON`. Compound identity-rhyme
filtering belongs in the on-device consumer.

## Validation

Run the synthetic tests and shell syntax check:

```bash
python3 -B -m unittest discover -s tests -v
bash -n scripts/download_and_process.sh scripts/clean_and_build.sh
```

Tests use temporary fixtures and a fake eSpeak executable. A real-data sample
check is available through `tests/scripts/run_rhyme_smoke_test.py --help`.

## Licensing and attribution

Code and tests are MIT licensed ([LICENSE-CODE](LICENSE-CODE)). Downloaded and
generated dictionary data is CC BY-SA 4.0; see [LICENSE-DATA.md](LICENSE-DATA.md)
for source attribution, transformation notices, and redistribution terms.

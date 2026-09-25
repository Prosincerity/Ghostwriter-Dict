# Ghostwriter Dictionary Data

Builds English, German, and Turkish pronunciation dictionaries from
[Kaikki/Wiktextract](https://kaikki.org/dictionary/), fills missing IPA with
eSpeak NG, applies product filters, and creates SQLite rhyme indexes.

## Requirements

- Python 3.10+ and Bash
- `curl` and eSpeak NG for the complete pipeline
- No third-party Python packages
- Enough disk space for archives and generated artifacts; extraction may use
  several gigabytes of memory

`raw/` and `out/` are generated and ignored by Git. Archives are streamed and
outputs are replaced atomically through `.part` files.

## Build workflows

### Complete rebuild

Download or update the archives, extract canonical lists, generate missing IPA,
apply product cleanup, build all six databases, and package their gzip release
assets with checksums:

```bash
./scripts/download_and_process.sh
```

By default, the release is `kaikki-vYYYYMMDD`, derived from the English
archive's HTTP `Last-Modified` date. To reuse existing archives, provide the
release explicitly:

```bash
./scripts/download_and_process.sh \
  --skip-download \
  --release-version kaikki-v20260902
```

Use `--release-version` without `--skip-download` to override the derived
release. Run the script with `--help` for all options.

### Cleanup and database rebuild only

When the existing Wiktionary and eSpeak IPA lists are available, reapply the
current cleanup policy and rebuild the databases without downloading or
extracting. eSpeak NG is used to replace malformed or unstressed Wiktionary
pronunciations:

```bash
./scripts/clean_and_build.sh \
  --release-version kaikki-v20260902
```

Default cleanup compares valid Wiktionary IPA with eSpeak and writes an audit
without changing those entries. After reviewing the comparison report, an
explicit rebuild can move only extreme mismatches to the eSpeak output:

```bash
./scripts/clean_and_build.sh \
  --release-version kaikki-v20260902 \
  --replace-extreme-mismatches \
  --extreme-distance 0.8
```

Both workflows produce these databases:

```text
out/en/en_kaikki-vYYYYMMDD.db
out/en/en_espeak_kaikki-vYYYYMMDD.db
out/de/de_kaikki-vYYYYMMDD.db
out/de/de_espeak_kaikki-vYYYYMMDD.db
out/tr/tr_kaikki-vYYYYMMDD.db
out/tr/tr_espeak_kaikki-vYYYYMMDD.db
```

The complete rebuild also creates six `*.db.gz` files beside the databases and
`out/SHA256SUMS` with checksums for the compressed files. To package databases
after a cleanup-only rebuild, run:

```bash
python3 scripts/package_release.py --release-version kaikki-v20260902
```

This compresses at gzip level 9 and replaces `out/SHA256SUMS` with six
`sha256sum`-compatible lines. Verify from `out/` with
`sha256sum -c SHA256SUMS`.

## Generated artifacts

Each language directory contains:

| Artifact | Purpose |
| --- | --- |
| `wordlist_<lang>_ipa.txt` | Canonical Wiktionary pronunciations |
| `wordlist_<lang>_noipa.txt` | Words lacking usable Wiktionary IPA |
| `wordlist_<lang>_espeak_ipa.txt` | Generated eSpeak pronunciations |
| `reports/wordlist_<lang>_espeak_ipa_source.json` | Sources and fingerprints used for eSpeak generation |
| `wordlist_<lang>_wiktionary_words_cleaned.txt` | Word-filtered Wiktionary input for IPA validation |
| `wordlist_<lang>_espeak_words_cleaned.txt` | Word-filtered eSpeak input for IPA validation |
| `wordlist_<lang>_rhyme_eligible.txt` | Cleaned Wiktionary input for SQLite |
| `wordlist_<lang>_espeak_rhyme_eligible.txt` | Cleaned eSpeak input for SQLite |
| `reports/wordlist_<lang>_rhyme_eligible_ipa_comparisons.jsonl` | One audited eSpeak comparison per valid Wiktionary IPA |
| `reports/` | Cleanup counts, changes, and grouped rejections |
| `*.db` | Finished versioned rhyme indexes |
| `*.db.gz` | Compressed release assets from the complete rebuild or packaging command |

Pronunciation lists are UTF-8 TSV with one compact JSON array per word:

```text
hammer	["/ˈhæmə/","/ˈhæmɚ/"]
```

The no-IPA lists contain one unique word per line. Wiktionary and eSpeak data
remain separate throughout the pipeline so their provenance stays visible.

## Pipeline behavior

### Extraction

Every English, German, and Turkish Wiktionary edition is searched for all
three target languages. Entries are routed only by top-level `lang_code`, then
normalized to Unicode NFC and deduplicated. Headwords with fewer than two
uppercase letters are lowercased before merging; words with two or more
uppercase letters, such as `ABD`, retain their spelling. For example, `Cat`
and `cat` become one `cat` row containing their distinct IPA values. The
extractor reads both `sounds[].ipa` and `sounds[].audio-ipa` and removes
complete placeholders.

Canonical lists intentionally retain phrases, slang, punctuation, digits, and
emoji for auditing. The product cleanup stage applies the stricter filter.

### eSpeak generation

Words without usable Wiktionary IPA are sent to their matching eSpeak NG
voice. Adaptive batch isolation prevents entries that produce multiple output
lines from shifting later pronunciations. The full build records all three
downloaded archives and each language's no-IPA input fingerprint in
`out/<lang>/reports/wordlist_<lang>_espeak_ipa_source.json`. On later runs,
it reuses the generated eSpeak list when the source report and output still
match; a changed download or missing output triggers generation.

### Product cleanup

Cleanup policy `rhyme-cleanup-v14` has separate word and IPA stages. Word
cleanup uses one shared alphabet for all three languages. It includes curated
English, German, Turkish, French, and common loanword letters, ASCII digits,
and Hawaiian ʻokina (`U+02BB`).

Eligible punctuation is position-sensitive:

- Apostrophes may attach to a letter or digit, including leading elisions such
  as `'Merica`.
- Dots and dashes are rejected anywhere in a headword, including `A.B.D.` and
  `inter-galactic`. Ampersands and slashes may occur between letters, as in
  `rock&roll` and `AC/DC`.
- A percent sign may follow a terminal number (`100%`).
- One or more plus signs may follow a terminal letter (`C++`).

The cleanup rejects spaces, dots, dashes, misplaced or unsupported punctuation,
unsupported symbols, and single-letter headwords. It normalizes typographic
apostrophes, Unicode dashes, subscript digits, and soft hyphens before validation.

The word stage only filters and normalizes headwords; it does not change their
IPA arrays. Both word stages write rejection groups, normalization logs, and
counts under `out/<lang>/reports/`.

The IPA stage expands supported alternatives and optional groups. It rejects
malformed notation, unknown tokens, and values without a phoneme. Each
Wiktionary IPA variant is checked independently. A malformed variant or one
without `ˈ` or `ˌ` is regenerated with eSpeak NG. Valid Wiktionary siblings
stay in the Wiktionary list; successful replacements go to the eSpeak eligible
list. Existing eSpeak IPA is validated but is not regenerated for missing
stress. The IPA report records original values, reasons, generated values,
and counts.

The IPA stage also generates eSpeak IPA once per Wiktionary word and compares
each valid variant using complete phoneme tokens from the audited language
inventory. Its comparison report includes phoneme edit counts, distance
normalized by the longer pronunciation, and stressed rhyme tails. The default
is report-only. An extreme candidate requires at least five phonemes in each
pronunciation, four phoneme edits, and a normalized distance of at least `0.8`.
The threshold is provisional; review the report for each language before using
`--replace-extreme-mismatches`. That option removes only flagged Wiktionary
variants and puts the generated IPA in the eSpeak eligible list. Use
`--extreme-distance` to set a reviewed threshold between zero and one.

Cleanup never changes canonical files. Its reports include grouped word and
IPA rejections, normalization logs, counts, reasons, and policy version.

### SQLite indexes

Each pronunciation becomes one row in this exact schema:

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

IPA is tokenized with a language-specific inventory before reversal.
`ipa_reversed` concatenates all complete tokens in reverse order without
whitespace. `assonance_reversed` contains only reversed vowel tokens and
remains space-delimited.

For SQLite prefix queries, enable `PRAGMA case_sensitive_like = ON`. Rhyme
prefixes are derived from `ipa_reversed`; no separate rhyme-key column is
stored.

## Commands

Each command supports `--help`:

```text
scripts/download_and_process.sh
scripts/clean_and_build.sh
scripts/extract_ipa.py
scripts/generate_espeak_ipa.py
scripts/clean_rhyme_words.py
scripts/clean_rhyme_ipa.py
scripts/generate_rhyme_db.py
scripts/package_release.py
```

Default repository paths are resolved relative to each script, so commands work
from any current directory.

## Testing

Run the synthetic suite and shell syntax check:

```bash
python3 -B -m unittest discover -s tests -v
bash -n scripts/download_and_process.sh scripts/clean_and_build.sh
```

Tests use temporary fixtures and a fake eSpeak executable. They do not require
real dictionary data.

For an opt-in deterministic sample of existing eligible lists:

```bash
python3 tests/scripts/run_rhyme_smoke_test.py \
  --sample-size 50000 \
  --source wiktionary --source espeak \
  --release-version kaikki-v20260902
```

Smoke artifacts are written below `out/<lang>/samples/`, `databases/`, and
`reports/`.

## Scope

The databases intentionally omit consonance keys, fixed phoneme tails,
syllable counts, and syllabification. Compound identity rhymes may therefore
match across a complete trailing morpheme; filtering those is left to a future
on-device consumer.

## Licensing

- Code and tests: MIT, see [LICENSE-CODE](LICENSE-CODE).
- Downloaded and generated dictionary data: CC BY-SA 4.0, see
  [LICENSE-DATA.md](LICENSE-DATA.md).
- Repository licensing map: [LICENSE](LICENSE).

eSpeak NG is GPL-3.0-or-later, but using it to generate missing IPA does not
make that output GPL-licensed. The eSpeak-derived files retain
Wiktionary-sourced headwords and are distributed under the same CC BY-SA 4.0
data notice; their filenames keep the generation method visible. See
[LICENSE-DATA.md](LICENSE-DATA.md) for the provenance and license distinction.

Source data can contain errors, offensive or obsolete terms, regional
variants, and inaccurate pronunciations. Audit it before production use.

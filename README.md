# Ghostwriter Dictionary Data

This repository builds English, German, and Turkish word/pronunciation lists
from machine-readable Wiktionary data published by
[Kaikki.org](https://kaikki.org/dictionary/). The processed lists feed SQLite
rhyme indexes consumed by the Ghostwriter application.

The pipeline creates intermediate wordlists, can use eSpeak NG to generate IPA
for words that have no Wiktionary pronunciation, and builds per-language,
per-source SQLite rhyme indexes.

## Data sources

The pipeline downloads three gzip-compressed JSONL extracts:

| Wiktionary edition | Local archive | Approximate compressed size |
| --- | --- | ---: |
| English | `raw/raw-wiktextract-data.jsonl.gz` | 2.7 GB |
| German | `raw/de-extract.jsonl.gz` | 289 MB |
| Turkish | `raw/tr-extract.jsonl.gz` | 42 MB |

Current downloads and sizes are listed on the
[Kaikki raw-data page](https://kaikki.org/dictionary/rawdata.html). The English
archive expands to more than 20 GB, but this project reads all archives as gzip
streams and does not create uncompressed copies.

A Wiktionary *edition* is not a language filter. For example, German and
Turkish entries can occur in the English edition, and the Turkish edition can
contain entries for many other languages. Every archive is therefore searched
for all selected languages. Entries are routed using Wiktextract's exact
`lang_code` field:

- `en` becomes an English entry.
- `de` becomes a German entry.
- `tr` becomes a Turkish entry.

The human-readable `lang` field is not used for routing.

## Requirements

- Python 3.9 or newer
- Bash
- `curl`
- eSpeak NG, only when generating pronunciations for the no-IPA wordlists
- Enough disk space for roughly 3.1 GB of compressed source archives, plus the
  generated files. Updating every archive at once can temporarily require
  roughly another 3.1 GB while replacements are downloaded.
- Sufficient memory to retain the selected unique words and pronunciations
  during deduplication; the full English build may require several gigabytes

No third-party Python packages are required.

## Downloading and building

From the repository root, run:

```bash
./scripts/download_and_process.sh
```

The script:

1. Creates `raw/` and `out/` when necessary.
2. Downloads the English, German, and Turkish archives.
3. Requests the current HTTP `ETag` for each archive and compares it with the
   value saved beside the local file in `raw/*.etag`.
4. Reuses a local archive only when its saved `ETag` matches Kaikki's current
   value.
5. Downloads a changed archive to a `.part` file and atomically replaces the
   previous archive only after the transfer succeeds. Failed partial downloads
   are removed while the previous complete archive is preserved.
6. Reads all three archives directly in compressed form.
7. Merges and deduplicates English, German, and Turkish records across them.
8. Rebuilds and overwrites all six files in `out/` on every run.

During extraction, a single-line progress bar shows compressed bytes read out
of the exact total, the number of JSONL records processed, and the running
unique-word count for each language. The final unique-word totals cannot be
known until all editions have been merged; using byte progress avoids a second
full read of the large archives merely to calculate a total.

The stable Kaikki URLs point to the latest available extracts. Consequently,
running the script again later checks for updated releases automatically. If a
server does not provide an `ETag`, the script downloads that archive rather
than incorrectly assuming the local copy is current. Only the current complete
raw archives and current generated wordlists are retained. The `raw/` and
`out/` directories are intentionally ignored by Git.

## Generated files

The Wiktionary extraction build writes six UTF-8 files:

```text
out/en/wordlist_en_ipa.txt
out/en/wordlist_en_noipa.txt
out/de/wordlist_de_ipa.txt
out/de/wordlist_de_noipa.txt
out/tr/wordlist_tr_ipa.txt
out/tr/wordlist_tr_noipa.txt
```

### Words with IPA

Each `wordlist_<language>_ipa.txt` file is tab-separated and contains exactly
one line per word:

```text
hammer\t["/ˈhæmə/","/ˈhæmɚ/"]
```

The first field is the word. The second field is a compact JSON array of all
unique IPA values found for that word. JSON is used instead of a custom symbol
separator so that no pronunciation can collide with the delimiter.

Different IPA strings are retained because they may represent accents,
dialects, phonemic versus phonetic notation, or other legitimate variants.
Identical IPA strings are stored only once.

### Words without IPA

Each `wordlist_<language>_noipa.txt` contains one unique word per line. A word
is placed here only when none of the three source dumps provides a usable IPA
value for it. These files are intended to be inputs for language-appropriate
G2P processing.

## Generating missing IPA with eSpeak NG

After building the six Wiktionary wordlists, run:

```bash
python3 scripts/generate_espeak_ipa.py
```

The script uses the `en`, `de`, and `tr` eSpeak NG voices and reads:

```text
out/en/wordlist_en_noipa.txt
out/de/wordlist_de_noipa.txt
out/tr/wordlist_tr_noipa.txt
```

It atomically creates or overwrites:

```text
out/en/wordlist_en_espeak_ipa.txt
out/de/wordlist_de_espeak_ipa.txt
out/tr/wordlist_tr_espeak_ipa.txt
```

Each output has the same one-word-per-line format as the extracted IPA files:

```text
example\t["ɪɡzˈɑːmpəl"]
```

The generator sends words to eSpeak NG in batches of 10,000 by default. Its
single-line progress bar reports the exact `processed/total` word count for the
current language. Batching avoids starting a separate process for every word
while allowing progress to update during a large build. It uses quiet IPA mode
and does not produce or play audio.

If eSpeak NG is installed in another distrobox, enter that distrobox and invoke
the script using this repository path. If `espeak-ng` is not on its `PATH`, pass
the executable explicitly:

```bash
python3 scripts/generate_espeak_ipa.py \
  --espeak /path/to/espeak-ng
```

To process only selected languages, repeat `--lang-code`:

```bash
python3 scripts/generate_espeak_ipa.py \
  --lang-code de \
  --lang-code tr
```

The batch size can be adjusted when needed:

```bash
python3 scripts/generate_espeak_ipa.py --batch-size 5000
```

The script refuses malformed or missing input files, reports eSpeak failures,
skips empty pronunciations, and replaces an existing output only after
successful generation. Extra empty separator lines at the end of an eSpeak
batch are safely ignored. If a punctuation-heavy word or phrase produces
multiple non-empty lines, the mismatched batch is automatically bisected until
that entry is isolated; its IPA fragments are then joined under that one word
so later pronunciations cannot become misaligned.

## Cleaning and deduplication

The extractor currently:

- merges records from every supplied dump;
- deduplicates words and IPA values after Unicode NFC normalization;
- treats capitalization as significant;
- reads both `sounds[].ipa` and `sounds[].audio-ipa`;
- rejects empty IPA and complete placeholders such as `[...]`, `[…]`, `?`,
  `/.../`, and `/…/`;
- rejects empty headwords and headwords containing tabs or line breaks; and
- when `--latin-headwords-only` is enabled, rejects headwords containing
  non-Latin letters while still allowing spaces, punctuation, digits, emoji,
  and modifier characters.

The download script enables `--latin-headwords-only` for English, German, and
modern Turkish. This removes Arabic-, Cyrillic-, Greek-, and CJK-script entries
that were incorrectly tagged with one of those language codes without applying
an overly restrictive alphabet-only filter to slang.

The script trusts the source's `lang_code`. A mislabeled Latin-script word may
still pass through and should be handled by a later quality-audit stage.

## Preparing rhyme-eligible wordlists

Canonical Wiktionary and eSpeak wordlists intentionally preserve source data,
including combining forms and unusual transcription notation. Before building
SQLite databases, run the separate product-cleanup stage:

```bash
python3 scripts/clean_rhyme_wordlist.py \
  out/en/wordlist_en_ipa.txt \
  out/en/wordlist_en_rhyme_eligible.txt \
  --lang-code en

python3 scripts/clean_rhyme_wordlist.py \
  out/en/wordlist_en_espeak_ipa.txt \
  out/en/wordlist_en_espeak_rhyme_eligible.txt \
  --lang-code en
```

Repeat this for German and Turkish. Cleanup is pronunciation-specific: if one
IPA variant is malformed, other usable variants for the same word remain. A
word is omitted only when none of its pronunciations survives.

The `rhyme-cleanup-v3` policy:

- permits Latin-script letters, including accented letters and ligatures, in
  English and German loanwords. The click letters `ǀǁǂǃ` and r rotunda
  `ꝛ`/`Ꝛ` remain explicitly excluded. Turkish permits its 29-letter alphabet,
  `ÂâÎîÛû`, and `QqWwXx` for established spellings, proper names, and technical
  loans;
- normalizes typographic apostrophes to ASCII `'`, Unicode dash connectors to
  ASCII `-`, subscript digits to ASCII digits, and removes invisible soft
  hyphens. The result must consist of alphanumeric segments joined by single
  internal hyphens or apostrophes, retaining `state-of-the-art` and `7'nci`.
  Spaces, leading/trailing or repeated connectors, Braille, dotted-circle
  notation, enclosed letters, other symbols, and emoji remain excluded;
- splits unambiguous alternatives joined by `~` into separate IPA values;
- expands balanced, non-nested optional groups such as `[dɔ(ː)ɡ]` into
  `[dɔɡ]` and `[dɔːɡ]`, capped at eight variants;
- normalizes verified ASCII apostrophe stress notation to `ˈ` and treats `·`
  as the same harmless pronunciation separator as `.`;
- rejects embedded tabs/newlines, ellipses or incomplete fragments, ambiguous
  comma notation, mixed ASCII-uppercase/SAMPA or orthographic notation, Greek
  `α`/`ε`, Turkish dotless `ı` in IPA, mismatched delimiters, and remaining
  unrecognized tokens.

The canonical inputs are never modified. For an output such as
`wordlist_en_rhyme_eligible.txt`, the script atomically writes:

```text
wordlist_en_rhyme_eligible.txt
wordlist_en_rhyme_eligible_rejected.jsonl
wordlist_en_rhyme_eligible_rejected_words.jsonl
wordlist_en_rhyme_eligible_changes.jsonl
wordlist_en_rhyme_eligible_word_changes.jsonl
wordlist_en_rhyme_eligible_report.json
```

Rejection rows preserve the word, original IPA, reason, and details.
Word-rejection rows preserve the original headword, all its pronunciations,
the rejection reason, and rejected-character counts with Unicode code points.
Headword-change rows preserve original and normalized spellings, all
pronunciations, and applied normalizations. If multiple source spellings
normalize to one product headword, their pronunciation arrays are merged and
deduplicated.
Transformation rows preserve the original IPA and every normalized output.
The summary contains input/output totals, counts by rejection and
transformation reason, and the cleanup policy version. These artifacts make
every product-level removal or rewrite auditable without weakening the
canonical dataset.

## Slang coverage

No part-of-speech, topic, register, or dictionary-word filter is applied to the
canonical wordlists. Consequently, slang, internet language, abbreviations,
phrases, punctuation, numbers, and emoji remain available for auditing when
Wiktionary contains them under the selected language code. The stricter
rhyme-product cleanup accepts slang and abbreviations only when the complete
headword uses the selected language's alphabet or ASCII digits.

This does not guarantee complete coverage of Gen Z, internet, or street slang:
Kaikki extracts what Wiktionary contributors have documented. Additional
appropriately licensed slang sources can be merged later, with their provenance
and licenses recorded separately.

## Running the extractor directly

The generic extractor accepts one or more uncompressed `.jsonl` or compressed
`.jsonl.gz` files:

```bash
python3 scripts/extract_ipa.py \
  raw/de-extract.jsonl.gz \
  raw/tr-extract.jsonl.gz \
  raw/raw-wiktextract-data.jsonl.gz \
  --lang-code en \
  --lang-code de \
  --lang-code tr \
  --latin-headwords-only \
  --outdir out
```

Repeat `--lang-code` for every desired output language.

## Tests

Tests use small temporary gzip files and do not read `raw/` or `out/`:

```bash
python3 -B -m unittest discover -s tests -v
```

The tests cover routing by `lang_code`, merging all editions, cross-dump
deduplication, multiple IPA variants, missing IPA, junk IPA removal,
audio-specific IPA, slang/emoji retention, non-Latin headword rejection,
eSpeak output parsing, language-specific phoneme tokenization, stress-anchored
rhyme keys, vowel-only assonance keys, and end-to-end SQLite builds. All
fixtures are synthetic, and the eSpeak test uses a temporary fake executable,
so real datasets and eSpeak NG are not required.

## SQLite rhyme indexes

The final build stage creates one database per language and pronunciation
source so provenance remains explicit and each database can be distributed
independently:

```text
out/en/en_kaikki-en20260902-de20260901-tr20260901.db
out/en/en_espeak_kaikki-en20260902-de20260901-tr20260901.db
out/de/de_kaikki-en20260902-de20260901-tr20260901.db
out/de/de_espeak_kaikki-en20260902-de20260901-tr20260901.db
out/tr/tr_kaikki-en20260902-de20260901-tr20260901.db
out/tr/tr_espeak_kaikki-en20260902-de20260901-tr20260901.db
```

Replace the example dates with the Wiktionary dump dates recorded by Kaikki
for the three source archives used to make the wordlists. All three dates are
included because every output language is merged across all three Wiktionary
editions. The builder requires `--release-version` and refuses an output
filename that does not contain that value. Do not guess a missing release;
retain the archive metadata needed to trace the version. An eSpeak database
uses the same release version as its source no-IPA wordlist: although eSpeak
supplies its pronunciations, its word set still comes from that Kaikki release.

Run the builder once for each desired input, passing its language explicitly:

```bash
python3 scripts/generate_rhyme_db.py \
  out/en/wordlist_en_rhyme_eligible.txt \
  out/en/en_kaikki-en20260902-de20260901-tr20260901.db \
  --lang-code en \
  --release-version kaikki-en20260902-de20260901-tr20260901
python3 scripts/generate_rhyme_db.py \
  out/en/wordlist_en_espeak_rhyme_eligible.txt \
  out/en/en_espeak_kaikki-en20260902-de20260901-tr20260901.db \
  --lang-code en \
  --release-version kaikki-en20260902-de20260901-tr20260901
```

Each JSON-array pronunciation is expanded into a separate `(word, ipa)` row.
Every database has the same schema; its filename, rather than a database
column, identifies the language, pronunciation source, and Kaikki release:

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

IPA is tokenized with a language-specific phoneme inventory so affricates,
length marks, and diacritics remain attached to the correct phoneme. The
complete token sequence is reversed and space-delimited in `ipa_reversed`,
turning pronunciation-tail searches into indexable prefix searches. For
SQLite `LIKE 'prefix%'` queries against these binary indexes, enable
`PRAGMA case_sensitive_like = ON` on the querying connection so the query
planner can use the prefix range without changing capitalization semantics.

`rhyme_key_reversed` contains the reversed sequence from the vowel following
the last primary stress marker (`ˈ`) through the end of the pronunciation.
When primary stress is absent, it falls back to the final vowel. Anchoring at
stress is intentional: merely matching a fixed number of trailing phonemes
would over-match words that share a long unstressed suffix.

`assonance_reversed` contains the reversed vowel-only sequence. It has its own
index because browse-by-assonance is a standalone query, not only a score
calculated after another lookup. No consonance key, fixed-size tail columns,
syllable count, syllable boundary, or inferred syllabification is stored.

Production database builds use the rhyme-eligible product wordlists. The
builder reports any remaining unknown IPA symbols, prints single-line progress
and summary statistics, creates indexes after its bulk insert, and atomically
replaces the destination only after a successful build.

### Deterministic integration smoke test

The opt-in smoke runner takes a deterministic, broadly distributed sample from
each rhyme-eligible wordlist, builds a database, checks its schema and indexes,
runs `PRAGMA integrity_check`, and records counts in a manifest. It is
intentionally separate from the lightweight automated suite. Run it only after
cleanup and the automated tests pass:

```bash
python3 tests/scripts/run_rhyme_smoke_test.py \
  --sample-size 50000 \
  --release-version kaikki-en20260902-de20260901-tr20260901
```

The default seed selects the 50,000 rows with the lowest deterministic BLAKE2b
scores for each language. This avoids the alphabetical bias of contiguous
chunks and produces the same sample whenever the source rows, seed, and release
are unchanged. If a wordlist contains fewer rows, all its rows are selected.
Use `--source espeak` to test eSpeak wordlists and repeat `--source` to test
both sources.

Generated samples are stored in `out/<lang>/samples/`, sampled databases in
`out/<lang>/databases/`, and build details in
`out/<lang>/smoke_manifest.json`. The complete `out/` tree is ignored by Git
because it contains generated CC BY-SA dictionary data and should be
regenerated for each release.

### Known rhyme-matching limitation

The database does not suppress identity rhymes created by compounds. German
words such as `Naturwissenschaft` and `Geisteswissenschaft`, for example, can
match across the complete `-wissenschaft` tail. This is deliberately left for
a future on-device post-match filter that can check whether a long matching
tail is itself a standalone dictionary word; it is not part of the schema or
database build.

## Licensing and attribution

This repository uses separate licenses for code and data:

- Original scripts and tests are licensed under the
  [MIT License](LICENSE-CODE).
- Downloaded and processed Wiktionary/Kaikki dictionary data is distributed
  under the
  [Creative Commons Attribution-ShareAlike 4.0 International License](LICENSE-DATA.md).

Kaikki states that its extracted dictionary data is made available under the
same licenses as Wiktionary: CC BY-SA and the GNU Free Documentation License.
This project uses the CC BY-SA 4.0 licensing path for the processed dataset.

The processed data is derived from English Wiktionary, German Wiktionary, and
Turkish Wiktionary contributors using Kaikki.org and Wiktextract. The data is
modified by language filtering, merging, Unicode normalization,
deduplication, non-Latin headword filtering, IPA extraction, and invalid IPA
removal. Product cleanup additionally applies a language-specific alphanumeric
headword policy, expands or normalizes supported transcription notation,
rejects malformed pronunciation variants, and records all changes and
rejections. SQLite releases expand the cleaned pronunciation arrays, tokenize
IPA, and derive indexed reversed rhyme and assonance keys. Pronunciations
generated locally with eSpeak NG are
identified by the `wordlist_<language>_espeak_ipa.txt` and versioned
`*_espeak_*.db` filenames.
Other dataset additions must be documented in this section and must use terms
compatible with CC BY-SA 4.0.

See [LICENSE](LICENSE) for the repository-wide licensing map and
[LICENSE-DATA.md](LICENSE-DATA.md) for the required data attribution notice.

## Disclaimer

The source and generated data may contain errors, offensive terms, obsolete
language, regional variants, or inaccurate pronunciations. The files are
provided without warranty and should be audited before production use.

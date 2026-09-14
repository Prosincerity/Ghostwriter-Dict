# Ghostwriter Dictionary Data

This repository builds English, German, and Turkish pronunciation dictionaries
from [Kaikki/Wiktextract](https://kaikki.org/dictionary/), fills missing IPA
with eSpeak NG, cleans the result for product use, and creates per-language,
per-source SQLite rhyme indexes.

## Requirements and data

- Python 3.10+, Bash, and `curl`
- eSpeak NG only for generated pronunciations
- No third-party Python packages
- Substantial disk space for compressed archives, atomic replacement files,
  and generated outputs; extraction may require several gigabytes of memory

The pipeline streams three archives without decompressing them to disk:

| Wiktionary edition | Local archive |
| --- | --- |
| English | `raw/raw-wiktextract-data.jsonl.gz` |
| German | `raw/de-extract.jsonl.gz` |
| Turkish | `raw/tr-extract.jsonl.gz` |

Every archive is searched for every requested language because a Wiktionary
edition is not a language filter. Entries are routed only by Wiktextract's
top-level `lang_code` (`en`, `de`, or `tr`), never the display-name `lang`.

`raw/` and `out/` are generated, Git-ignored directories. Downloads and build
outputs use `.part` files and atomic replacement, so a failed update preserves
the previous complete artifact.

## Pipeline

### 1. Download and extract Wiktionary data

```bash
./scripts/download_and_process.sh
```

The script compares saved and remote HTTP ETags, downloads changed archives,
then merges all three editions. Extraction routes by `lang_code`, normalizes
Unicode to NFC, treats capitalization as significant, and deduplicates words
and IPA. It reads `sounds[].ipa` and `sounds[].audio-ipa`, rejects empty values
and complete placeholders, and enables the canonical non-Latin-letter filter.
That filter still preserves phrases, punctuation, digits, slang, and emoji for
later auditing.

Outputs are written under each language directory:

```text
out/<lang>/wordlist_<lang>_ipa.txt
out/<lang>/wordlist_<lang>_noipa.txt
```

IPA lists have one UTF-8 TSV row per word. The second field is a compact JSON
array containing all unique pronunciations:

```text
hammer\t["/ˈhæmə/","/ˈhæmɚ/"]
```

No-IPA lists contain one word per line, and only when no supplied dump has a
usable pronunciation.

To run the extractor directly:

```bash
python3 scripts/extract_ipa.py \
  raw/de-extract.jsonl.gz \
  raw/tr-extract.jsonl.gz \
  raw/raw-wiktextract-data.jsonl.gz \
  --lang-code en --lang-code de --lang-code tr \
  --latin-headwords-only \
  --outdir out
```

### 2. Generate missing IPA with eSpeak NG

```bash
python3 scripts/generate_espeak_ipa.py
```

This reads each `wordlist_<lang>_noipa.txt` and atomically writes:

```text
out/<lang>/wordlist_<lang>_espeak_ipa.txt
```

The output uses the same TSV/JSON-array format. Defaults are all three language
voices and batches of 10,000 words. Useful options are:

```bash
python3 scripts/generate_espeak_ipa.py \
  --lang-code de --lang-code tr \
  --batch-size 5000 \
  --espeak /path/to/espeak-ng
```

The generator isolates anomalous multi-line eSpeak results so one
punctuation-heavy entry cannot shift later pronunciations.

### 3. Create rhyme-eligible wordlists

Canonical wordlists remain unchanged. Run cleanup separately for Wiktionary
and eSpeak inputs, for example:

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

Repeat for `de` and `tr`. The current policy, `rhyme-cleanup-v3`, does the
following:

- English and German accept Latin letters, accents, and ligatures, except click
  letters `ǀǁǂǃ` and r rotunda `ꝛ`/`Ꝛ`. Turkish accepts its 29-letter alphabet,
  `ÂâÎîÛû`, and `QqWwXx`. ASCII digits are valid in every language.
- Typographic apostrophes become `'`, Unicode dash connectors become `-`,
  subscript digits become ASCII digits, and soft hyphens are removed.
- A normalized headword must consist of alphanumeric segments joined only by
  one internal `-` or `'`. `state-of-the-art` and `7'nci` pass; spaces,
  combining forms, malformed connectors, Braille, dotted-circle notation,
  enclosed letters, other symbols, and emoji do not.
- Unambiguous `~` IPA alternatives are split; balanced non-nested optional
  groups expand to at most eight variants; IPA `'` becomes `ˈ` and `·` becomes
  `.`.
- IPA with controls, incomplete ellipses, ambiguous commas, bad delimiters,
  mixed uppercase/SAMPA or orthographic notation, Greek `α`/`ε`, Turkish
  dotless `ı`, or unknown tokens is rejected.

Pronunciations are validated independently, so valid variants survive a bad
sibling. Normalized headword collisions are merged and their IPA deduplicated.
Each eligible wordlist has these atomic audit sidecars:

```text
*_rejected.jsonl        pronunciation rejections
*_rejected_words.jsonl  headword rejections and invalid code points
*_changes.jsonl         IPA transformations
*_word_changes.jsonl    headword transformations
*_report.json           counts, reasons, and policy version
```

### 4. Build versioned SQLite indexes

Build separate Wiktionary and eSpeak databases for each language. The filename
must contain a traceable composite release slug for all three merged Kaikki
edition dates. An eSpeak database inherits the release of its source no-IPA
list.

```bash
RELEASE=kaikki-en20260902-de20260901-tr20260901

python3 scripts/generate_rhyme_db.py \
  out/en/wordlist_en_rhyme_eligible.txt \
  out/en/en_$RELEASE.db \
  --lang-code en --release-version "$RELEASE"

python3 scripts/generate_rhyme_db.py \
  out/en/wordlist_en_espeak_rhyme_eligible.txt \
  out/en/en_espeak_$RELEASE.db \
  --lang-code en --release-version "$RELEASE"
```

Repeat for German and Turkish. Never guess a release or use a filename that
cannot be traced to the archive metadata. Language, source, and release remain
in the filename rather than database columns.

Every database uses exactly:

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

Each pronunciation becomes one row. IPA is tokenized with an audited
per-language inventory before deriving space-delimited reversed values:

- `ipa_reversed`: the complete phoneme sequence reversed.
- `rhyme_key_reversed`: the sequence from the vowel after the last primary
  stress through the end, reversed; without primary stress, it starts at the
  last vowel.
- `assonance_reversed`: the vowel-only sequence reversed.

Spaces preserve phoneme boundaries for indexed prefix matching. Enable
`PRAGMA case_sensitive_like = ON` when using SQLite `LIKE 'prefix%'` so these
binary indexes can support prefix range scans.

Indexes are created after bulk insertion. Unknown symbols are reported, and
the destination is atomically replaced only after a successful build.

## Testing

Run the dependency-free synthetic suite:

```bash
python3 -B -m unittest discover -s tests -v
```

For shell changes also run:

```bash
bash -n scripts/download_and_process.sh
```

The tests use temporary fixtures and a fake eSpeak executable; they do not
read real datasets.

After cleanup and unit tests pass, the opt-in smoke runner selects the lowest
seeded BLAKE2b hashes from each real eligible list, builds sampled databases,
checks schema/indexes and `PRAGMA integrity_check`, and writes manifests:

```bash
python3 tests/scripts/run_rhyme_smoke_test.py \
  --sample-size 50000 \
  --source wiktionary --source espeak \
  --release-version kaikki-en20260902-de20260901-tr20260901
```

The default seed is deterministic; unchanged input, seed, and release produce
the same sample. Results are Git-ignored under `out/<lang>/samples/`,
`out/<lang>/databases/`, and `out/<lang>/smoke_manifest.json`.

## Scope and known limitation

The database intentionally has no consonance key, fixed-size tail columns,
syllable count, syllable boundaries, or inferred syllabification.

Compound identity rhymes are not filtered. For example, German compounds can
match across a complete shared trailing morpheme. A future on-device filter may
exclude long matching tails that are standalone dictionary words.

## Licensing

- Original scripts and tests: MIT, see [LICENSE-CODE](LICENSE-CODE).
- Kaikki/Wiktionary-derived and generated dictionary data: CC BY-SA 4.0, see
  [LICENSE-DATA.md](LICENSE-DATA.md).

The data is transformed by language routing, merging, NFC normalization,
deduplication, headword filtering/normalization, IPA extraction and cleanup,
eSpeak generation, phoneme tokenization, and derived rhyme/assonance keys.
Wiktionary and eSpeak outputs remain separately identified. Document new data
sources and use compatible licensing. See [LICENSE](LICENSE) for the full
repository licensing map.

## Disclaimer

Source and generated data may contain errors, offensive or obsolete terms,
regional variants, and inaccurate pronunciations. Audit before production use.

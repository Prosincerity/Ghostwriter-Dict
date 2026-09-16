# Ghostwriter Dictionary Data

Builds English, German, and Turkish pronunciation dictionaries from
[Kaikki/Wiktextract](https://kaikki.org/dictionary/), fills missing IPA with
eSpeak NG, applies product filters, and creates SQLite rhyme indexes.

## Requirements

- Python 3.10+, Bash, `curl`, and eSpeak NG
- No third-party Python packages
- Enough space for the compressed archives and generated outputs; extraction
  may use several gigabytes of memory

`raw/` and `out/` are generated and ignored by Git. Archives are streamed and
outputs are replaced atomically through `.part` files.

## Build the databases

Run the complete pipeline:

```bash
./scripts/download_and_process.sh
```

It updates the three Kaikki archives, extracts canonical data, generates
missing IPA, cleans both pronunciation sources, and builds six databases. The
release is `kaikki-vYYYYMMDD`, using the English archive's HTTP
`Last-Modified` date.

To reuse existing archives without network access, supply that release:

```bash
./scripts/download_and_process.sh \
  --skip-download \
  --release-version kaikki-v20260902
```

The finished files are:

```text
out/en/en_kaikki-vYYYYMMDD.db
out/en/en_espeak_kaikki-vYYYYMMDD.db
out/de/de_kaikki-vYYYYMMDD.db
out/de/de_espeak_kaikki-vYYYYMMDD.db
out/tr/tr_kaikki-vYYYYMMDD.db
out/tr/tr_espeak_kaikki-vYYYYMMDD.db
```

Use `--release-version` without `--skip-download` to override the automatically
derived release. Run the script with `--help` for its complete CLI.

## Generated artifacts

Each language directory contains:

| Artifact | Purpose |
| --- | --- |
| `wordlist_<lang>_ipa.txt` | Canonical Wiktionary pronunciations |
| `wordlist_<lang>_noipa.txt` | Words lacking usable Wiktionary IPA |
| `wordlist_<lang>_espeak_ipa.txt` | Generated eSpeak pronunciations |
| `wordlist_<lang>_rhyme_eligible.txt` | Cleaned Wiktionary input for SQLite |
| `wordlist_<lang>_espeak_rhyme_eligible.txt` | Cleaned eSpeak input for SQLite |
| `reports/` | Cleanup counts, changes, and grouped rejections |
| `*.db` | Finished versioned rhyme indexes |

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
normalized to Unicode NFC and deduplicated. The extractor reads both
`sounds[].ipa` and `sounds[].audio-ipa`, removes complete placeholders, and
keeps capitalization significant.

Canonical lists intentionally retain phrases, slang, punctuation, digits, and
emoji for auditing. The product cleanup stage applies the stricter filter.

### eSpeak generation

Words without usable Wiktionary IPA are sent to their matching eSpeak NG
voice. Adaptive batch isolation prevents entries that produce multiple output
lines from shifting later pronunciations.

### Product cleanup

Cleanup policy `rhyme-cleanup-v10` keeps one-token headwords made from the
language's accepted Latin letters, ASCII digits, printable ASCII keyboard
punctuation, or Hawaiian ʻokina (`U+02BB`). Spaces, emoji, Braille, enclosed
letters, dotted-circle notation, and other unsupported symbols are rejected.
Turkish uses its 29-letter alphabet plus `ÂâÎîÛû` and `QqWwXx`.

Typographic apostrophes, Unicode dashes, subscript digits, and soft hyphens are
normalized. Supported IPA alternatives and optional groups are expanded;
malformed notation, unknown tokens, and values without an actual phoneme are
rejected. Pronunciations are checked independently, so valid siblings remain.

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
`ipa_reversed` contains all tokens in reverse order;
`assonance_reversed` contains only reversed vowel tokens. Both are
space-delimited so indexed prefix searches preserve phoneme boundaries.

For SQLite prefix queries, enable `PRAGMA case_sensitive_like = ON`. Rhyme
prefixes are derived from `ipa_reversed`; no separate rhyme-key column is
stored.

## Run stages individually

Each Python script has `--help` documentation:

```text
scripts/extract_ipa.py
scripts/generate_espeak_ipa.py
scripts/clean_rhyme_wordlist.py
scripts/generate_rhyme_db.py
```

All paths resolve relative to the scripts where defaults are provided, so the
commands work from any current directory.

## Testing

Run the synthetic suite and shell syntax check:

```bash
python3 -B -m unittest discover -s tests -v
bash -n scripts/download_and_process.sh
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

Source data can contain errors, offensive or obsolete terms, regional
variants, and inaccurate pronunciations. Audit it before production use.

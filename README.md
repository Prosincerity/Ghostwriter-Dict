# Ghostwriter Dictionary Data

Builds English, German, and Turkish pronunciation indexes from
[Kaikki/Wiktextract](https://kaikki.org/dictionary/) data. The pipeline fills
missing IPA with eSpeak NG, cleans words and pronunciations for rhyme search,
and creates SQLite databases.

## Requirements

- Python 3.10+, Bash, `flock`, and `curl`
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
After downloading, SHA-256 checkpoints verify each stage's inputs, relevant
scripts, settings and outputs. States live under each language’s
`reports/state/`; the six-file package has a shared checkpoint. G2P and
validation additionally fingerprint the eSpeak executable, version and files
under the data directory reported by eSpeak. Each shell entry point locks the
output directory for the build. Retry the same command after an interruption.
A changed upstream file reruns its consumers. If recomputation yields identical
input bytes for the next stage, that stage can reuse its outputs.

To clean existing canonical IPA lists and rebuild packaged databases without
downloading or extracting, provide their release name:

```bash
./scripts/clean_and_build.sh --release-version kaikki-v20260902
```

Both scripts repair malformed or unstressed Wiktionary IPA with eSpeak and move
extreme mismatches to the eSpeak output by default. Add `--report-only` to
audit mismatches without moving them. `--extreme-distance` changes the mismatch
threshold. Run either script with `--help` for all options.

Both builds write six `*.db.gz` files and `out/SHA256SUMS`. Verify the package
from `out/` with `sha256sum -c SHA256SUMS`.

## Outputs

Each `out/<lang>/` directory (`en`, `de`, or `tr`) contains:

| File | Contents |
| --- | --- |
| `raw_<lang>_ipa.jsonl`, `raw_<lang>_noipa.jsonl` | Original word and raw IPA fields per selected record |
| `wordlist_<lang>_ipa.txt` | Source fields for retained, normalized headwords, before IPA eligibility checks |
| `wordlist_<lang>_noipa.txt` | Retained words with no non-placeholder source IPA |
| `wordlist_<lang>_espeak_ipa.txt` | Generated eSpeak IPA |
| `wordlist_<lang>_wiktionary_words_cleaned.txt`, `wordlist_<lang>_espeak_words_cleaned.txt` | Prepared source IPA (complete build), or headword-cleaned existing lists (existing-list rebuild) |
| `wordlist_<lang>_rhyme_eligible.txt`, `wordlist_<lang>_espeak_rhyme_eligible.txt` | Cleaned inputs for SQLite |
| `<lang>_kaikki-vYYYYMMDD.db`, `<lang>_espeak_kaikki-vYYYYMMDD.db` | Versioned SQLite indexes |

Reports live in `out/<lang>/reports/`:

| Directory | Contents |
| --- | --- |
| `downloading/` | Archive fingerprints and download status (complete build) |
| `reading/` | Extraction counts and source fingerprints |
| `ipa_generation/` | eSpeak output counts and words producing empty IPA |
| `cleaning/` | Word and IPA counts, changes, rejections, comparisons, and summary |

IPA wordlists are UTF-8 TSV with one JSON array per word:

```text
hammer	["/ˈhæmə/","/ˈhæmɚ/"]
```

No-IPA lists contain one word per line. Wiktionary and eSpeak pronunciations
remain in separate files.

## How the data is processed

1. Download and stream all editions. Project each selected record into raw JSONL by top-level language and presence of IPA fields.
2. Clean headwords and source IPA, merge variants, and write missing-word and repair inputs. Review `reports/cleaning/summary.md` and its structured audit. See [CLEANING_RULES.md](CLEANING_RULES.md) for the exact rules.
3. Generate missing IPA from the cleaned wordlist with the existing adaptive eSpeak batches.
4. Validate source IPA against eSpeak, repair rejected candidates, and apply the configured extreme-mismatch policy.
5. Build the six SQLite indexes and run SQLite integrity checks before replacing each database.
6. Package the six gzip archives and write the release checksums. Packaging is local.

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

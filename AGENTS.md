# AGENTS.md

## Repository

This project turns English, German, and Turkish Kaikki/Wiktextract data into
audited SQLite pronunciation indexes. Read `README.md` before substantial
changes.

- `scripts/download_and_process.sh`: complete archive-to-database build
- `scripts/clean_and_build.sh`: cleanup and database rebuild from existing IPA
  lists; never downloads or extracts, but invokes eSpeak for IPA repairs
- `scripts/extract_ipa.py`: canonical Wiktionary IPA/no-IPA extraction
- `scripts/generate_espeak_ipa.py`: missing-IPA generation
- `scripts/clean_rhyme_words.py`: written-word cleanup and word audit reports
- `scripts/clean_rhyme_ipa.py`: IPA validation, eSpeak repair, and IPA reports
- `scripts/generate_rhyme_db.py`: versioned SQLite index creation
- `tests/`: synthetic tests and the opt-in real-data smoke runner

## Safety and implementation rules

- Do not inspect, search, print, hash, or copy `raw/` or `out/` unless the user
  explicitly requests it. Exclude both from recursive searches.
- Never commit archives, generated outputs, `.part` files, or Python caches.
  Only finished SQLite releases are intended for versioning.
- Stream `.jsonl.gz` files; never decompress them to disk.
- Use only the Python standard library and resolve repository paths relative to
  scripts so commands work from any current directory.
- Write downloads and generated artifacts through `.part` files and replace
  them atomically. A failure must preserve the previous complete artifact.
- Use synthetic fixtures by default. Run a real build or audit only when the
  user explicitly requests one.
- Long operations use one updating progress line. Extraction reports bytes,
  records, and unique words without a pre-count; eSpeak reports exact progress.

## Data contracts

Search every supplied Wiktionary edition for every requested language and
route only by top-level `lang_code`. Normalize words and IPA to NFC before
deduplication. Lowercase headwords with fewer than two uppercase letters,
merging their distinct IPA values; preserve spellings with two or more uppercase
letters. Read both `sounds[].ipa` and `sounds[].audio-ipa`, retaining distinct
variants and rejecting only empty or complete placeholder values.

Canonical lists preserve slang, phrases, punctuation, digits, and emoji.
`--latin-headwords-only` rejects non-Latin letters but is not the product
filter. Keep Wiktionary and eSpeak outputs separate, and preserve eSpeak's
adaptive isolation of anomalous multi-line output.

IPA wordlists use one UTF-8 TSV row per word with a compact JSON array:

```text
hammer	["/ˈhæmə/","/ˈhæmɚ/"]
```

No-IPA lists contain one unique word per line only when no supplied dump has a
usable pronunciation. Do not introduce repeated word rows or custom IPA
delimiters.

## Product cleanup

Never modify canonical lists in place. Policy `rhyme-cleanup-v13` produces
separate rhyme-eligible lists and reports under `out/<lang>/reports/`.

- All languages share the same curated Latin alphabet covering English,
  German, Turkish, French, and common loanword letters, plus ASCII digits and
  Hawaiian ʻokina (`U+02BB`). Extend the shared alphabet deliberately.
- Accept apostrophes attached to letters or digits. Dots, dashes, ampersands,
  and slashes are valid only between letters. Percent signs may follow terminal
  numbers; one or more plus signs may follow terminal letters. Reject other
  punctuation, misplaced symbols, and single-letter headwords.
- Normalize typographic apostrophes, Unicode dashes, subscript digits, and soft
  hyphens. The result must be one token; reject spaces and unsupported symbols.
- Split unambiguous alternatives, expand at most eight non-nested optional
  variants, and normalize supported IPA notation.
- Reject controls, incomplete or ambiguous notation, malformed delimiters,
  orthographic/SAMPA forms, unknown tokens, and values without a phoneme.
- Validate pronunciations independently, retain valid siblings, and merge
  normalized headword collisions without duplicate IPA.
- Regenerate malformed and stressless Wiktionary IPA with eSpeak. Keep valid
  Wiktionary siblings in their source list and route regenerated IPA to the
  eSpeak list. Audit each rejected candidate and replacement.
- Compare each valid Wiktionary IPA to one batched eSpeak result per word using
  complete audited phoneme tokens. Record distance and stressed rhyme tails.
  Default to report-only; moving extreme mismatches requires the explicit
  opt-in flag and must preserve valid sibling pronunciations.

Reports must retain policy version, counts, reasons, original/normalized
values, rejected characters or tokens, and grouped readable rejection lists.

## SQLite contract

Build one database per language and source from rhyme-eligible lists only. Use
the English Kaikki date as `kaikki-vYYYYMMDD`; provenance stays in filenames,
not table columns. Each IPA array expands to one `(word, ipa)` row.

The production schema is exactly:

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

Tokenize with the audited language inventory; never reverse raw characters or
silently accept/drop unknown symbols. Store reversed complete tokens without
separators or whitespace in `ipa_reversed`; keep reversed vowel tokens
space-delimited in `assonance_reversed`. Create indexes after bulk insertion.

Do not add a rhyme key, consonance key, fixed tail, syllable data, or
syllabification. Compound identity-rhyme filtering remains an on-device concern.

## Validation and licensing

Run:

```bash
python3 -B -m unittest discover -s tests -v
bash -n scripts/download_and_process.sh scripts/clean_and_build.sh
```

Tests must not require real datasets or eSpeak. Code and tests are MIT
(`LICENSE-CODE`); downloaded and generated dictionary data is CC BY-SA 4.0
(`LICENSE-DATA.md`). Keep attribution, transformation notices, documentation,
tests, and provenance aligned with behavior changes.

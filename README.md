# Ghostwriter Dictionary Data

This repository builds English, German, and Turkish word/pronunciation lists
from machine-readable Wiktionary data published by
[Kaikki.org](https://kaikki.org/dictionary/). The processed lists are intended
to become a SQLite dictionary consumed by the Ghostwriter application.

The current pipeline creates the intermediate wordlists and can use eSpeak NG
to generate IPA for words that have no Wiktionary pronunciation. SQLite
generation is planned but is not implemented yet.

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
out/wordlist_en_ipa.txt
out/wordlist_en_noipa.txt
out/wordlist_de_ipa.txt
out/wordlist_de_noipa.txt
out/wordlist_tr_ipa.txt
out/wordlist_tr_noipa.txt
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
out/wordlist_en_noipa.txt
out/wordlist_de_noipa.txt
out/wordlist_tr_noipa.txt
```

It atomically creates or overwrites:

```text
out/wordlist_en_espeak_ipa.txt
out/wordlist_de_espeak_ipa.txt
out/wordlist_tr_espeak_ipa.txt
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

## Slang coverage

No part-of-speech, topic, register, or dictionary-word filter is applied.
Consequently, slang, internet language, abbreviations, phrases, punctuation,
numbers, and emoji are retained when Wiktionary contains them under the
selected language code.

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
audio-specific IPA, slang/emoji retention, non-Latin headword rejection, and
eSpeak output parsing. The eSpeak test uses a temporary fake executable, so
eSpeak NG does not need to be installed to run the tests.

## Planned SQLite representation

Because the same spelling may occur in more than one language, a combined
database should use `(language, word)` rather than `word` alone as its primary
key. The IPA JSON array can be stored directly as text:

```sql
CREATE TABLE entries (
    language TEXT NOT NULL,
    word TEXT NOT NULL,
    ipa TEXT NOT NULL,
    PRIMARY KEY (language, word)
);
```

An alternative normalized schema can place pronunciations in a child table.
That is preferable if Ghostwriter needs to query individual pronunciations.

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
removal. Pronunciations generated locally with eSpeak NG are identified by the
`wordlist_<language>_espeak_ipa.txt` filenames. Other dataset additions must be
documented in this section and must use terms compatible with CC BY-SA 4.0.

See [LICENSE](LICENSE) for the repository-wide licensing map and
[LICENSE-DATA.md](LICENSE-DATA.md) for the required data attribution notice.

## Disclaimer

The source and generated data may contain errors, offensive terms, obsolete
language, regional variants, or inaccurate pronunciations. The files are
provided without warranty and should be audited before production use.

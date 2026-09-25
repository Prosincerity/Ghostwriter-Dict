# Dictionary data license and attribution

The downloaded and processed dictionary data in this project, including the
wordlists and any SQLite database built from them, is made available under the
[Creative Commons Attribution-ShareAlike 4.0 International License][license]
(CC BY-SA 4.0).

The canonical and legally binding license text is available at:

<https://creativecommons.org/licenses/by-sa/4.0/legalcode>

## Source attribution

The dictionary data is derived from contributions to:

- [English Wiktionary](https://en.wiktionary.org/)
- [German Wiktionary](https://de.wiktionary.org/)
- [Turkish Wiktionary](https://tr.wiktionary.org/)

The Wiktionary data was converted to machine-readable JSONL by
[Wiktextract](https://github.com/tatuylonen/wiktextract) and distributed by
[Kaikki.org](https://kaikki.org/dictionary/). Dump provenance and current
download information are available on the
[Kaikki raw-data page](https://kaikki.org/dictionary/rawdata.html).

Missing pronunciations are generated from Wiktionary headwords with
[eSpeak NG](https://github.com/espeak-ng/espeak-ng). eSpeak NG is licensed under
GPL-3.0-or-later, but its GPL license does not apply to these generated IPA
transcriptions merely because the program produced them; see the
[GNU GPL FAQ on program output](https://www.gnu.org/licenses/gpl-faq.en.html#WhatCaseIsOutputGPL).
The eSpeak wordlists and SQLite indexes retain the Wiktionary-derived
headwords, so this project distributes those artifacts under CC BY-SA 4.0
alongside the Wiktionary-derived artifacts. The separate eSpeak filenames
identify how their pronunciations were produced; they are not a claim that
eSpeak NG itself is licensed under CC BY-SA 4.0. This project does not
distribute the eSpeak NG program or its voice data.

Copyright in the original Wiktionary material remains with its respective
contributors. This project does not claim exclusive ownership of that
material.

## Modifications

This project modifies the source data by:

- selecting entries by the `en`, `de`, and `tr` `lang_code` values;
- combining entries from the English, German, and Turkish Wiktionary editions;
- extracting headwords and IPA/audio-IPA values;
- normalizing strings to Unicode NFC;
- lowercasing headwords with fewer than two uppercase letters while preserving
  multi-capital spellings;
- deduplicating words and pronunciations;
- removing unusable IPA placeholders and malformed records;
- filtering product headwords with a shared curated Latin alphabet and
  position-sensitive punctuation rules;
- validating and normalizing pronunciation notation;
- regenerating malformed or unstressed Wiktionary pronunciations with eSpeak NG
  and placing replacements in separately identified eSpeak lists while keeping
  valid Wiktionary variants;
- auditing valid Wiktionary pronunciations against eSpeak-generated IPA and,
  only when explicitly enabled, moving extreme mismatches to the eSpeak list;
- deriving reversed IPA and vowel-only search values for rhyme indexes; and
- optionally generating pronunciations for missing-IPA words with eSpeak NG,
  stored in separately identified `wordlist_<language>_espeak_ipa.txt` files.

If additional G2P systems or data sources are incorporated later, their
provenance and the nature of those additions must also be documented.

## Conditions when redistributing

When sharing the data or an adapted database, comply with CC BY-SA 4.0. Among
other requirements, you must:

- give appropriate credit to the Wiktionary contributors and identify
  Wiktextract/Kaikki as the extraction and distribution path;
- provide a link to CC BY-SA 4.0;
- indicate that the source data was modified; and
- distribute adapted material under CC BY-SA 4.0 or a license Creative Commons
  recognizes as BY-SA compatible.

Do not imply endorsement by Wikimedia, Wiktionary, Wiktextract, Kaikki.org, or
their contributors.

This summary is provided for convenience and does not replace or modify the
canonical CC BY-SA 4.0 legal text.

## Warranty

To the extent permitted by the applicable license and law, the data is
provided as-is and without warranties of accuracy, completeness, merchantability,
fitness for a particular purpose, or non-infringement.

[license]: https://creativecommons.org/licenses/by-sa/4.0/

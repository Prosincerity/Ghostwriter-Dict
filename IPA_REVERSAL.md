# SQLite IPA keys, format 2

The indexed value comes from the exact text in `dictionary.ipa`. Each TSV word's JSON array still produces one row per distinct pronunciation. The writer preserves IPA code points and surrounding space, and continues to validate the headword and reject controls in database input. Upstream release extraction preserves original fields in the raw JSONL projections. Product cleanup can expand optional forms, split alternatives, trim whitespace, normalize to NFC, replace unsupported notation or generate a repair. A release key restores the **eligible pronunciation**. The source audit also runs the codec directly on the pre-cleaning fields.

## Working with the key

With `scripts/` on the Python import path:

```python
from ipa_reversal import (
    deserialize, reconstruct, restore_original, reverse_ipa, reverse_ipa_for_db,
    reverse_tokens, serialize, tokenize_ipa,
)

ipa = "ˈt͡ʃaː"
tokens = tokenize_ipa(ipa, "en")             # ("ˈt͡ʃ", "aː")
key = serialize(reverse_tokens(tokens))     # "aː ˈt͡ʃ"
assert reconstruct(tokens) == ipa
assert deserialize(key) == reverse_tokens(tokens)
assert restore_original(str(key)) == ipa    # works on ordinary SQLite TEXT
assert reverse_ipa(reverse_ipa(ipa, "en"), "en") == ipa
```

Raw IPA and a serialized key can have identical spellings and different meanings. The first `reverse_ipa` call returns a `ReversedIPA` string subtype to carry that distinction in memory. Pass plain database strings to `restore_original`. Deserialization uses the stored fields and never infers their boundaries again.

| Original IPA | Language | Stored key | Restored IPA |
| --- | --- | --- | --- |
| `ˈt͡ʃaː` | English | `aː ˈt͡ʃ` | `ˈt͡ʃaː` |
| `ɡˈast` | German | `t s ˈa ɡ` | `ɡˈast` |
| `/t͡ʃoˈd͡ʒuk/` | Turkish | `/ k u ˈd͡ʒ o t͡ʃ /` | `/t͡ʃoˈd͡ʒuk/` |
| `[a  b]` | English | `[ b \u{20} \u{20} a ]` | `[a  b]` |

### Token boundaries

1. **Source information.** Both production build modes request the matching eSpeak voice's Unicode IPA with `--ipa --sep=_ -D` in deterministic batches at the eSpeak database stage. Each word is replayed once per TSV row and shared by its variants. After trimming the engine's line padding, removing its auxiliary separators must give the existing IPA exactly. A match transfers field boundaries. A mismatch keeps the existing IPA, uses the inventory fallback, and increments `engine_spelling_differs`. This extra pass also works for historical wordlists without a boundary sidecar. Checkpoints include the engine and data fingerprints. The ordinary generation and repair commands keep their original IPA output.
2. **Supplied eSpeak notation.** Pass `source="espeak-separated"` for phoneme fields separated by underscores or U+200C, including transcriptions consisting of a single field. `source="espeak"` recognizes those literal separators in historical or mixed eSpeak input. The codec retains a separator present in the source IPA. A separator appearing only in an auxiliary engine result never enters the stored IPA. For eSpeak's fully tied notation, `source="espeak-tied"` uses explicit connections and keeps adjacent unconnected bases separate. eSpeak 1.52.0 emits U+200D with `--tie=z` and U+200C with `--sep=z`. Source metadata matters for `_`, which is an unknown symbol in Wiktionary transcriptions. [eSpeak NG command reference](https://raw.githubusercontent.com/espeak-ng/espeak-ng/master/src/espeak-ng.1.ronn)
3. **Ties and modifiers.** U+0361, U+035C and U+200D join complete components, including chains and marks on either side. Combining marks, aspiration, length, ejective and other postfix marks stay with their base. Allowed preposed modifiers attach to a following base at the start of a segment. A repeated consonant remains two sounds unless the source explicitly groups them. The attested spellings `n ̩` and `p⁽ʲ⁾` keep the intervening notation inside the host token. A spaced combining diacritic also receives an audit flag.
4. **Language inventory.** English groups its audited diphthongs, including `əʊ` and centering vowels, and the affricates `tʃ dʒ`. German also groups `pf ts` and uses its own vowel combinations, including `aɪ aʊ ɔʏ oʏ ʊɪ`. Turkish groups `tʃ dʒ` and keeps adjacent unmarked vowels separate. An explicit nonsyllabic vowel or tie supplies additional evidence. Syllable boundaries interrupt matching. The single legacy glyphs `ʦ ʧ ʤ` retain their spelling. Untied inventory merges and plausible alternative splits are recorded. An unmarked `dz` can remain two sounds. An explicitly tied `d͡z` is one. The inventory cannot resolve all dialect, hiatus or compound boundaries.
5. **Stress, notation and unknowns.** Preceding stress marks travel with the following segment. A stress mark separated from its host remains visible and is flagged. Tone marks, spaces, liaison, syllable boundaries, wrappers and optional delimiters survive. Opaque symbols remain in the raw audit with their code points and an example. Database builds reject unknowns with file, word and pronunciation context. A successful round trip says nothing about whether a suspicious spelling is good IPA.

### Storage and queries

ASCII space separates fields. A source whitespace code point is encoded as `\u{hex}`, using lowercase hexadecimal without zero padding. A literal backslash is doubled. Empty IPA encodes as an empty key. Bracket, parenthesis and angle delimiter tokens are mirrored for display in the reversed orientation. The decoder reverses that mapping. Slashes are symmetric. A well-formed wrapper consequently stays paired. The serializer performs no Unicode normalization. A decomposed base plus diacritic remains decomposed, including its combining-mark order.

Format 2 uses the original four columns and two indexes, with `PRAGMA user_version = 2`. Old databases used version 0 and omitted information from the search key. Rebuild from the eligible wordlists or from the original `ipa` values with the appropriate source metadata. Splitting an old key cannot recover the missing boundaries. The vowel-only `assonance_reversed` column still uses the established normalized analysis.

For a prefix search, bind an entire serialized token prefix. Match equality or append one ASCII space followed by the SQL wildcard. Escape literal backslash, percent and underscore before using `LIKE … ESCAPE`. Include the wrapper field for wrapped records, e.g. the final sound /t/ has candidate prefixes `t`, `/ t` and `[ t` in plain, phonemic and phonetic entries respectively. Stress is part of its host token. Consumers that intentionally ignore notation or stress must account for those cases. An exact serialized prefix distinguishes German `t s` from `ts`.

## Validation and research

The full build audits the raw Kaikki projections and canonical eSpeak lists. The existing-list rebuild audits the supplied canonical lists. Both produce an eligible-input audit alongside each database. The database writer checks source reconstruction, exact serialized token identity, and restored spelling before inserting a row. It publishes the database and JSON report together after SQLite integrity checks. Reports contain pronunciation and unique-IPA counts, token and segment distributions, notation frequencies, boundary evidence, and grouped diagnostics. Raw audits count non-text fields separately. To reproduce a raw audit or a source-specific build from the repository root:

```bash
python3 -B scripts/ipa_audit.py out/de/raw_de_ipa.jsonl \
  out/de/reports/ipa_reversal/wiktionary_source.json --lang-code de --source wiktionary --raw-jsonl
python3 -B scripts/ipa_audit.py out/de/wordlist_de_espeak_ipa.txt \
  out/de/reports/ipa_reversal/espeak_source.json --lang-code de --source espeak
python3 -B scripts/generate_rhyme_db.py out/de/wordlist_de_espeak_rhyme_eligible.txt \
  /tmp/de_espeak_example.db --release-version example --lang-code de --source espeak --boundary-espeak espeak-ng
```

The implementation uses Python's standard library and the project's language inventories. Grapheme boundaries describe text elements, while IPA ties and language-specific multigraphs add phonetic structure. Canonical normalization can change the binary representation, so the lossless scanner only normalizes a temporary character for inventory lookup. [Unicode segmentation](https://unicode.org/reports/tr29/) and [normalization](https://unicode.org/reports/tr15/)

The surveyed libraries informed that choice. ipatok groups ties and modifiers but normally omits whitespace and suprasegmentals. ipa-parser distinguishes sounds, breaks and unknowns and supports configurable combinations, but its symbol representation normalizes Unicode. [ipatok](https://github.com/pavelsof/ipatok) and [ipa-parser](https://github.com/danmysak/ipa-parser)

Gruut and gruut-ipa distinguish phone analysis from mapping into a language's phoneme inventory. PanPhon maps segments to features for phonological comparisons. LingPy exposes configurable handling for vowel merging, gemination, brackets and splitters. The release needs its own original-spelling fields and versioned SQLite codec in addition to those analysis capabilities. [Gruut](https://github.com/rhasspy/gruut), [gruut-ipa](https://github.com/rhasspy/gruut-ipa), [PanPhon](https://github.com/dmort27/panphon) and [LingPy](https://lingpy.github.io/reference/lingpy.sequence.html)

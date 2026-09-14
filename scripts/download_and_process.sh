#!/usr/bin/env bash
# Download the English, German, and Turkish Wiktionary-edition extracts, then
# merge English-, German-, and Turkish-language entries found in all dumps.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname -- "$SCRIPT_DIR")"
RAW_DIR="$PROJECT_DIR/raw"
OUT_DIR="$PROJECT_DIR/out"
DE_ARCHIVE="$RAW_DIR/de-extract.jsonl.gz"
TR_ARCHIVE="$RAW_DIR/tr-extract.jsonl.gz"
EN_ARCHIVE="$RAW_DIR/raw-wiktextract-data.jsonl.gz"
DE_URL="https://kaikki.org/dictionary/downloads/de/de-extract.jsonl.gz"
TR_URL="https://kaikki.org/dictionary/downloads/tr/tr-extract.jsonl.gz"
EN_URL="https://kaikki.org/dictionary/raw-wiktextract-data.jsonl.gz"

mkdir -p "$RAW_DIR" "$OUT_DIR"

download() {
    local url="$1"
    local destination="$2"
    local etag_file="${destination}.etag"
    local remote_etag
    local saved_etag=""
    local downloaded_etag=""

    remote_etag="$(
        curl --fail --head --location --retry 3 --silent --show-error "$url" |
            awk '/^[Ee][Tt][Aa][Gg]:/ {
                    sub(/\r$/, "")
                    sub(/^[^:]*:[[:space:]]*/, "")
                    print
                }' |
            tail -n 1
    )"

    if [[ -f "$etag_file" ]]; then
        IFS= read -r saved_etag < "$etag_file" || true
    fi

    if [[ -s "$destination" && -n "$remote_etag" && "$saved_etag" == "$remote_etag" ]]; then
        echo "Up to date: $(basename -- "$destination") ($remote_etag)"
        return 0
    fi

    if [[ -z "$remote_etag" ]]; then
        echo "No ETag received; downloading $(basename -- "$destination")..."
    else
        echo "Update available: $(basename -- "$destination") ($remote_etag)"
    fi

    # Keep the previous complete archive until its replacement has downloaded.
    if ! curl --fail --location --retry 3 \
        --etag-save "$etag_file.part" \
        --output "$destination.part" \
        "$url"; then
        rm -f -- "$destination.part" "$etag_file.part"
        echo "Download failed; previous archive was kept: $destination" >&2
        return 1
    fi

    if [[ ! -s "$destination.part" ]]; then
        rm -f -- "$destination.part" "$etag_file.part"
        echo "Downloaded archive is empty: $destination.part" >&2
        return 1
    fi

    if [[ -f "$etag_file.part" ]]; then
        IFS= read -r downloaded_etag < "$etag_file.part" || true
    fi
    if [[ -z "$downloaded_etag" ]]; then
        downloaded_etag="$remote_etag"
    fi

    mv -- "$destination.part" "$destination"
    printf '%s\n' "$downloaded_etag" > "$etag_file.new"
    mv -- "$etag_file.new" "$etag_file"
    rm -f -- "$etag_file.part"
}

download "$DE_URL" "$DE_ARCHIVE"
download "$TR_URL" "$TR_ARCHIVE"
download "$EN_URL" "$EN_ARCHIVE"

echo "Merging lang_code=en, lang_code=de, and lang_code=tr entries from all editions..."
python3 "$SCRIPT_DIR/extract_ipa.py" \
    "$DE_ARCHIVE" \
    "$TR_ARCHIVE" \
    "$EN_ARCHIVE" \
    --lang-code en \
    --lang-code de \
    --lang-code tr \
    --latin-headwords-only \
    --outdir "$OUT_DIR"

echo "Done:"
echo "  $OUT_DIR/en/wordlist_en_ipa.txt"
echo "  $OUT_DIR/en/wordlist_en_noipa.txt"
echo "  $OUT_DIR/de/wordlist_de_ipa.txt"
echo "  $OUT_DIR/de/wordlist_de_noipa.txt"
echo "  $OUT_DIR/tr/wordlist_tr_ipa.txt"
echo "  $OUT_DIR/tr/wordlist_tr_noipa.txt"

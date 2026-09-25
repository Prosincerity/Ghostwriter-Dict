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
RELEASE_VERSION=""
SKIP_DOWNLOAD=false
REPLACE_EXTREME_MISMATCHES=true
EXTREME_DISTANCE=""

usage() {
    cat <<EOF
Usage: $(basename -- "$0") [OPTIONS]

Build canonical wordlists, eSpeak pronunciations, cleaned product lists, six
SQLite databases, and their gzip release assets for English, German, and Turkish.

Options:
  --release-version VERSION  Override the slug derived from the English date
  --skip-download            Reuse local archives (requires --release-version)
  --replace-extreme-mismatches  Move extreme IPA mismatches to eSpeak output (default)
  --report-only             Audit mismatches without replacing IPA
  --extreme-distance NUMBER  Comparison threshold from 0 to 1 (default: 0.8)
  -h, --help                 Show this help
EOF
}

while (( $# > 0 )); do
    case "$1" in
        --release-version)
            if (( $# < 2 )); then
                echo "error: --release-version requires a value" >&2
                exit 2
            fi
            RELEASE_VERSION="$2"
            shift 2
            ;;
        --skip-download)
            SKIP_DOWNLOAD=true
            shift
            ;;
        --replace-extreme-mismatches)
            REPLACE_EXTREME_MISMATCHES=true
            shift
            ;;
        --report-only)
            REPLACE_EXTREME_MISMATCHES=false
            shift
            ;;
        --extreme-distance)
            if (( $# < 2 )); then
                echo "error: --extreme-distance requires a value" >&2
                exit 2
            fi
            EXTREME_DISTANCE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "$SKIP_DOWNLOAD" == true && -z "$RELEASE_VERSION" ]]; then
    echo "error: --skip-download requires --release-version" >&2
    exit 2
fi
if [[ -n "$RELEASE_VERSION" && ! "$RELEASE_VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]]; then
    echo "error: invalid release version: $RELEASE_VERSION" >&2
    exit 2
fi

mkdir -p "$RAW_DIR" "$OUT_DIR"

download() {
    local url="$1"
    local destination="$2"
    local release_date_variable="$3"
    local etag_file="${destination}.etag"
    local headers
    local remote_etag
    local remote_last_modified
    local remote_release_date
    local saved_etag=""
    local downloaded_etag=""

    headers="$(curl --fail --head --location --retry 3 --silent --show-error "$url")"
    remote_etag="$(
        awk '/^[Ee][Tt][Aa][Gg]:/ {
                sub(/\r$/, "")
                sub(/^[^:]*:[[:space:]]*/, "")
                value = $0
            }
            END { print value }' <<< "$headers"
    )"
    remote_last_modified="$(
        awk '/^[Ll][Aa][Ss][Tt]-[Mm][Oo][Dd][Ii][Ff][Ii][Ee][Dd]:/ {
                sub(/\r$/, "")
                sub(/^[^:]*:[[:space:]]*/, "")
                value = $0
            }
            END { print value }' <<< "$headers"
    )"
    if [[ -z "$RELEASE_VERSION" && -n "$release_date_variable" ]]; then
        if [[ -z "$remote_last_modified" ]]; then
            echo "No Last-Modified date received for $url" >&2
            return 1
        fi
        if ! remote_release_date="$(
            date --utc --date "$remote_last_modified" +%Y%m%d
        )"; then
            echo "Invalid Last-Modified date for $url: $remote_last_modified" >&2
            return 1
        fi
        printf -v "$release_date_variable" '%s' "$remote_release_date"
    fi

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

if [[ "$SKIP_DOWNLOAD" == false ]]; then
    EN_RELEASE_DATE=""
    download "$DE_URL" "$DE_ARCHIVE" ""
    download "$TR_URL" "$TR_ARCHIVE" ""
    download "$EN_URL" "$EN_ARCHIVE" EN_RELEASE_DATE
    if [[ -z "$RELEASE_VERSION" ]]; then
        RELEASE_VERSION="kaikki-v${EN_RELEASE_DATE}"
    fi
else
    for archive in "$DE_ARCHIVE" "$TR_ARCHIVE" "$EN_ARCHIVE"; do
        if [[ ! -s "$archive" ]]; then
            echo "error: missing local archive: $archive" >&2
            exit 1
        fi
    done
fi

echo "Kaikki release: $RELEASE_VERSION"

IPA_OPTIONS=()
if [[ "$REPLACE_EXTREME_MISMATCHES" == true ]]; then
    IPA_OPTIONS+=(--replace-extreme-mismatches)
fi
if [[ -n "$EXTREME_DISTANCE" ]]; then
    IPA_OPTIONS+=(--extreme-distance "$EXTREME_DISTANCE")
fi

echo "Merging lang_code=en, lang_code=de, and lang_code=tr entries from all editions..."
python3 "$SCRIPT_DIR/extract_ipa.py" \
    "$DE_ARCHIVE" \
    "$TR_ARCHIVE" \
    "$EN_ARCHIVE" \
    --lang-code en \
    --lang-code de \
    --lang-code tr \
    --outdir "$OUT_DIR"

echo "Generating eSpeak IPA for words without Wiktionary IPA..."
python3 "$SCRIPT_DIR/generate_espeak_ipa.py" --outdir "$OUT_DIR" \
    --source-archive "$DE_ARCHIVE" \
    --source-archive "$TR_ARCHIVE" \
    --source-archive "$EN_ARCHIVE"

for lang_code in en de tr; do
    language_dir="$OUT_DIR/$lang_code"
    echo "Cleaning $lang_code Wiktionary headwords..."
    python3 "$SCRIPT_DIR/clean_rhyme_words.py" \
        "$language_dir/wordlist_${lang_code}_ipa.txt" \
        "$language_dir/wordlist_${lang_code}_wiktionary_words_cleaned.txt" \
        --lang-code "$lang_code"

    echo "Cleaning $lang_code eSpeak headwords..."
    python3 "$SCRIPT_DIR/clean_rhyme_words.py" \
        "$language_dir/wordlist_${lang_code}_espeak_ipa.txt" \
        "$language_dir/wordlist_${lang_code}_espeak_words_cleaned.txt" \
        --lang-code "$lang_code"

    echo "Validating and repairing $lang_code IPA..."
    python3 "$SCRIPT_DIR/clean_rhyme_ipa.py" \
        "$language_dir/wordlist_${lang_code}_wiktionary_words_cleaned.txt" \
        "$language_dir/wordlist_${lang_code}_espeak_words_cleaned.txt" \
        "$language_dir/wordlist_${lang_code}_rhyme_eligible.txt" \
        "$language_dir/wordlist_${lang_code}_espeak_rhyme_eligible.txt" \
        --lang-code "$lang_code" \
        "${IPA_OPTIONS[@]}"

    echo "Building $lang_code Wiktionary database..."
    python3 "$SCRIPT_DIR/generate_rhyme_db.py" \
        "$language_dir/wordlist_${lang_code}_rhyme_eligible.txt" \
        "$language_dir/${lang_code}_${RELEASE_VERSION}.db" \
        --lang-code "$lang_code" \
        --release-version "$RELEASE_VERSION"

    echo "Building $lang_code eSpeak database..."
    python3 "$SCRIPT_DIR/generate_rhyme_db.py" \
        "$language_dir/wordlist_${lang_code}_espeak_rhyme_eligible.txt" \
        "$language_dir/${lang_code}_espeak_${RELEASE_VERSION}.db" \
        --lang-code "$lang_code" \
        --release-version "$RELEASE_VERSION"
done

echo "Packaging release archives and checksums..."
python3 "$SCRIPT_DIR/package_release.py" --release-version "$RELEASE_VERSION"

echo "Finished databases and release archives:"
for lang_code in en de tr; do
    echo "  $OUT_DIR/$lang_code/${lang_code}_${RELEASE_VERSION}.db"
    echo "  $OUT_DIR/$lang_code/${lang_code}_${RELEASE_VERSION}.db.gz"
    echo "  $OUT_DIR/$lang_code/${lang_code}_espeak_${RELEASE_VERSION}.db"
    echo "  $OUT_DIR/$lang_code/${lang_code}_espeak_${RELEASE_VERSION}.db.gz"
done
echo "  $OUT_DIR/SHA256SUMS"

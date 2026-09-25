#!/usr/bin/env bash
# Reapply product cleanup and rebuild databases from existing IPA wordlists.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname -- "$SCRIPT_DIR")"
OUT_DIR="$PROJECT_DIR/out"
RELEASE_VERSION=""
REPLACE_EXTREME_MISMATCHES=true
EXTREME_DISTANCE=""

usage() {
    cat <<EOF
Usage: $(basename -- "$0") --release-version VERSION

Clean the existing Wiktionary and eSpeak IPA wordlists, regenerate malformed
or unstressed Wiktionary IPA with eSpeak NG, then rebuild all six databases.
This command does not download archives or extract Wiktionary data.

Options:
  --release-version VERSION  Release slug used in database filenames (required)
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

if [[ -z "$RELEASE_VERSION" ]]; then
    echo "error: --release-version is required" >&2
    exit 2
fi
if [[ ! "$RELEASE_VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]]; then
    echo "error: invalid release version: $RELEASE_VERSION" >&2
    exit 2
fi

for lang_code in en de tr; do
    language_dir="$OUT_DIR/$lang_code"
    for input_path in \
        "$language_dir/wordlist_${lang_code}_ipa.txt" \
        "$language_dir/wordlist_${lang_code}_espeak_ipa.txt"; do
        if [[ ! -s "$input_path" ]]; then
            echo "error: missing or empty IPA wordlist: $input_path" >&2
            exit 1
        fi
    done
done

echo "Rebuilding cleaned lists and databases for $RELEASE_VERSION"
IPA_OPTIONS=()
if [[ "$REPLACE_EXTREME_MISMATCHES" == true ]]; then
    IPA_OPTIONS+=(--replace-extreme-mismatches)
fi
if [[ -n "$EXTREME_DISTANCE" ]]; then
    IPA_OPTIONS+=(--extreme-distance "$EXTREME_DISTANCE")
fi
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

echo "Finished databases:"
for lang_code in en de tr; do
    echo "  $OUT_DIR/$lang_code/${lang_code}_${RELEASE_VERSION}.db"
    echo "  $OUT_DIR/$lang_code/${lang_code}_espeak_${RELEASE_VERSION}.db"
done

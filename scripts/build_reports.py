"""Small atomic build reports and archive fingerprints shared by pipeline steps."""

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_sources(paths: list[Path]) -> list[dict[str, object]]:
    sources = []
    for path in paths:
        metadata = path.stat()
        etag_path = Path(f"{path}.etag")
        sources.append({
            "file": str(path.resolve()),
            "etag": etag_path.read_text(encoding="utf-8").strip() if etag_path.is_file() else None,
            "size_bytes": metadata.st_size,
            "modified_ns": metadata.st_mtime_ns,
        })
    return sources


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.part")
    try:
        with part.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
        os.replace(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Record full-build download or cleanup step.")
    parser.add_argument("stage", choices=("downloading", "cleaning"))
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--archive", type=Path, action="append", default=[])
    parser.add_argument("--status", action="append", default=[])
    parser.add_argument("--lang-code", choices=("en", "de", "tr"))
    parser.add_argument("--comparison-mode")
    parser.add_argument("--extreme-distance")
    args = parser.parse_args()
    if args.stage == "downloading":
        statuses = dict(item.split("=", 1) for item in args.status)
        report = {
            "release_version": args.release_version,
            "archives": archive_sources(args.archive),
            "status": statuses,
        }
        for lang in ("en", "de", "tr"):
            write_json(args.outdir / lang / "reports" / "downloading" / "report.json", report)
    else:
        if not args.lang_code:
            parser.error("cleaning requires --lang-code")
        lang = args.lang_code
        reports = args.outdir / lang / "reports" / "cleaning"
        names = (
            f"wordlist_{lang}_wiktionary_words_cleaned_report.json",
            f"wordlist_{lang}_espeak_words_cleaned_report.json",
            f"wordlist_{lang}_rhyme_eligible_ipa_report.json",
        )
        step_reports = {}
        for name in names:
            step_reports[name] = json.loads((reports / name).read_text(encoding="utf-8"))
        write_json(reports / "report.json", {
            "release_version": args.release_version,
            "comparison_mode": args.comparison_mode,
            "extreme_distance": args.extreme_distance or "0.8",
            "step_reports": step_reports,
        })


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Create maximum-compression gzip release assets and a SHA256SUMS manifest."""

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile


PROJECT_DIR = Path(__file__).resolve().parents[1]


def staged_file(destination: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".part", dir=destination.parent
    )
    os.close(descriptor)
    return Path(name)


def compress_database(database: Path, index: int, total: int) -> tuple[Path, Path, str]:
    archive = database.with_name(f"{database.name}.gz")
    archive_part = staged_file(archive)
    try:
        processed = 0
        with database.open("rb") as source, archive_part.open("wb") as target:
            with gzip.GzipFile(
                filename="", fileobj=target, mode="wb", compresslevel=9, mtime=0
            ) as compressed:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    compressed.write(chunk)
                    processed += len(chunk)
                    if processed % (16 * 1024 * 1024) < len(chunk):
                        print(
                            f"\rCompressing {index}/{total}: {database.name} "
                            f"({processed:,} bytes)",
                            end="",
                            file=sys.stderr,
                            flush=True,
                        )

        digest = hashlib.sha256()
        with archive_part.open("rb") as compressed:
            for chunk in iter(lambda: compressed.read(1024 * 1024), b""):
                digest.update(chunk)

        return archive_part, archive, digest.hexdigest()
    except BaseException:
        archive_part.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-version", required=True, help="release slug in database filenames"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", args.release_version):
        parser.error("invalid release version")

    databases = [
        PROJECT_DIR / "out" / lang / f"{lang}{source}_{args.release_version}.db"
        for lang in ("en", "de", "tr")
        for source in ("", "_espeak")
    ]
    missing = [
        str(path) for path in databases if not path.is_file() or path.stat().st_size == 0
    ]
    if missing:
        parser.error("missing or empty databases:\n  " + "\n  ".join(missing))

    manifest = PROJECT_DIR / "out" / "SHA256SUMS"
    staged = []
    manifest_part = None
    try:
        for index, database in enumerate(sorted(databases), 1):
            print(
                f"\rCompressing {index}/{len(databases)}: {database.name}",
                end="",
                file=sys.stderr,
                flush=True,
            )
            staged.append(compress_database(database, index, len(databases)))
        manifest_part = staged_file(manifest)
        manifest_part.write_text(
            "".join(
                f"{digest}  {archive.relative_to(manifest.parent)}\n"
                for _, archive, digest in staged
            ),
            encoding="ascii",
        )
        for archive_part, archive, _ in staged:
            os.replace(archive_part, archive)
        os.replace(manifest_part, manifest)
    finally:
        for archive_part, _, _ in staged:
            archive_part.unlink(missing_ok=True)
        if manifest_part is not None:
            manifest_part.unlink(missing_ok=True)
    print(f"\rPackaged {len(databases)} archives; checksums: {manifest}", file=sys.stderr)


if __name__ == "__main__":
    main()

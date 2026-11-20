"""Organize the files directly inside a folder into category subfolders.

Guarantees:
  * Nothing is deleted or overwritten (name clashes get a " (1)", " (2)" suffix).
  * Only files directly inside the target folder are touched (no recursion).
  * Files only ever move into category subfolders of the target folder.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple

DEFAULT_TARGET_DIR = Path(r"C:\Users\name\folder")

# Single source of truth: category -> extensions. Sets give O(1) lookups and
# silently remove duplicates (".jpf" was listed twice in the original).
EXTENSIONS_BY_CATEGORY: dict[str, frozenset[str]] = {
    "Images": frozenset({
        ".jpg", ".jpeg", ".jpe", ".jif", ".jfif", ".jfi", ".png", ".gif",
        ".webp", ".tiff", ".tif", ".psd", ".raw", ".arw", ".cr2", ".nrw",
        ".k25", ".bmp", ".dib", ".heif", ".heic", ".ind", ".indd", ".indt",
        ".jp2", ".j2k", ".jpf", ".jpx", ".jpm", ".mj2", ".svg", ".svgz",
        ".ai", ".eps", ".ico",
    }),
    "Videos": frozenset({
        ".webm", ".mpg", ".mp2", ".mpeg", ".mpe", ".mpv", ".ogg", ".mp4",
        ".mp4v", ".m4v", ".avi", ".wmv", ".mov", ".qt", ".flv", ".swf",
        ".avchd",
    }),
    "Audio": frozenset({".m4a", ".flac", ".mp3", ".wav", ".wma", ".aac"}),
    "Documents": frozenset({
        ".doc", ".docx", ".odt", ".pdf", ".xls", ".xlsx", ".ppt", ".pptx",
        ".zip",
    }),
}
FALLBACK_CATEGORY = "Others"

# Built once at import time instead of scanning lists for every file.
CATEGORY_BY_EXTENSION = {
    ext: category
    for category, extensions in EXTENSIONS_BY_CATEGORY.items()
    for ext in extensions
}
CATEGORY_DIRS = (*EXTENSIONS_BY_CATEGORY, FALLBACK_CATEGORY)

# So the script never moves itself if it lives in the folder it organizes.
SCRIPT_PATH = Path(__file__).resolve()

log = logging.getLogger("organizer")


class Result(NamedTuple):
    moved: int
    failed: int


def category_for(path: Path) -> str:
    """Return the category folder name for a file, based on its extension."""
    return CATEGORY_BY_EXTENSION.get(path.suffix.lower(), FALLBACK_CATEGORY)


def ensure_category_dirs(root: Path) -> None:
    """Create the category folders, refusing any that would escape `root`."""
    for name in CATEGORY_DIRS:
        folder = root / name
        # SAFETY: if "Images" is a symlink/junction to somewhere else, moving
        # files into it would leave the target folder. resolve() follows links.
        if folder.resolve().parent != root:
            raise OSError(f"{folder} points outside {root}; refusing to use it")
        if not folder.exists():
            folder.mkdir()
            log.info("Folder %s created", name)
        elif not folder.is_dir():
            raise NotADirectoryError(f"{folder} exists but is not a folder")


def list_files(root: Path) -> list[Path]:
    """Regular files directly inside `root` (no folders, no symlinks)."""
    files = []
    # scandir reuses the OS directory listing for type checks, so there is no
    # extra stat call per file (the original called isdir() on every item).
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            path = Path(entry.path)
            if path != SCRIPT_PATH:
                files.append(path)
    # Materialized (and sorted) BEFORE moving: don't change a directory
    # while iterating over it.
    return sorted(files)


def unique_destination(dest_dir: Path, filename: str) -> Path:
    """Return a path in `dest_dir` that doesn't exist yet: 'a.jpg', 'a (1).jpg'..."""
    candidate = dest_dir / filename
    if not os.path.lexists(candidate):  # lexists also catches broken symlinks
        return candidate
    stem, suffix = Path(filename).stem, Path(filename).suffix
    counter = 1
    while True:
        candidate = dest_dir / f"{stem} ({counter}){suffix}"
        if not os.path.lexists(candidate):
            return candidate
        counter += 1


def log_summary(per_category: Counter, moved: int, failed: int,
                elapsed: float, dry_run: bool) -> None:
    """Print a per-category breakdown plus totals and elapsed time."""
    log.info("-" * 34)
    for category in CATEGORY_DIRS:
        if per_category[category]:
            log.info("  %-10s %5d", category, per_category[category])
    verb = "would be moved" if dry_run else "moved"
    log.info("%d %s, %d failed (%.2fs)", moved, verb, failed, elapsed)


def organize(target_dir: Path, dry_run: bool = False, show_each: bool = True) -> Result:
    """Move each file in `target_dir` into its category folder.

    show_each=False hides the per-file progress lines (the summary still prints).
    """
    target_dir = Path(target_dir)
    if not target_dir.is_dir():
        raise NotADirectoryError(f"The directory {target_dir} does not exist.")
    root = target_dir.resolve()

    if not dry_run:
        ensure_category_dirs(root)

    files = list_files(root)
    total = len(files)
    width = len(str(total))  # pads the counter so lines stay aligned: [ 3/12]
    log.info("Organizing %s%s", root, "  (DRY RUN - nothing will be moved)" if dry_run else "")
    if not files:
        log.info("No files to organize")
        return Result(0, 0)
    log.info("Found %d file(s)", total)

    started = time.perf_counter()
    per_category: Counter = Counter()
    moved = failed = 0
    for index, source in enumerate(files, start=1):
        category = category_for(source)
        dest_dir = root / category
        try:
            destination = unique_destination(dest_dir, source.name)
            if not dry_run:
                # Same filesystem, so this is an instant rename. Unlike
                # shutil.move it never falls back to copy-then-delete, which
                # could leave duplicates if the delete step fails.
                source.rename(destination)
        except OSError as exc:
            # One bad file must not abort the rest of the run.
            failed += 1
            log.error("[%*d/%d] FAILED  %s: %s", width, index, total, source.name, exc)
            continue
        moved += 1
        per_category[category] += 1
        if show_each:
            note = f"  (renamed: {destination.name})" if destination.name != source.name else ""
            log.info("[%*d/%d] %s -> %s/%s", width, index, total, source.name, category, note)

    log_summary(per_category, moved, failed, time.perf_counter() - started, dry_run)
    return Result(moved, failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", nargs="?", type=Path, default=DEFAULT_TARGET_DIR,
                        help="folder to organize (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would happen without moving anything")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="hide per-file lines; only show the summary")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        result = organize(args.target, dry_run=args.dry_run, show_each=not args.quiet)
    except (OSError, NotADirectoryError) as exc:  # missing dir, permissions, unsafe folder
        log.error("%s", exc)
        return 1
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())

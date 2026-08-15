"""Future-proof safe deletion helper for temporary local reference files."""

from pathlib import Path


class UnsafeReferencePathError(ValueError):
    """Raised when a candidate escapes the explicitly configured storage root."""


def safe_unlink_within_root(storage_root: Path, candidate: Path) -> int:
    """Unlink one file inside root and return its size; missing files are successful."""

    root = storage_root.resolve(strict=True)
    resolved = candidate.resolve(strict=False)
    if resolved == root or not resolved.is_relative_to(root):
        raise UnsafeReferencePathError("reference path is outside storage root")
    if not resolved.exists():
        return 0
    if not resolved.is_file():
        raise UnsafeReferencePathError("reference path is not a regular file")
    size = resolved.stat().st_size
    resolved.unlink(missing_ok=True)
    return size

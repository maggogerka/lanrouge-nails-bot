"""Filesystem containment guarantees for optional temporary references."""

from pathlib import Path

import pytest

from app.services.reference_file_cleanup import (
    UnsafeReferencePathError,
    safe_unlink_within_root,
)


def test_missing_local_file_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "references"
    root.mkdir()

    assert safe_unlink_within_root(root, root / "missing.jpg") == 0


def test_file_inside_root_is_deleted_and_size_returned(tmp_path: Path) -> None:
    root = tmp_path / "references"
    root.mkdir()
    photo = root / "photo.jpg"
    photo.write_bytes(b"12345")

    assert safe_unlink_within_root(root, photo) == 5
    assert not photo.exists()


def test_path_traversal_outside_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "references"
    root.mkdir()
    outside = tmp_path / "private.jpg"
    outside.write_bytes(b"do-not-delete")

    with pytest.raises(UnsafeReferencePathError, match="outside"):
        safe_unlink_within_root(root, root / ".." / "private.jpg")

    assert outside.exists()


def test_symlink_to_outside_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "references"
    root.mkdir()
    outside = tmp_path / "private.jpg"
    outside.write_bytes(b"do-not-delete")
    link = root / "link.jpg"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(UnsafeReferencePathError, match="outside"):
        safe_unlink_within_root(root, link)

    assert outside.exists()

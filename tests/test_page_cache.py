"""Tests for releasing the astrometry index page cache after a solve.

``solve-field`` mmaps the index tiles it reads and the kernel keeps them resident in the OS
page cache after the subprocess exits; across many solves this grows without bound toward the
full index size. ``_release_index_page_cache`` advises ``POSIX_FADV_DONTNEED`` to drop those
clean pages. These tests confirm the advice is issued for every index file and that the call
degrades to a safe no-op where ``posix_fadvise`` is unavailable.
"""

import os
from pathlib import Path

import pytest

import astroeasy.dotnet.runner as runner
from astroeasy import AstrometryConfig, Detection, ImageMetadata
from astroeasy.dotnet.runner import _release_index_page_cache


def _make_index_dir(tmp_path: Path, n: int = 3) -> list[Path]:
    """Create ``n`` fake index ``.fits`` files (plus a decoy non-index file) under ``tmp_path``.

    Args:
        tmp_path: Directory to populate.
        n: Number of index files to create.

    Returns:
        The paths of the created index files.
    """
    index_files = []
    for i in range(n):
        index_file = tmp_path / f"index-520{i}.fits"
        index_file.write_bytes(b"\x00" * 4096)
        index_files.append(index_file)
    (tmp_path / "notes.txt").write_text("not an index file")
    return index_files


@pytest.mark.skipif(
    not hasattr(os, "posix_fadvise"),
    reason="posix_fadvise not available on this platform",
)
def test_release_advises_dontneed_on_every_index_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every index ``.fits`` (and only those) is advised whole-file ``POSIX_FADV_DONTNEED``."""
    index_files = _make_index_dir(tmp_path)
    advised: list[tuple[int, int, int]] = []
    real_fadvise = os.posix_fadvise

    def _spy(fd: int, offset: int, length: int, advice: int) -> None:
        advised.append((offset, length, advice))
        real_fadvise(fd, offset, length, advice)

    monkeypatch.setattr(os, "posix_fadvise", _spy)
    _release_index_page_cache(tmp_path)

    assert len(advised) == len(index_files)
    assert all(
        offset == 0 and length == 0 and advice == os.POSIX_FADV_DONTNEED
        for offset, length, advice in advised
    )


def test_release_is_noop_without_posix_fadvise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On platforms lacking ``posix_fadvise`` (macOS, Windows) the call is a safe no-op."""
    _make_index_dir(tmp_path)
    monkeypatch.delattr(os, "posix_fadvise", raising=False)
    _release_index_page_cache(tmp_path)  # must not raise


def test_release_is_noop_for_none_path() -> None:
    """A ``None`` indices path (e.g. some Docker setups) is a safe no-op."""
    _release_index_page_cache(None)  # must not raise


def test_release_index_page_cache_defaults_on_and_roundtrips(tmp_path: Path) -> None:
    """The opt-out flag defaults to True and round-trips through to_dict/from_dict."""
    cfg = AstrometryConfig(indices_path=tmp_path)
    assert cfg.release_index_page_cache is True
    restored = AstrometryConfig.from_dict({**cfg.to_dict(), "release_index_page_cache": False})
    assert restored.release_index_page_cache is False


def _detections(n: int = 8) -> list[Detection]:
    """Return ``n`` synthetic detections (enough to attempt a solve)."""
    return [Detection(x=float(10 * i), y=float(8 * i), flux=float(1000 - i)) for i in range(n)]


@pytest.mark.parametrize("release_enabled", [True, False])
def test_solve_field_respects_release_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, release_enabled: bool
) -> None:
    """solve_field releases the index page cache only when release_index_page_cache is True.

    The real solve is stubbed out (``run_against_local`` -> False) and the release helper is
    spied, so this exercises only the config-gated call in the ``finally`` block.
    """
    calls: list[object] = []
    monkeypatch.setattr(runner, "_release_index_page_cache", lambda p: calls.append(p))
    monkeypatch.setattr(runner, "run_against_local", lambda *a, **k: False)

    cfg = AstrometryConfig(indices_path=tmp_path, release_index_page_cache=release_enabled)
    runner.solve_field(_detections(), ImageMetadata(width=1024, height=1024), cfg)

    if release_enabled:
        assert calls, "release must run when release_index_page_cache is True"
    else:
        assert calls == [], "release must be skipped when release_index_page_cache is False"

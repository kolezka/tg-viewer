"""Unit tests for tool/tg_daemon.py — retention policy + snapshot discovery."""
from __future__ import annotations

import os
import time
from pathlib import Path

from tool.tg_daemon import (
    _bucket,
    _find_latest_backup,
    _list_snapshots,
    plan_pruning,
)


def _snap(root: Path, name: str, mtime: float) -> Path:
    p = root / name
    p.mkdir()
    os.utime(p, (mtime, mtime))
    return p


def test_bucket_recent(monkeypatch):
    now = 1_000_000.0
    # Anything <= 4h is "recent" with non-colliding bucket key (id=0 for all).
    assert _bucket(0, now)[0] == "recent"
    assert _bucket(3600, now)[0] == "recent"
    assert _bucket(4 * 3600, now)[0] == "recent"


def test_bucket_hourly():
    now = 1_000_000.0
    g, b = _bucket(5 * 3600, now)  # 5h ago
    assert g == "hourly"
    assert isinstance(b, int)
    # Two snapshots in the same hour collide; different hours don't.
    assert _bucket(5 * 3600, now)[1] == _bucket(5 * 3600 + 600, now)[1]
    assert _bucket(5 * 3600, now)[1] != _bucket(6 * 3600 + 600, now)[1]


def test_bucket_daily_then_expired():
    now = 1_000_000.0
    assert _bucket(2 * 86400, now)[0] == "daily"
    assert _bucket(30 * 86400, now)[0] == "daily"
    assert _bucket(31 * 86400, now)[0] == "expired"


def test_plan_pruning_keeps_all_recent(tmp_path: Path):
    now = time.time()
    # Five snapshots within the last 4h — all stay
    snaps = [_snap(tmp_path, f"tg_{i}", now - i * 600) for i in range(5)]
    pruned = plan_pruning(snaps, now)
    assert pruned == []


def test_plan_pruning_dedupes_hourly(tmp_path: Path):
    # Pin `now` to a clean hour boundary so the three test snapshots can
    # only fall into one bucket regardless of when the test runs.
    now = float(int(time.time()) // 3600 * 3600)
    # Three snapshots ~5h old (same hourly bucket): newest survives,
    # the other two get pruned. Spacings are well under the 3600s bucket.
    a = _snap(tmp_path, "tg_a", now - 5 * 3600 - 1200)
    b = _snap(tmp_path, "tg_b", now - 5 * 3600 -  600)
    c = _snap(tmp_path, "tg_c", now - 5 * 3600 -   60)
    pruned = plan_pruning([a, b, c], now)
    assert set(pruned) == {a, b}


def test_plan_pruning_expires_old(tmp_path: Path):
    now = time.time()
    old = _snap(tmp_path, "tg_old", now - 40 * 86400)
    new = _snap(tmp_path, "tg_new", now - 600)
    pruned = plan_pruning([old, new], now)
    assert old in pruned
    assert new not in pruned


def test_find_latest_backup_picks_newest_flat(tmp_path: Path):
    now = time.time()
    older = _snap(tmp_path, "tg_old", now - 3600)
    newer = _snap(tmp_path, "tg_new", now - 60)
    assert _find_latest_backup(tmp_path) == newer
    # Ignore non-tg dirs and files
    (tmp_path / "junk").mkdir()
    (tmp_path / "tg_not_a_dir").write_text("file")
    assert _find_latest_backup(tmp_path) == newer


def test_find_latest_backup_returns_none_when_empty(tmp_path: Path):
    assert _find_latest_backup(tmp_path) is None


def test_list_snapshots_sorts_newest_first(tmp_path: Path):
    now = time.time()
    a = _snap(tmp_path, "tg_a", now - 100)
    b = _snap(tmp_path, "tg_b", now - 50)
    c = _snap(tmp_path, "tg_c", now - 200)
    assert _list_snapshots(tmp_path) == [b, a, c]

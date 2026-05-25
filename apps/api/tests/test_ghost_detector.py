"""Unit tests for tool/ghost_detector.py."""
from __future__ import annotations

from pathlib import Path

from tool.ghost_detector import (
    compare_snapshots,
    diff_snapshots,
    find_previous_snapshot,
    message_key,
)


def _msg(peer_id: int, ts: int, text: str = "", **extra) -> dict:
    return {"peer_id": peer_id, "timestamp": ts, "text": text, **extra}


def test_message_key_uses_namespace_when_present():
    a = _msg(100, 1000, "hi", namespace=3)
    b = _msg(100, 1000, "ho", namespace=3)  # same key — collisions OK with explicit ns
    assert message_key(a) == message_key(b) == (100, 1000, 3)


def test_message_key_falls_back_to_text_hash_when_namespace_absent():
    a = _msg(100, 1000, "hi")
    b = _msg(100, 1000, "ho")
    # Without a namespace, different text => different keys (no collision)
    assert message_key(a) != message_key(b)
    # Same text, same ts, same peer => same key
    c = _msg(100, 1000, "hi")
    assert message_key(a) == message_key(c)


def test_diff_snapshots_text_edit_appears_as_remove_plus_add():
    # When namespace is absent we hash the text into the key, so editing the
    # text changes the key — the edit surfaces as a remove+add pair rather
    # than a `modified` entry. That's fine forensically (the user sees the
    # before/after) and avoids brittle in-place edit detection.
    old = [
        _msg(1, 100, "stay"),
        _msg(1, 101, "removed-row"),
        _msg(2, 200, "before edit"),
    ]
    new = [
        _msg(1, 100, "stay"),
        _msg(2, 200, "after edit"),
        _msg(3, 300, "fresh"),
    ]
    d = diff_snapshots(old, new)
    removed_texts = sorted(r["text"] for r in d["removed"])
    added_texts = sorted(r["text"] for r in d["added"])
    assert removed_texts == ["before edit", "removed-row"]
    assert added_texts == ["after edit", "fresh"]


def test_diff_snapshots_modified_catches_peer_metadata_drift():
    # Same key (peer_id, timestamp, text-hash) but peer_name changed —
    # that's an in-place modification.
    old = [_msg(1, 100, "hi", peer_name="old-name", outgoing=False)]
    new = [_msg(1, 100, "hi", peer_name="new-name", outgoing=False)]
    d = diff_snapshots(old, new)
    assert d["removed"] == []
    assert d["added"] == []
    assert len(d["modified"]) == 1
    assert d["modified"][0]["old"]["peer_name"] == "old-name"
    assert d["modified"][0]["new"]["peer_name"] == "new-name"


def test_diff_snapshots_no_drift_returns_empty_lists():
    msgs = [_msg(1, 100, "a"), _msg(1, 200, "b")]
    d = diff_snapshots(msgs, list(msgs))
    assert d == {"removed": [], "added": [], "modified": []}


def test_diff_snapshots_handles_5_added_zero_removed():
    # Mirrors the real wal-recovery case: parser run with WAL excluded vs included
    # produced 9026 vs 9031 messages — should report 0 removed, 5 added.
    base = [_msg(1, i, f"m{i}") for i in range(10)]
    extra = [_msg(1, 100 + i, f"new{i}") for i in range(5)]
    d = diff_snapshots(base, base + extra)
    assert len(d["removed"]) == 0
    assert len(d["added"]) == 5


def test_find_previous_snapshot_picks_second_newest(tmp_path: Path):
    # Build a fake tg_*/tg_*/parsed_data layout, three snapshots with distinct mtimes.
    def make_snap(name: str, mtime: float) -> Path:
        d = tmp_path / f"tg_{name}" / f"tg_{name}" / "parsed_data"
        d.mkdir(parents=True)
        import os
        os.utime(d, (mtime, mtime))
        return d

    old = make_snap("old", 1000.0)
    mid = make_snap("mid", 2000.0)
    new = make_snap("new", 3000.0)

    prev = find_previous_snapshot(new, tmp_path)
    assert prev == mid  # newest one that's not `current`

    # When called from the oldest, picks the second-newest available (new still wins).
    prev = find_previous_snapshot(old, tmp_path)
    assert prev == new


def test_find_previous_snapshot_returns_none_when_alone(tmp_path: Path):
    only = tmp_path / "tg_only" / "tg_only" / "parsed_data"
    only.mkdir(parents=True)
    assert find_previous_snapshot(only, tmp_path) is None


def test_find_previous_snapshot_returns_none_when_repo_root_empty(tmp_path: Path):
    fake = tmp_path / "nothing" / "parsed_data"
    fake.mkdir(parents=True)
    assert find_previous_snapshot(fake, tmp_path) is None


def test_compare_snapshots_loads_messages_per_account(tmp_path: Path):
    import json
    old = tmp_path / "old"
    new = tmp_path / "new"
    for d in (old, new):
        (d / "account-1").mkdir(parents=True)

    json.dump([_msg(1, 100, "kept"), _msg(1, 101, "deleted")],
              open(old / "account-1" / "messages.json", "w"))
    json.dump([_msg(1, 100, "kept"), _msg(1, 102, "fresh")],
              open(new / "account-1" / "messages.json", "w"))

    res = compare_snapshots(old, new)
    assert res["accounts_only_in_old"] == []
    assert res["accounts_only_in_new"] == []
    diff = res["diffs"]["account-1"]
    assert [r["text"] for r in diff["removed"]] == ["deleted"]
    assert [r["text"] for r in diff["added"]] == ["fresh"]


def test_compare_snapshots_reports_account_asymmetry(tmp_path: Path):
    import json
    old = tmp_path / "old"
    new = tmp_path / "new"
    (old / "account-1").mkdir(parents=True)
    (new / "account-2").mkdir(parents=True)
    json.dump([], open(old / "account-1" / "messages.json", "w"))
    json.dump([], open(new / "account-2" / "messages.json", "w"))

    res = compare_snapshots(old, new)
    assert res["accounts_only_in_old"] == ["account-1"]
    assert res["accounts_only_in_new"] == ["account-2"]
    assert res["diffs"] == {}

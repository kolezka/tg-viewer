"""ghost_detector.py — temporal cross-validation of parsed message snapshots.

The encrypted-DB parser already flags messages whose t7 row is gone *now*
(via in-process WAL comparison). This module adds the orthogonal check:
**run-vs-run drift**. If a message was present in yesterday's
`messages.json` but is missing from today's, that's independent evidence
of a deletion between runs.

The diff key is `(peer_id, timestamp, namespace_or_text_hash)`:
  - `peer_id` and `timestamp` come straight from the t7 key
  - `namespace` is bytes 16-19 of the t7 key (set for secret-chat msgs,
    None for older formats); included so two rows with identical
    `(peer_id, timestamp)` but different namespaces don't collide
  - When the namespace is None, we fall back to a short text hash so
    near-simultaneous messages stay distinct

Output records are plain dicts (no datetime, no set) so the diff
serializes straight to JSON without custom encoders.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def message_key(msg: dict[str, Any]) -> tuple[Any, ...]:
    """Stable identity for a single t7 message.

    Secret-chat rows carry an explicit namespace; everything else falls
    back to a short text hash so identical-timestamp rows in the same
    chat (think: rapid-fire one-word replies) don't collide.
    """
    peer_id = msg.get("peer_id")
    timestamp = msg.get("timestamp")
    namespace = msg.get("namespace")
    if namespace is not None:
        return (peer_id, timestamp, namespace)
    text = msg.get("text", "") or ""
    h = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:8]
    return (peer_id, timestamp, h)


_DIFF_FIELDS = ("text", "media", "outgoing", "peer_name")


def _row(msg: dict[str, Any]) -> dict[str, Any]:
    """Project a message into a small comparable dict (drop noisy fields)."""
    return {
        "peer_id": msg.get("peer_id"),
        "peer_name": msg.get("peer_name"),
        "timestamp": msg.get("timestamp"),
        "date": msg.get("date"),
        "text": msg.get("text", ""),
        "media": msg.get("media", []),
        "outgoing": msg.get("outgoing"),
    }


def diff_snapshots(
    old: list[dict[str, Any]], new: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Three-way diff between two `messages.json` payloads.

    `removed` is the forensic prize: rows that existed in `old` and have
    vanished from `new`. `added` is the inverse (rarely interesting on
    its own — usually just new messages). `modified` catches in-place
    edits / redactions: same key, different `_DIFF_FIELDS`.
    """
    old_by_key = {message_key(m): m for m in old}
    new_by_key = {message_key(m): m for m in new}

    removed = [_row(old_by_key[k]) for k in old_by_key.keys() - new_by_key.keys()]
    added = [_row(new_by_key[k]) for k in new_by_key.keys() - old_by_key.keys()]
    modified: list[dict[str, Any]] = []
    for k in old_by_key.keys() & new_by_key.keys():
        o, n = old_by_key[k], new_by_key[k]
        if any(o.get(f) != n.get(f) for f in _DIFF_FIELDS):
            modified.append({"old": _row(o), "new": _row(n)})

    removed.sort(key=lambda r: (r["timestamp"] or 0), reverse=True)
    added.sort(key=lambda r: (r["timestamp"] or 0), reverse=True)
    modified.sort(key=lambda r: (r["new"]["timestamp"] or 0), reverse=True)
    return {"removed": removed, "added": added, "modified": modified}


def find_previous_snapshot(
    current_parsed_dir: Path, repo_root: Path
) -> Path | None:
    """Locate the newest `parsed_data` sibling under `repo_root` that isn't current.

    Supports both layouts:
      * `tg_*/tg_*/parsed_data`   — produced by `./tg-viewer backup` (nested)
      * `tg_*/parsed_data`         — produced by the periodic daemon (flat)

    Heuristic only walks two levels under `repo_root`, doesn't follow
    symlinks, and ignores the snapshot at `current_parsed_dir`.
    """
    current_parsed_dir = current_parsed_dir.resolve()
    candidates: list[tuple[float, Path]] = []
    for outer in repo_root.glob("tg_*"):
        if not outer.is_dir():
            continue
        # Flat layout: outer/parsed_data
        flat = outer / "parsed_data"
        if flat.is_dir() and flat.resolve() != current_parsed_dir:
            candidates.append((flat.stat().st_mtime, flat))
        # Nested layout: outer/tg_*/parsed_data
        for inner in outer.glob("tg_*"):
            if not inner.is_dir():
                continue
            nested = inner / "parsed_data"
            if nested.is_dir() and nested.resolve() != current_parsed_dir:
                candidates.append((nested.stat().st_mtime, nested))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _load_messages(account_dir: Path) -> list[dict[str, Any]]:
    f = account_dir / "messages.json"
    if not f.is_file():
        return []
    with open(f, encoding="utf-8") as fh:
        return json.load(fh)


def compare_snapshots(old_parsed: Path, new_parsed: Path) -> dict[str, Any]:
    """Diff every account that appears in both snapshots; report mismatches."""
    old_accounts = {p.name for p in old_parsed.glob("account-*") if p.is_dir()}
    new_accounts = {p.name for p in new_parsed.glob("account-*") if p.is_dir()}

    common = old_accounts & new_accounts
    diffs: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for acc in sorted(common):
        old_msgs = _load_messages(old_parsed / acc)
        new_msgs = _load_messages(new_parsed / acc)
        diffs[acc] = diff_snapshots(old_msgs, new_msgs)

    return {
        "old_parsed": str(old_parsed),
        "new_parsed": str(new_parsed),
        "accounts_only_in_old": sorted(old_accounts - new_accounts),
        "accounts_only_in_new": sorted(new_accounts - old_accounts),
        "diffs": diffs,
    }

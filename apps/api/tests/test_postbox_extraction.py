"""Extraction robustness + FTS attribution regressions for postbox_parser.

Covers the 2026-06-27 review findings:
  * Form 1 media-ref scan must not crash on a truncated `01 69 01` marker.
  * parse_messages_from_t7 must isolate a bad row, not abort the account.
  * FTS rows must be attributed to their peer (not dumped in 'unknown') and
    deduped against t7 even for non-user (namespaced) peers.
  * Per-conversation filenames must not collide for distinct peers.
"""
from __future__ import annotations

import json
import struct
from types import SimpleNamespace

from tool.postbox_parser import (
    extract_media_refs,
    export_account,
    parse_messages_from_t7,
)
from api.chats_logic import compute_chats


# ── blob helpers ────────────────────────────────────────────────────────────
def _t7_key(peer_id: int, ts: int = 1_700_000_000) -> bytes:
    """20-byte t7 key: peer_id(8 BE) + pad(4) + ts(4 BE) + namespace(4)."""
    return struct.pack(">q", peer_id) + b"\x00" * 4 + struct.pack(">I", ts) + b"\x00" * 4


def _t7_value(text: str) -> bytes:
    """t7 value with the main text length-prefixed at offset 4."""
    t = text.encode("utf-8")
    return b"\x00" * 4 + struct.pack("<I", len(t)) + t + b"\x00" * 16


def _peer_title(title: str) -> bytes:
    t = title.encode("utf-8")
    return b"\x01t\x04" + struct.pack("<I", len(t)) + t


def _peer_username(username: str) -> bytes:
    u = username.encode("utf-8")
    return b"\x02un\x04" + struct.pack("<I", len(u)) + u


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """Minimal SQLCipher-conn stand-in dispatching by table name."""

    def __init__(self, t2=None, t7=None, fts=None):
        self._t2 = t2 or []   # list[(key:int, value:bytes)]
        self._t7 = t7 or []   # list[(key:bytes, value:bytes)] — rowid = position
        self._fts = fts or []  # list[(id, c0, c1, c2, c3)]

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if "FROM t2" in s:
            return _Result(list(self._t2))
        if "FROM t7" in s:
            last = params[0] if params else 0
            limit = params[1] if len(params) > 1 else 10_000
            out = []
            for rowid, (k, v) in enumerate(self._t7, start=1):
                if rowid <= last:
                    continue
                out.append((rowid, k, v))
                if len(out) >= limit:
                    break
            return _Result(out)
        if "ft41_content" in s:
            return _Result(list(self._fts))
        return _Result([])

    def close(self):
        pass


# ── Finding #1: Form 1 truncated marker must not crash ──────────────────────
def test_extract_media_refs_no_crash_on_truncated_form1():
    # '01 69 01' lands in the last 7 bytes -> file_id slice would be < 8 bytes.
    value = b"\x00" * 40 + b"\x01\x69\x01" + b"\x11\x22\x33"
    assert extract_media_refs(value) == []  # no struct.error


def test_extract_media_refs_form1_valid_still_parsed():
    fid = 7_777_777_777
    value = b"\x00" * 8 + b"\x01\x69\x01" + struct.pack("<q", fid) + b"\x00" * 8
    refs = extract_media_refs(value)
    assert any(r["file_id"] == fid for r in refs)


# ── Finding #2: one bad t7 row must not abort the whole account ──────────────
def test_parse_messages_from_t7_isolates_bad_row(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "dummy").write_bytes(b"x")  # make media_index non-empty

    bad = (_t7_key(222), b"\x00" * 40 + b"\x01\x69\x01" + b"\x11\x22\x33")
    good = (_t7_key(111), _t7_value("survivor message"))
    conn = FakeConn(t7=[bad, good])

    msgs = parse_messages_from_t7(conn, peers={}, media_dir=media)

    assert [m["text"] for m in msgs] == ["survivor message"]


# ── Findings #3 + #4: FTS attribution + cross-namespace dedup ────────────────
def test_export_account_attributes_fts_to_namespaced_peer(tmp_path):
    chan_id = (2 << 32) | 555  # channel: hi-word == 2, bare id 555
    conn = FakeConn(
        t2=[(chan_id, _peer_title("Cool Channel"))],
        t7=[(_t7_key(chan_id), _t7_value("channel post"))],
        fts=[
            (1, "p555", "m1", "channel post", ""),          # dup of t7 -> dropped
            (2, "p555", "m2", "deleted channel post", ""),  # fts-only -> kept
        ],
    )
    export_account(conn, "chan", tmp_path, backup_dir=None)

    acct = tmp_path / "account-chan"
    all_msgs = json.loads((acct / "all_messages.json").read_text())
    convos = json.loads((acct / "conversations_index.json").read_text())

    # dedup worked across the namespace boundary: 1 t7 + 1 fts-only = 2 (not 3).
    assert len(all_msgs) == 2
    # the fts-only row is attributed to the channel, not a bogus 'unknown' bucket.
    assert len(convos) == 1
    conv = convos[0]
    assert conv["peer_name"] == "Cool Channel"
    assert conv["peer_id"] == chan_id
    assert conv["message_count"] == 2
    assert all(str(c.get("peer_id")) != "unknown" for c in convos)


# ── Finding #7: distinct peers must not share a conversation filename ────────
def test_export_account_conversation_filenames_do_not_collide(tmp_path):
    # Both labels sanitize to "dup_x": username "dup.x" vs name "dup x".
    conn = FakeConn(
        t2=[
            (1001, _peer_username("dup.x")),
            (1002, _peer_title("dup x")),
        ],
        t7=[
            (_t7_key(1001), _t7_value("from A")),
            (_t7_key(1002), _t7_value("from B")),
        ],
    )
    export_account(conn, "coll", tmp_path, backup_dir=None)

    convo_files = list((tmp_path / "account-coll" / "conversations").glob("*.json"))
    assert len(convo_files) == 2


# ── Finding #4 (API side): has_fts must hold for a namespaced peer ───────────
def test_compute_chats_has_fts_for_namespaced_peer():
    chan_id = (2 << 32) | 555
    state = SimpleNamespace(
        databases={
            "acct": {
                "conversations": [
                    {
                        "peer_id": chan_id,
                        "all_peer_ids": [chan_id],
                        "peer_name": "Cool Channel",
                        "peer_username": "",
                        "message_count": 3,
                        "last_message": 1_700_000_000,
                    }
                ],
                "messages_fts": [{"peer_ref": "p555", "text": "x"}],
                "peers": [{"id": chan_id, "first_name": "Cool Channel"}],
            }
        }
    )
    chats = compute_chats(state)
    chan = next(c for c in chats if c["id"] == str(chan_id))
    assert chan["has_fts"] is True

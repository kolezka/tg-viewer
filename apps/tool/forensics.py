"""forensics.py — join storage_catalog ⨝ log_events ⨝ messages by file_id.

Three datasets carry information about the same physical media file:

  storage_catalog   filename + size + on_disk flag (per-account)
  log_events        Telegram MTProto debug log records (per-backup)
  messages          t7 rows with media[].file_id (per-account)

`build_forensic_index` produces one row per unique file_id with all
three sources fused. Tombstones (on_disk=False) sort first — the user
asking "what did I lose" sees the answer immediately.
"""
from __future__ import annotations

import re
from typing import Any, Iterable


# Filename → file_id regexes. Negative ids encode as a doubled separator:
#   local-file--1155884980421330152  →  -1155884980421330152
# The patterns below all capture the signed id (sign + digits) so int()
# round-trips correctly.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # secret-file-<file_id>-<dc>[.ext]
    ("secret-file", re.compile(r"^secret-file-(-?\d+)-\d+(?:\.\w+)?$")),
    # local-file-<file_id> and local-file--<abs(file_id)> (negative)
    ("local-file", re.compile(r"^local-file-(-?\d+)(?:_partial(?:\.\w+)?)?$")),
    # telegram-local-file-<file_id>
    ("telegram-local-file", re.compile(r"^telegram-local-file-(-?\d+)(?:_partial(?:\.\w+)?)?$")),
    # telegram-cloud-photo-size-<dc>-<file_id>-<suffix>
    ("cloud-photo", re.compile(r"^telegram-cloud-photo-size-\d+-(-?\d+)-[a-z]$")),
    # telegram-cloud-document-size-<dc>-<file_id>-<suffix>
    ("cloud-doc-size", re.compile(r"^telegram-cloud-document-size-\d+-(-?\d+)-[a-z]$")),
    # telegram-cloud-document-<dc>-<file_id>
    ("cloud-doc", re.compile(r"^telegram-cloud-document-\d+-(-?\d+)$")),
    # telegram-peer-photo-size-<dc>-<file_id>-<extra>
    ("peer-photo", re.compile(r"^telegram-peer-photo-size-\d+-(-?\d+)-")),
    # telegram-stickerpackthumbnail-<dc>-<file_id>-<extra>
    ("sticker-thumb", re.compile(r"^telegram-stickerpackthumbnail-\d+-(-?\d+)-")),
]


def extract_file_id_from_filename(name: str) -> int | None:
    """Return the signed file_id encoded in a Telegram cache filename, or None."""
    if not name:
        return None
    # Strip optional `_partial` / `.<ext>` tails that don't show up in canonical
    # names but appear next to them on disk. Patterns above also tolerate them
    # for the local-file family where the suffix is part of the storage layout.
    base = name
    for ext in (".jpg", ".jpeg", ".mp4", ".mp3", ".webm", ".ogg", ".png"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    for _kind, pat in _PATTERNS:
        m = pat.match(base)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                return None
    return None


def _message_media_ids(message: dict) -> Iterable[int]:
    for m in message.get("media", []) or []:
        fid = m.get("file_id")
        if isinstance(fid, int):
            yield fid


def build_forensic_index(
    storage_catalog: list[dict],
    log_events: list[dict],
    messages: list[dict],
    account_id: str = "",
) -> list[dict]:
    """Join three sources on file_id; emit one record per unique id.

    Tombstones (any source says on_disk=False, no source contradicts) sort
    first; remaining rows sort by joined-timestamp desc.
    """
    by_id: dict[int, dict[str, Any]] = {}

    def entry(fid: int) -> dict[str, Any]:
        if fid not in by_id:
            by_id[fid] = {
                "file_id": fid,
                "filenames": [],
                "size_bytes": None,
                "on_disk": False,
                "tombstone": False,
                "sources": set(),
                "log_event": None,
                "message": None,
                "account": account_id,
            }
        return by_id[fid]

    # 1. storage_catalog: every known file Telegram has tracked.
    on_disk_seen: dict[int, bool] = {}
    for s in storage_catalog or []:
        fid = extract_file_id_from_filename(s.get("filename", ""))
        if fid is None:
            continue
        e = entry(fid)
        e["sources"].add("storage")
        if s["filename"] not in e["filenames"]:
            e["filenames"].append(s["filename"])
        sz = s.get("size_bytes") or 0
        if sz and (e["size_bytes"] is None or sz > e["size_bytes"]):
            e["size_bytes"] = sz
        # An id is on_disk if ANY of its filenames is on_disk; tombstone wins
        # only when no representation survives.
        flag = bool(s.get("on_disk"))
        on_disk_seen[fid] = on_disk_seen.get(fid, False) or flag

    # 2. log_events: keep the newest matching encrypted_message per file_id.
    for ev in log_events or []:
        if ev.get("event") != "encrypted_message":
            continue
        data = ev.get("data") or {}
        file_meta = data.get("file") or {}
        fid = file_meta.get("id")
        if not isinstance(fid, int):
            continue
        e = entry(fid)
        e["sources"].add("log")
        candidate = {
            "dcId": file_meta.get("dcId"),
            "accessHash": file_meta.get("accessHash"),
            "size": file_meta.get("size"),
            "keyFingerprint": file_meta.get("keyFingerprint"),
            "chatId": data.get("chatId"),
            "date": data.get("date"),
            "source_file": ev.get("source_file", ""),
            "source_line": ev.get("source_line", 0),
        }
        if not e["log_event"] or (candidate["date"] or 0) > (e["log_event"].get("date") or 0):
            e["log_event"] = candidate
        # Storage missed its size? Take it from the log.
        if not e["size_bytes"] and isinstance(candidate["size"], int):
            e["size_bytes"] = candidate["size"]

    # 3. messages: t7 rows with media[].file_id.
    for msg in messages or []:
        for fid in _message_media_ids(msg):
            e = entry(fid)
            e["sources"].add("message")
            # Newest message wins (rare; usually only one references a given id).
            ts = msg.get("timestamp") or 0
            existing_ts = (e["message"] or {}).get("timestamp") or 0
            if not e["message"] or ts > existing_ts:
                e["message"] = {
                    "account": account_id,
                    "peer_id": msg.get("peer_id"),
                    "peer_name": msg.get("peer_name"),
                    "timestamp": msg.get("timestamp"),
                    "date": msg.get("date"),
                    "outgoing": msg.get("outgoing"),
                }

    # Finalise: on_disk flag + tombstone derivation + freeze sources list.
    for fid, e in by_id.items():
        if fid in on_disk_seen:
            e["on_disk"] = on_disk_seen[fid]
            # Tombstone = storage knew it, nothing has it on disk now.
            e["tombstone"] = "storage" in e["sources"] and not on_disk_seen[fid]
        else:
            # Only log/message saw it — no storage row, can't tell on_disk.
            e["on_disk"] = False
            e["tombstone"] = False
        e["sources"] = sorted(e["sources"])

    def _sort_key(e: dict) -> tuple:
        msg_ts = (e["message"] or {}).get("timestamp") or 0
        log_ts = (e["log_event"] or {}).get("date") or 0
        return (
            not e["tombstone"],   # tombstones first
            -max(msg_ts, log_ts), # then newest activity
            e["file_id"],
        )

    return sorted(by_id.values(), key=_sort_key)

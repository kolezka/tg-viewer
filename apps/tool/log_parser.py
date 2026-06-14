#!/usr/bin/env python3
"""
log_parser.py — Parse Telegram macOS MTProto debug logs for forensic metadata.

Telegram for macOS writes verbose MTProto/Postbox debug traces to
  ~/Library/Group Containers/6N38VWS5BX.ru.keepcoder.Telegram/appstore/logs/
The tg-backup.sh script copies these into `<backup>/logs/` alongside
`accounts-shared-data`. Logs are NOT scoped per-account on disk — a single
client process can hold multiple sessions and emits one log stream.

We extract event metadata only. The actual encrypted payloads are TRUNCATED
in the logs (e.g. `bytes: 2185013e2940308a...2264b` — first 8 hex chars, then
`...`, then total byte count). Decrypting them is impossible; the value here
is the *envelope* that survives even when the message row is later deleted
from t7.

Output layout
-------------
We write per-account JSON because the only meaningful cross-reference is
against that account's `messages.json` (t7 dump). Events that don't carry a
peer/chat (uploads, downloads, generic chat lifecycle) are duplicated into
every account file — they're cheap and avoid a separate global file the API
would need to special-case.

Output: `parsed_data/account-{id}/log_events.json`

Event shapes
------------
All events share:
  { event, source_file, source_line, log_timestamp, data: {...} }

- encrypted_message:  randomId, chatId, peer_id, date, bytes_size,
                      file: None | { id, accessHash, size, dcId,
                      keyFingerprint, empty? }
- upload_part:        fileId, filePart, bytes_size
- download_file:      file_id, accessHash, mtime, bytes_size, type
- pending_removed:    chatId, msg_index_a, msg_index_b
- secret_chat_update: chatId, accessHash?, date?, kind ("accepted",
                      "requested", "discarded", "waiting")

Cross-reference adds `in_db: bool` and `db_match: {...} | None` to every
`encrypted_message` record. A record is a ghost when `in_db is False`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator


# Match the `[MT] YYYY-M-D HH:MM:SS.mmm ...` line prefix. Telegram does NOT
# zero-pad month/day, hence `\d{1,2}`. The leading subsystem tag in `[…]`
# can be `MT`, `State`, `SecretChat`, `PendingMessageManager`, etc. — we
# capture the timestamp once and let event-specific regexes look at the rest.
_LINE_TIMESTAMP_RE = re.compile(
    r"^\[(?P<tag>[A-Za-z]+)\]\s+"
    r"(?P<ts>\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}:\d{2}\.\d{3})\s+"
)


# Update.updateNewEncryptedMessage(message: EncryptedMessage.encryptedMessage(
#   randomId: <int64>, chatId: <int32>, date: <int>, bytes: <hex>...<size>b,
#   file: EncryptedFile.encryptedFileEmpty)
#   | file: EncryptedFile.encryptedFile(id: ..., accessHash: ..., size: ...,
#                                       dcId: ..., keyFingerprint: ...))
#
# The same payload appears nested in both:
#   Updates.updates(updates: [Update.updateNewEncryptedMessage(...)])
#   updates.ChannelDifference.channelDifference(... newMessages: ...,
#       otherUpdates: [Update.updateNewEncryptedMessage(...)] ...)
# Anchoring on `Update.updateNewEncryptedMessage(message:` matches both.
_ENC_MSG_RE = re.compile(
    r"Update\.updateNewEncryptedMessage\(message:\s+"
    r"EncryptedMessage\.encryptedMessage\("
    r"randomId:\s*(?P<random_id>-?\d+),\s*"
    r"chatId:\s*(?P<chat_id>-?\d+),\s*"
    r"date:\s*(?P<date>\d+),\s*"
    r"bytes:\s*[0-9a-fA-F]+\.\.\.(?P<bytes_size>\d+)b,\s*"
    r"file:\s*EncryptedFile\.(?P<file_kind>encryptedFileEmpty|encryptedFile)"
    r"(?:\((?P<file_args>[^)]*)\))?"
)

# `id: N, accessHash: N, size: N, dcId: N, keyFingerprint: N` (any order in
# theory; Telegram emits this fixed order in practice — we still parse field
# by field).
_FILE_FIELD_RE = re.compile(
    r"(?P<key>id|accessHash|size|dcId|keyFingerprint):\s*(?P<val>-?\d+)"
)


# upload.saveFilePart(fileId: N, filePart: N, bytes: <hex>...<size>b)
_UPLOAD_RE = re.compile(
    r"upload\.saveFilePart\("
    r"fileId:\s*(?P<file_id>-?\d+),\s*"
    r"filePart:\s*(?P<file_part>\d+),\s*"
    r"bytes:\s*[0-9a-fA-F]+\.\.\.(?P<bytes_size>\d+)b\s*\)"
)


# response for ... is upload.File.file(type: storage.FileType.<name>,
#   mtime: N, bytes: <hex>...<size>b)
# These are download payloads. The accompanying request line (see
# `upload.getFile(location: InputFileLocation.inputEncryptedFileLocation(
# id: N, accessHash: N), offset: N, limit: N)`) carries the file_id; we
# pair them with the immediately-preceding getFile if visible in the same
# line, otherwise leave file_id null.
_DOWNLOAD_RE = re.compile(
    r"upload\.File\.file\("
    r"type:\s*storage\.FileType\.(?P<file_type>\w+),\s*"
    r"mtime:\s*(?P<mtime>\d+),\s*"
    r"bytes:\s*[0-9a-fA-F]+\.\.\.(?P<bytes_size>\d+)b\s*\)"
)

# `add request upload.getFile(...)` — the request line just before download
# responses. We only mine it standalone (no pairing); inputEncryptedFileLocation
# carries id+accessHash for the encrypted file.
_GETFILE_REQ_RE = re.compile(
    r"upload\.getFile\([^)]*"
    r"InputFileLocation\.inputEncryptedFileLocation\("
    r"id:\s*(?P<file_id>-?\d+),\s*"
    r"accessHash:\s*(?P<access_hash>-?\d+)\)"
    r"[^)]*offset:\s*(?P<offset>\d+),\s*limit:\s*(?P<limit>\d+)"
)


# [PendingMessageManager] ... removed messages: [3:Id(rawValue: N):X_Y]
# Sometimes more than one item: `[3:Id(...):1_1, 3:Id(...):1_2]`. We emit one
# record per item.
_PENDING_REMOVED_RE = re.compile(
    r"removed messages:\s*\[(?P<items>[^\]]+)\]"
)
_PENDING_ITEM_RE = re.compile(
    r"3:Id\(rawValue:\s*(?P<chat_id>-?\d+)\):(?P<a>\d+)_(?P<b>\d+)"
)


# updateEncryption(chat: SecretChat.encryptedChat(id: N, accessHash: N,
#   date: N, adminId: N, participantId: N, gA: ...)) and friends.
# This regex captures the kind + chat id; we drill into fields after match.
_SECRET_CHAT_KINDS = (
    "encryptedChat",
    "encryptedChatRequested",
    "encryptedChatWaiting",
    "encryptedChatDiscarded",
    "encryptedChatEmpty",
)
_SECRET_CHAT_RE = re.compile(
    r"EncryptedChat\.(?P<kind>"
    + "|".join(_SECRET_CHAT_KINDS)
    + r")\((?P<args>[^)]*)\)"
)


def _parse_kv_int(text: str, key: str) -> int | None:
    """Pull `key: <int>` out of a free-form fragment. Returns None if absent."""
    m = re.search(rf"{re.escape(key)}:\s*(-?\d+)", text)
    return int(m.group(1)) if m else None


def _make_record(
    event: str,
    source_file: str,
    source_line: int,
    log_timestamp: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event": event,
        "source_file": source_file,
        "source_line": source_line,
        "log_timestamp": log_timestamp,
        "data": data,
    }


def _scan_line(
    line: str, source_file: str, line_no: int
) -> Iterator[dict[str, Any]]:
    """Yield zero or more event records extracted from one log line.

    A single line can match multiple patterns (rare but possible — e.g. an
    Updates.updates wrapper holding several updateNewEncryptedMessage items),
    so each event-type regex uses `finditer` to avoid silently dropping the
    second occurrence.
    """
    ts_match = _LINE_TIMESTAMP_RE.match(line)
    if not ts_match:
        return
    log_ts = ts_match.group("ts")

    # 1. Encrypted message arrivals (highest priority).
    for m in _ENC_MSG_RE.finditer(line):
        chat_id = int(m.group("chat_id"))
        date = int(m.group("date"))
        peer_id = (3 << 32) | (chat_id & 0xFFFFFFFF)
        data: dict[str, Any] = {
            "random_id": int(m.group("random_id")),
            "chat_id": chat_id,
            "peer_id": peer_id,
            "date": date,
            "bytes_size": int(m.group("bytes_size")),
        }
        if m.group("file_kind") == "encryptedFileEmpty":
            data["file"] = None
        else:
            file_args = m.group("file_args") or ""
            file_data: dict[str, int] = {}
            for fm in _FILE_FIELD_RE.finditer(file_args):
                file_data[fm.group("key")] = int(fm.group("val"))
            data["file"] = file_data or None
        yield _make_record(
            "encrypted_message", source_file, line_no, log_ts, data
        )

    # 2. Outgoing upload parts.
    for m in _UPLOAD_RE.finditer(line):
        yield _make_record(
            "upload_part",
            source_file,
            line_no,
            log_ts,
            {
                "file_id": int(m.group("file_id")),
                "file_part": int(m.group("file_part")),
                "bytes_size": int(m.group("bytes_size")),
            },
        )

    # 3. Incoming download responses. The file_id from the matching
    #    upload.getFile request isn't co-located in the same line in real
    #    logs, so we attach what we can read here and leave id pairing for
    #    a possible future enrichment pass.
    for m in _DOWNLOAD_RE.finditer(line):
        yield _make_record(
            "download_file",
            source_file,
            line_no,
            log_ts,
            {
                "file_type": m.group("file_type"),
                "mtime": int(m.group("mtime")),
                "bytes_size": int(m.group("bytes_size")),
            },
        )

    # 3b. inputEncryptedFileLocation request — carries the encrypted file
    #     id + access hash that the download response itself omits.
    for m in _GETFILE_REQ_RE.finditer(line):
        yield _make_record(
            "download_request",
            source_file,
            line_no,
            log_ts,
            {
                "file_id": int(m.group("file_id")),
                "access_hash": int(m.group("access_hash")),
                "offset": int(m.group("offset")),
                "limit": int(m.group("limit")),
            },
        )

    # 4. PendingMessageManager removed (= server-ack of an outgoing message).
    pm = _PENDING_REMOVED_RE.search(line)
    if pm and "PendingMessageManager" in line[:80]:
        for item in _PENDING_ITEM_RE.finditer(pm.group("items")):
            chat_id = int(item.group("chat_id"))
            yield _make_record(
                "pending_removed",
                source_file,
                line_no,
                log_ts,
                {
                    "chat_id": chat_id,
                    "peer_id": (3 << 32) | (chat_id & 0xFFFFFFFF),
                    "msg_index_a": int(item.group("a")),
                    "msg_index_b": int(item.group("b")),
                },
            )

    # 5. Secret-chat lifecycle. Match each EncryptedChat.* variant; pull
    #    out id/accessHash/date when present.
    for m in _SECRET_CHAT_RE.finditer(line):
        # `args` truncates at the first inner `)` (e.g. `gA: Bytes(...)`), so
        # field values after such a nested paren are lost. Run the kv lookups
        # against the full line instead; we keep only the `kind` from the match.
        data = {
            "kind": m.group("kind"),
            "chat_id": _parse_kv_int(line, "id"),
        }
        for fk in ("accessHash", "date", "adminId", "participantId"):
            v = _parse_kv_int(line, fk)
            if v is not None:
                data[fk] = v
        yield _make_record(
            "secret_chat_update", source_file, line_no, log_ts, data
        )


def parse_log_file(path: Path) -> list[dict[str, Any]]:
    """Parse a single `log-*.txt` file. Streams line by line.

    Returns a flat list. Reading is O(lines) with no cross-line buffering, but
    every extracted event is accumulated into the returned list — so peak
    memory scales with the number of matched events, not the file size. The
    caller (`parse_logs_dir`) further holds the concatenation of all files'
    events fully in memory; this is not a streaming pipeline.
    """
    out: list[dict[str, Any]] = []
    source = path.name
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            for rec in _scan_line(line, source, line_no):
                out.append(rec)
    return out


def parse_logs_dir(logs_dir: Path) -> list[dict[str, Any]]:
    """Walk a `logs/` directory and parse every `log-*.txt`.

    `critlog-*.txt` are skipped — they're a binary/structured crashlog
    format the human-readable parser doesn't understand.
    """
    if not logs_dir.is_dir():
        return []
    events: list[dict[str, Any]] = []
    for log_path in sorted(logs_dir.iterdir()):
        if not log_path.is_file():
            continue
        if not log_path.name.startswith("log-") or not log_path.name.endswith(
            ".txt"
        ):
            continue
        events.extend(parse_log_file(log_path))
    return events


def cross_reference_with_messages(
    events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    tolerance_seconds: int = 2,
) -> list[dict[str, Any]]:
    """Annotate every `encrypted_message` event with `in_db` + `db_match`.

    Match heuristic: same `peer_id = (3 << 32) | chatId` and timestamp
    within ±`tolerance_seconds`. Telegram's `date` in the encrypted-message
    arrival and the `timestamp` derived from the t7 key are usually exactly
    equal, but on the boundary of upstream-client clock drift we allow ±2s
    as a buffer. Events that don't match a t7 row are flagged as ghosts.

    Mutates events in-place AND returns the list, for convenience in the
    caller's `events = cross_reference_with_messages(events, msgs)` pattern.
    """
    # Index messages by peer_id → sorted list of timestamps with payloads.
    by_peer: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for msg in messages:
        pid = msg.get("peer_id")
        ts = msg.get("timestamp")
        if pid is None or ts is None:
            continue
        try:
            pid_int = int(pid)
            ts_int = int(ts)
        except (TypeError, ValueError):
            continue
        by_peer.setdefault(pid_int, []).append((ts_int, msg))

    for entries in by_peer.values():
        entries.sort(key=lambda x: x[0])

    for ev in events:
        if ev["event"] != "encrypted_message":
            continue
        data = ev["data"]
        peer_id = data.get("peer_id")
        date = data.get("date")
        candidates = by_peer.get(peer_id, [])
        match = None
        for ts, msg in candidates:
            if abs(ts - date) <= tolerance_seconds:
                match = {
                    "peer_id": msg.get("peer_id"),
                    "timestamp": ts,
                    "text": (msg.get("text") or "")[:120],
                    "outgoing": msg.get("outgoing"),
                }
                break
        ev["in_db"] = match is not None
        ev["db_match"] = match
    return events


def summarize(events: list[dict[str, Any]]) -> dict[str, int]:
    """Return event-type counts + ghost count for a single-line summary."""
    out: dict[str, int] = {}
    ghosts = 0
    for ev in events:
        out[ev["event"]] = out.get(ev["event"], 0) + 1
        if ev["event"] == "encrypted_message" and ev.get("in_db") is False:
            ghosts += 1
    out["__ghosts__"] = ghosts
    return out


def main() -> None:
    """CLI helper for ad-hoc inspection: `python -m tool.log_parser <logs_dir>`."""
    if len(sys.argv) < 2:
        print("Usage: python -m tool.log_parser <logs_dir> [messages.json]")
        sys.exit(1)
    logs_dir = Path(sys.argv[1])
    events = parse_logs_dir(logs_dir)
    if len(sys.argv) >= 3:
        msgs = json.loads(Path(sys.argv[2]).read_text())
        cross_reference_with_messages(events, msgs)
    counts = summarize(events)
    ghosts = counts.pop("__ghosts__", 0)
    print(
        f"Parsed {len(events)} events from {logs_dir}: "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        + f"; ghosts={ghosts}"
    )


if __name__ == "__main__":
    main()

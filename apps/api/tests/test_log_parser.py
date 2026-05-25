"""Unit tests for tool.log_parser.

Fixture lines are real log strings from a tg-backup snapshot, lightly
shortened (the truncated `bytes:` hex prefix kept, the size suffix kept,
the connection-id trailing decoration trimmed). Anything the regex actually
inspects is preserved verbatim.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tool import log_parser


# --- canned log lines -------------------------------------------------------


ENC_MSG_EMPTY_FILE = (
    "[MT] 2026-5-25 22:55:08.290 [MTProto#0xc94451ea0@0xc8c639e00 [248] "
    "received Updates.updates(updates: [Update.updateNewEncryptedMessage("
    "message: EncryptedMessage.encryptedMessage(randomId: 1418985608753277351, "
    "chatId: 1771397630, date: 1779742508, bytes: 2185013e2940308a...104b, "
    "file: EncryptedFile.encryptedFileEmpty), qts: 981478974)], users: [], "
    "chats: [], date: 1779742507, seq: 0) (7643935868802885633, "
    "-3954540495367100079/2616701037900525442)]\n"
)


ENC_MSG_WITH_FILE = (
    "[MT] 2026-5-25 23:02:03.604 [MTProto#0xc94451ea0@0xc8c639e00 [2440] "
    "received Updates.updates(updates: [Update.updateNewEncryptedMessage("
    "message: EncryptedMessage.encryptedMessage(randomId: 1906043315404116848, "
    "chatId: 1771397630, date: 1779742923, bytes: 2185013e2940308a...2264b, "
    "file: EncryptedFile.encryptedFile(id: 5811993738297220291, "
    "accessHash: -2620397795542898489, size: 73904, dcId: 4, "
    "keyFingerprint: -1097703132)), qts: 981478997)], users: [], chats: [], "
    "date: 1779742922, seq: 0) (...)]\n"
)


UPLOAD_PART = (
    "[MT] 2026-5-25 23:01:28.436 [MTRequestMessageService#c8b3fc850 add "
    "request upload.saveFilePart(fileId: 4100810929828771308, filePart: 0, "
    "bytes: 35cb841a04aa77d7...16384b)]\n"
)


DOWNLOAD_FILE = (
    "[MT] 2026-5-25 22:52:20.200 [MTRequestMessageService#0xc8b7755e0 "
    "response for 7643935144983830528 is upload.File.file(type: "
    "storage.FileType.filePartial, mtime: 1779734363, "
    "bytes: 3f4ff682d9818927...37360b)]\n"
)


DOWNLOAD_REQUEST = (
    "[MT] 2026-5-25 22:52:19.675 [MTRequestMessageService#c8b7755e0 add "
    "request upload.getFile(flags: 0, location: InputFileLocation."
    "inputEncryptedFileLocation(id: 5812262057789103240, "
    "accessHash: -5773165571780142891), offset: 0, limit: 131072)]\n"
)


PENDING_REMOVED = (
    "[PendingMessageManager] 2026-5-25 22:52:08.119 removed messages: "
    "[3:Id(rawValue: 1529214002):1_8]\n"
)


PENDING_REMOVED_MULTI = (
    "[PendingMessageManager] 2026-5-25 22:52:08.119 removed messages: "
    "[3:Id(rawValue: 1529214002):1_8, 3:Id(rawValue: 1543987364):1_2]\n"
)


# Constructed: the real corpus didn't include a fresh secret-chat handshake,
# but the regex must handle these so we test against the documented shape
# (Telegram's MTProto schema for EncryptedChat.*).
SECRET_CHAT_NEW = (
    "[MT] 2026-5-25 22:50:00.000 [MTProto] received updateEncryption("
    "chat: EncryptedChat.encryptedChat(id: 1234567890, accessHash: "
    "9876543210, date: 1779742000, adminId: 1111, participantId: 2222, "
    "gA: <bytes>))\n"
)


SECRET_CHAT_DISCARDED = (
    "[MT] 2026-5-25 22:50:00.000 [MTProto] EncryptedChat.encryptedChatDiscarded("
    "id: 4242)\n"
)


# A real wrapper variant: `updateNewEncryptedMessage` nested inside the
# difference response rather than `Updates.updates`. The regex must still
# match because it anchors on `Update.updateNewEncryptedMessage(message:`.
ENC_MSG_IN_DIFFERENCE = (
    "[MT] 2026-5-25 22:55:00.000 [MTProto] received updates."
    "ChannelDifference.channelDifference(... otherUpdates: ["
    "Update.updateNewEncryptedMessage(message: EncryptedMessage."
    "encryptedMessage(randomId: 42, chatId: 99, date: 1779700000, "
    "bytes: deadbeef...500b, file: EncryptedFile.encryptedFileEmpty), "
    "qts: 1)] ...)\n"
)


# --- direct regex / scan tests ---------------------------------------------


def _one(line: str) -> dict:
    """Helper — assert exactly one record yielded and return it."""
    recs = list(log_parser._scan_line(line, "fixture.txt", 1))
    assert len(recs) == 1, f"expected 1 record, got {len(recs)}: {recs!r}"
    return recs[0]


def test_encrypted_message_empty_file_basic_fields():
    rec = _one(ENC_MSG_EMPTY_FILE)
    assert rec["event"] == "encrypted_message"
    assert rec["log_timestamp"] == "2026-5-25 22:55:08.290"
    assert rec["source_file"] == "fixture.txt"
    assert rec["source_line"] == 1
    d = rec["data"]
    assert d["random_id"] == 1418985608753277351
    assert d["chat_id"] == 1771397630
    # peer_id formula: (3 << 32) | chatId
    assert d["peer_id"] == (3 << 32) | 1771397630
    assert d["date"] == 1779742508
    assert d["bytes_size"] == 104
    assert d["file"] is None  # encryptedFileEmpty


def test_encrypted_message_with_attached_file_fields():
    rec = _one(ENC_MSG_WITH_FILE)
    f = rec["data"]["file"]
    assert f == {
        "id": 5811993738297220291,
        "accessHash": -2620397795542898489,
        "size": 73904,
        "dcId": 4,
        "keyFingerprint": -1097703132,
    }
    assert rec["data"]["bytes_size"] == 2264


def test_encrypted_message_inside_channel_difference():
    # Regression: regex must handle the difference-response wrapper too.
    rec = _one(ENC_MSG_IN_DIFFERENCE)
    assert rec["event"] == "encrypted_message"
    assert rec["data"]["random_id"] == 42
    assert rec["data"]["chat_id"] == 99
    assert rec["data"]["bytes_size"] == 500


def test_upload_part():
    rec = _one(UPLOAD_PART)
    assert rec["event"] == "upload_part"
    assert rec["data"] == {
        "file_id": 4100810929828771308,
        "file_part": 0,
        "bytes_size": 16384,
    }


def test_download_file_response():
    rec = _one(DOWNLOAD_FILE)
    assert rec["event"] == "download_file"
    assert rec["data"]["file_type"] == "filePartial"
    assert rec["data"]["mtime"] == 1779734363
    assert rec["data"]["bytes_size"] == 37360


def test_download_request_carries_file_id_and_access_hash():
    rec = _one(DOWNLOAD_REQUEST)
    assert rec["event"] == "download_request"
    assert rec["data"]["file_id"] == 5812262057789103240
    assert rec["data"]["access_hash"] == -5773165571780142891
    assert rec["data"]["offset"] == 0
    assert rec["data"]["limit"] == 131072


def test_pending_removed_single():
    rec = _one(PENDING_REMOVED)
    assert rec["event"] == "pending_removed"
    assert rec["data"]["chat_id"] == 1529214002
    assert rec["data"]["msg_index_a"] == 1
    assert rec["data"]["msg_index_b"] == 8
    assert rec["data"]["peer_id"] == (3 << 32) | 1529214002


def test_pending_removed_multi_emits_one_per_item():
    recs = list(log_parser._scan_line(PENDING_REMOVED_MULTI, "fixture.txt", 1))
    assert len(recs) == 2
    assert recs[0]["data"]["chat_id"] == 1529214002
    assert recs[1]["data"]["chat_id"] == 1543987364
    assert recs[1]["data"]["msg_index_b"] == 2


def test_secret_chat_update_new():
    rec = _one(SECRET_CHAT_NEW)
    assert rec["event"] == "secret_chat_update"
    assert rec["data"]["kind"] == "encryptedChat"
    assert rec["data"]["chat_id"] == 1234567890
    assert rec["data"]["accessHash"] == 9876543210
    assert rec["data"]["date"] == 1779742000
    assert rec["data"]["adminId"] == 1111
    assert rec["data"]["participantId"] == 2222


def test_secret_chat_discarded():
    rec = _one(SECRET_CHAT_DISCARDED)
    assert rec["event"] == "secret_chat_update"
    assert rec["data"]["kind"] == "encryptedChatDiscarded"
    assert rec["data"]["chat_id"] == 4242


def test_line_without_mt_prefix_is_skipped():
    # Lines without the `[Tag] YYYY-M-D HH:MM:SS.mmm` prefix are ignored.
    recs = list(log_parser._scan_line(
        "random stack-trace noise upload.saveFilePart(...)", "fixture.txt", 1
    ))
    assert recs == []


# --- file + directory walking ----------------------------------------------


def test_parse_log_file_reads_all_lines(tmp_path: Path):
    log = tmp_path / "log-2026-5-25_00-00-00.000.txt"
    log.write_text(
        ENC_MSG_EMPTY_FILE
        + "this line has no MT tag\n"
        + UPLOAD_PART
        + PENDING_REMOVED
    )
    events = log_parser.parse_log_file(log)
    assert [e["event"] for e in events] == [
        "encrypted_message",
        "upload_part",
        "pending_removed",
    ]
    # Line numbers must be 1-indexed and refer to the line *in* the file.
    assert events[0]["source_line"] == 1
    assert events[1]["source_line"] == 3
    assert events[2]["source_line"] == 4


def test_parse_logs_dir_skips_critlog_and_non_log(tmp_path: Path):
    (tmp_path / "log-2026-5-25_00-00-00.000.txt").write_text(ENC_MSG_EMPTY_FILE)
    (tmp_path / "critlog-2026-5-25_00-00-00.000.txt").write_text(ENC_MSG_EMPTY_FILE)
    (tmp_path / "random.txt").write_text(ENC_MSG_EMPTY_FILE)
    (tmp_path / "log-other.dat").write_text(ENC_MSG_EMPTY_FILE)
    events = log_parser.parse_logs_dir(tmp_path)
    # Only the log-*.txt file is picked up.
    assert len(events) == 1
    assert events[0]["source_file"].startswith("log-")
    assert events[0]["source_file"].endswith(".txt")


def test_parse_logs_dir_missing_returns_empty(tmp_path: Path):
    assert log_parser.parse_logs_dir(tmp_path / "does-not-exist") == []


# --- cross-reference --------------------------------------------------------


def test_cross_reference_marks_ghost_when_no_t7_row():
    events = list(log_parser._scan_line(ENC_MSG_EMPTY_FILE, "f.txt", 1))
    log_parser.cross_reference_with_messages(events, [])
    assert events[0]["in_db"] is False
    assert events[0]["db_match"] is None


def test_cross_reference_matches_within_tolerance():
    events = list(log_parser._scan_line(ENC_MSG_EMPTY_FILE, "f.txt", 1))
    peer_id = (3 << 32) | 1771397630
    msgs = [
        {
            "peer_id": peer_id,
            # Off by 1 second from log date (1779742508) — within tolerance.
            "timestamp": 1779742509,
            "text": "hi there",
            "outgoing": False,
        }
    ]
    log_parser.cross_reference_with_messages(events, msgs)
    assert events[0]["in_db"] is True
    assert events[0]["db_match"]["peer_id"] == peer_id
    assert events[0]["db_match"]["text"] == "hi there"


def test_cross_reference_rejects_outside_tolerance():
    events = list(log_parser._scan_line(ENC_MSG_EMPTY_FILE, "f.txt", 1))
    peer_id = (3 << 32) | 1771397630
    msgs = [
        {
            "peer_id": peer_id,
            # 5 seconds away — over the default tolerance of 2s.
            "timestamp": 1779742513,
            "text": "different message",
        }
    ]
    log_parser.cross_reference_with_messages(events, msgs)
    assert events[0]["in_db"] is False
    assert events[0]["db_match"] is None


def test_cross_reference_ignores_non_encrypted_events():
    """upload_part / download_file etc. don't carry peer_id, must skip cleanly."""
    events = list(log_parser._scan_line(UPLOAD_PART, "f.txt", 1))
    log_parser.cross_reference_with_messages(events, [])
    # No in_db key added for non-encrypted_message events.
    assert "in_db" not in events[0]


def test_summarize_counts_events_and_ghosts():
    events = []
    events += list(log_parser._scan_line(ENC_MSG_EMPTY_FILE, "f.txt", 1))
    events += list(log_parser._scan_line(ENC_MSG_WITH_FILE, "f.txt", 2))
    events += list(log_parser._scan_line(UPLOAD_PART, "f.txt", 3))
    log_parser.cross_reference_with_messages(events, [])  # all ghosts
    s = log_parser.summarize(events)
    assert s["encrypted_message"] == 2
    assert s["upload_part"] == 1
    assert s["__ghosts__"] == 2

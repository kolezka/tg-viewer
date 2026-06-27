"""Message direction (sent vs received) — see postbox_parser.decode_message_flags.

Telegram Postbox serialises a t7 message value as (MessageHistoryTable.swift):

    type          Int8    offset 0
    stableId      UInt32  offset 1
    stableVersion UInt32  offset 5
    dataFlags     Int8    offset 9   -- selects the optional block below
    <optional fields, present iff the matching dataFlags bit is set, in order:
        0x01 hasGloballyUniqueId  Int64  (8)
        0x02 hasGlobalTags        UInt32 (4)
        0x04 hasGroupingKey       Int64  (8)
        0x08 hasGroupInfo         UInt32 (4)
        0x10 hasLocalTags         UInt32 (4)
        0x20 hasThreadId          Int64  (8)>
    flags         UInt32  -- MessageFlags; bit 0x04 = Incoming
    tags          UInt32

The flags field only sits at offset 10 when dataFlags == 0. The old parser
hardcoded offset 10, so any message carrying a globallyUniqueId / threadId /
grouping key had its direction read from random id bytes — mixing sent and
received messages (verified against a 388k-message live snapshot: ~8% of
regular-chat messages flipped, up to 68% in some peers).
"""
from __future__ import annotations

import struct

from tool.postbox_parser import (
    MESSAGE_FLAG_INCOMING,
    decode_message_flags,
    message_is_outgoing,
)

INCOMING = 0x04
COUNTED_AS_INCOMING = 0x100  # a real flag that coexists with Incoming


def _value(*, data_flags: int, flags: int, optional: bytes = b"") -> bytes:
    """Build a serialized t7 StoreMessage value with the given flags."""
    assert len(optional) == _optional_len(data_flags)
    return (
        b"\x00"                       # type
        + struct.pack("<I", 1)        # stableId
        + struct.pack("<I", 1)        # stableVersion
        + bytes([data_flags])         # dataFlags
        + optional                    # variable-length optional block
        + struct.pack("<I", flags)    # flags (MessageFlags)
        + struct.pack("<I", 0)        # tags
    )


def _optional_len(data_flags: int) -> int:
    sizes = {0x01: 8, 0x02: 4, 0x04: 8, 0x08: 4, 0x10: 4, 0x20: 8}
    return sum(size for bit, size in sizes.items() if data_flags & bit)


def test_incoming_flag_bit_value():
    assert MESSAGE_FLAG_INCOMING == 0x04


def test_flags_at_offset_10_when_no_optional_fields():
    # dataFlags == 0: flags really do live at offset 10.
    v = _value(data_flags=0x00, flags=INCOMING)
    assert decode_message_flags(v) == INCOMING
    assert message_is_outgoing(peer_id=42, value=v) is False


def test_outgoing_when_incoming_bit_clear():
    v = _value(data_flags=0x00, flags=0)
    assert message_is_outgoing(peer_id=42, value=v) is True


def test_globally_unique_id_shifts_flags_by_eight():
    # dataFlags 0x01 inserts an 8-byte globallyUniqueId; the real flags are at
    # offset 18. Put bytes that LOOK incoming (0x04 set) inside the unique id to
    # prove the decoder ignores them and reads the true flags field.
    unique_id = struct.pack("<q", 0x1B246FEEF9E89965)  # byte at offset 10 == 0x65
    v = _value(data_flags=0x01, flags=0, optional=unique_id)
    assert v[10] & 0x04  # the byte the old code read would say "incoming"...
    assert decode_message_flags(v) == 0  # ...but the true flags say outgoing
    assert message_is_outgoing(peer_id=42, value=v) is True


def test_thread_id_shifts_flags_by_eight():
    # hasThreadId (0x20) is the dominant case in real data (332k/388k messages).
    thread_id = struct.pack("<q", -1)  # 0xFF.. -> byte10 has 0x04 set, looks incoming
    v = _value(data_flags=0x20, flags=INCOMING | COUNTED_AS_INCOMING, optional=thread_id)
    assert decode_message_flags(v) == INCOMING | COUNTED_AS_INCOMING
    assert message_is_outgoing(peer_id=42, value=v) is False


def test_combined_optional_fields_offset():
    # dataFlags 0x2c == hasGroupingKey(8) + hasGroupInfo(4) + hasThreadId(8) = 20 bytes.
    optional = b"\x11" * 20
    v = _value(data_flags=0x2C, flags=0, optional=optional)
    assert message_is_outgoing(peer_id=42, value=v) is True


def test_channel_messages_are_incoming():
    # hi-word == 2 (channels/broadcast): stored from the subscriber's view.
    peer_id = 2 << 32
    v = _value(data_flags=0x00, flags=0)  # flags say outgoing, but channel wins
    assert message_is_outgoing(peer_id=peer_id, value=v) is False


def test_too_short_value_returns_none():
    assert decode_message_flags(b"\x00\x01\x02") is None


# --- Real-data regression fixtures (first 32 bytes captured from the live DB) ---
# "xan lover" chat (peer 38091753670). The user confirmed "Na karteczke..." was
# SENT; the old byte-10 heuristic mislabeled it as received.
REAL = {
    "nie bede mial kodu qr (received)": (
        bytes.fromhex("00 8a 08 0b 00 00 00 00 00 00 44 00 00 00 00 00"
                      "00 00 00 01 c6 08 72 de 08 00 00 00 15 00 00 00".replace(" ", "")),
        False,  # outgoing?
    ),
    "Na karteczke (SENT)": (
        bytes.fromhex("00 b0 08 0b 00 02 00 00 00 01 65 99 e8 f9 ee 6f"
                      "24 1b 00 00 00 00 00 00 00 00 00 01 b0 89 18 1f".replace(" ", "")),
        True,
    ),
    "to lepiej (SENT)": (
        bytes.fromhex("00 95 08 0b 00 02 00 00 00 01 19 b9 d7 10 14 a1"
                      "a3 83 00 00 00 00 00 00 00 00 00 01 b0 89 18 1f".replace(" ", "")),
        True,
    ),
}


def test_real_messages_direction():
    peer = 38091753670
    for label, (value, expected_out) in REAL.items():
        assert message_is_outgoing(peer_id=peer, value=value) is expected_out, label

#!/usr/bin/env python3
"""
postbox_parser.py — Parse Telegram Postbox database format
Extracts messages, peer info, and metadata from decrypted SQLCipher databases.

Tables in Telegram Postbox:
  t2  - Peers (users, channels, groups) with serialized info
  t3  - Peer presence/status
  t4  - Message index (key=peer+msgid, value=small metadata)
  t6  - Media references
  t7  - Full message data (key=peer+msgid+ns, value=serialized message)
  t12 - Message tags/labels
  t62 - Message global index
  ft41_content - Full-text search index of messages
"""

import struct
import json
import re
import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

from . import redact
from . import log_parser


def parse_peer_from_t2(key: int, value: bytes) -> Optional[Dict[str, Any]]:
    """Parse peer info from t2 table.

    Postbox binary format uses tagged fields:
      String fields: 02 + tag(2b) + 04 + length(uint32 LE) + utf-8 string
      Phone field:   01 + 'p' + 04 + length(uint32 LE) + utf-8 string

    Bot detection: User records carry a `bi` (bot_info) field whose type tag
    is 0x05 (struct present) for bots and 0x0b (nil) for non-bot users.
    Channels and groups omit the field entirely. Verified across 84k peers
    in the live snapshot — clean signal, including bots with non-"bot"
    usernames like @stickers, @botfather, @gif, @gamee.
    """
    peer = {'id': key}

    if b'\x02\x62\x69\x05' in value:
        peer['is_bot'] = True
    elif b'\x02\x62\x69\x0b' in value:
        peer['is_bot'] = False

    # Tag -> field name mapping for 02-prefixed string fields
    field_map = {
        b'fn': 'first_name',
        b'ln': 'last_name',
        b'un': 'username',
    }

    pos = 0
    val = value
    while pos < len(val) - 6:
        if val[pos] == 0x02:
            tag = val[pos + 1:pos + 3]
            if tag in field_map and val[pos + 3] == 0x04:
                if pos + 8 <= len(val):
                    strlen = struct.unpack('<I', val[pos + 4:pos + 8])[0]
                    if 0 < strlen < 500 and pos + 8 + strlen <= len(val):
                        try:
                            s = val[pos + 8:pos + 8 + strlen].decode('utf-8').strip()
                            if s:
                                peer[field_map[tag]] = s
                            pos += 8 + strlen
                            continue
                        except (UnicodeDecodeError, ValueError):
                            pass
        # Fields with 01 prefix: 01 + tag(1b) + 04 + length(uint32 LE) + string
        # 01 + 'p' = phone, 01 + 't' = title (channel/group name)
        elif val[pos] == 0x01 and pos + 2 < len(val) and val[pos + 2] == 0x04:
            tag_byte = val[pos + 1]
            if pos + 7 <= len(val):
                strlen = struct.unpack('<I', val[pos + 3:pos + 7])[0]
                if 0 < strlen < 500 and pos + 7 + strlen <= len(val):
                    try:
                        s = val[pos + 7:pos + 7 + strlen].decode('utf-8').strip()
                        if tag_byte == ord('p') and s and re.match(r'^\d{6,15}$', s):
                            peer['phone'] = s
                        elif tag_byte == ord('t') and s:
                            peer['title'] = s
                            if 'first_name' not in peer:
                                peer['first_name'] = s
                        pos += 7 + strlen
                        continue
                    except (UnicodeDecodeError, ValueError):
                        pass
        pos += 1

    # For secret chats (namespace=3): extract remote peer from 'r' field
    # Format: 01 72 01 <user_id as LE int32/int64>
    r_pos = value.find(b'\x01r\x01')
    if r_pos >= 0 and r_pos + 11 <= len(value):
        r_chunk = value[r_pos + 3:r_pos + 11]
        if len(r_chunk) >= 8:
            # Try 4-byte LE first, then 8-byte LE as composite PeerId
            lo4 = struct.unpack('<I', r_chunk[:4])[0]
            le8 = struct.unpack('<q', r_chunk[:8])[0]
            peer['_remote_peer_id_lo4'] = lo4
            peer['_remote_peer_id_le8'] = le8

    if len(peer) > 1:
        return peer
    return None


METADATA_STRINGS = frozenset({
    '_rawValue', 'entities', 'src', 'content', 'discriminator',
    'fileId', 'title', 'slug', 'innerColor', 'outerColor',
    'patternColor', 'textColor', 'patternFileId',
    'uns', 'sth', 'clclr', 'nclr', 'bgem', 'pclr', 'pgem',
    'ssc', 'vfid', 'emjs', 'biri', 'fl',
})

# Metadata field names that indicate serialized Postbox data when found as substrings
_METADATA_SUBSTRINGS = (
    '_rawValue', 'entities', 'channelId', 'fileId', 'discriminator',
    'patternColor', 'textColor', 'innerColor', 'outerColor',
    'patternFileId', 'bubbleUpEmojiOrStickersets', 'cidbubbleUp',
)


def _looks_like_metadata(text: str) -> bool:
    """Return True if text looks like serialized metadata, not a real message."""
    stripped = text.strip()
    if stripped in METADATA_STRINGS:
        return True
    # Filter short strings that are just field tags with padding
    if len(stripped) < 4 and not any(c.isalpha() for c in stripped):
        return True
    # Text containing null bytes is binary data, not a real message
    if '\x00' in stripped:
        return True
    # Check for metadata field names embedded in binary-mixed text
    for substr in _METADATA_SUBSTRINGS:
        if substr in stripped:
            return True
    return False


MIME_SIGNATURES = [
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'GIF87a', 'image/gif'),
    (b'GIF89a', 'image/gif'),
    (b'\x1a\x45\xdf\xa3', 'video/webm'),
    (b'OggS', 'audio/ogg'),
    (b'\xff\xfb', 'audio/mpeg'),
    (b'\xff\xf3', 'audio/mpeg'),
    (b'\xff\xf2', 'audio/mpeg'),
    (b'ID3', 'audio/mpeg'),
    (b'%PDF', 'application/pdf'),
    (b'\x1f\x8b', 'application/gzip'),
]


def detect_mime_type(filepath: Path) -> str:
    """Detect MIME type from file magic bytes.

    Telegram caches a lot of media without explicit filename suffixes —
    Lottie stickers go in as gzip(JSON) with no `.tgs` extension, SVG
    icons go in as gzip(XML), macOS app icons as `icns`. We peek inside
    these wrappers so the catalog doesn't misclassify them as documents.
    """
    try:
        with open(filepath, 'rb') as f:
            header = f.read(12)
    except OSError:
        return 'application/octet-stream'

    # Gzip wrapper: decompress a slice to identify the real format.
    if header[:2] == b'\x1f\x8b':
        try:
            import gzip
            with gzip.open(filepath, 'rb') as gh:
                inner = gh.read(256)
            stripped = inner.lstrip()
            # Lottie / TGS animated stickers — JSON object with the
            # frame-rate / in-point / out-point triple, often (but not
            # always) tagged with "tgs":.
            if (b'"tgs":' in inner) or (
                stripped[:1] == b'{'
                and b'"fr":' in inner
                and b'"ip":' in inner
                and b'"op":' in inner
            ):
                return 'application/x-tgsticker'
            # SVG (Telegram caches sticker pack thumbnails as gzipped SVG)
            if b'<svg' in inner or stripped[:5] == b'<?xml':
                return 'image/svg+xml'
        except Exception:
            pass
        return 'application/gzip'

    # macOS icon resource (.icns): app/sticker pack icons cached by Telegram.
    # The IconUtilities embed PNGs inside; treat the whole file as image.
    if header[:4] == b'icns':
        return 'image/icns'

    for sig, mime in MIME_SIGNATURES:
        if header.startswith(sig):
            return mime

    # RIFF container: check for WEBP at offset 8
    if header[:4] == b'RIFF' and len(header) >= 12 and header[8:12] == b'WEBP':
        return 'image/webp'

    # ftyp container (MP4/M4A/MOV): check subtype at offset 8
    if len(header) >= 12 and header[4:8] == b'ftyp':
        subtype = header[8:12]
        if subtype == b'M4A ':
            return 'audio/mp4'
        return 'video/mp4'

    return 'application/octet-stream'


def classify_media_type(mime: str, filename: str) -> str:
    """Classify a file into a media type category."""
    if mime == 'application/x-tgsticker' or '-tgs' in filename or filename.endswith('.tgs'):
        return 'sticker'
    if mime.startswith('image/gif'):
        return 'gif'
    if mime.startswith('image/'):
        # WebP files cached by Telegram are almost always static stickers
        # (real photos are stored as JPEG via the photo-size pipeline).
        # SVG and icns files are sticker-pack/app-icon assets, not user photos.
        if mime == 'image/webp' and 'photo-size' not in filename:
            return 'sticker'
        if mime in ('image/svg+xml', 'image/icns'):
            return 'sticker'
        return 'photo'
    if mime.startswith('video/'):
        return 'video'
    if mime.startswith('audio/'):
        return 'audio'
    return 'document'


def extract_text_from_message(value: bytes) -> Optional[str]:
    """Extract message text from t7 serialized value.

    Postbox uses length-prefixed strings in little-endian uint32 format.
    The main message text is typically the longest printable string found
    in the first 100 bytes of the value, excluding known metadata fields.
    """
    best_text = None
    best_len = 0

    for offset in range(4, min(100, len(value) - 4)):
        try:
            strlen = struct.unpack('<I', value[offset:offset + 4])[0]
        except struct.error:
            continue

        if strlen < 2 or strlen > 100000 or offset + 4 + strlen > len(value):
            continue

        try:
            decoded = value[offset + 4:offset + 4 + strlen].decode('utf-8')
        except (UnicodeDecodeError, ValueError):
            continue

        printable_count = sum(1 for c in decoded if c.isprintable() or c in '\n\r\t')
        if printable_count / max(len(decoded), 1) < 0.5:
            continue

        stripped = decoded.strip()
        if _looks_like_metadata(stripped):
            continue

        if len(stripped) > best_len:
            best_text = stripped
            best_len = len(stripped)

    return best_text


def extract_media_refs(value: bytes) -> List[Dict[str, Any]]:
    """Extract media file references from a serialized message/media value.

    Telegram serializes file references in two encodings, both of which appear
    in t7 message values and t6 media metadata:

    1. Int64 form (regular chats):
         01 69 01 <8b LE file_id>
       Adjacent fields hold dc_id (`01 64 00 <4b LE>`) and dimensions
       (`02 64 78/79 00 <4b LE>`).

    2. Bytes-blob form (t6 entries; secret chats; newer message variants):
         01 69 0a 0c <4b BE dc_id> <4b zero pad> <8b LE file_id>
       This is a length-prefixed bytes field where the embedded blob carries
       the dc_id and file_id together. Older parser builds missed this and
       therefore failed to link any secret-chat media to its on-disk file.

    Returns a deduplicated list with keys: file_id, dc_id, width, height.
    """
    refs = []

    def _add(file_id: int, dc_id: int, w: int = 0, h: int = 0) -> None:
        if file_id == 0:
            return
        if any(r['file_id'] == file_id for r in refs):
            return
        ref = {'file_id': file_id, 'dc_id': dc_id}
        if w:
            ref['width'] = w
        if h:
            ref['height'] = h
        refs.append(ref)

    def _scan_dimensions(idx: int) -> tuple:
        """Look up dx/dy Int32 fields in a 80-byte window around idx."""
        window = value[max(0, idx - 80):min(len(value), idx + 80)]
        w = h = 0
        dx = window.find(b'\x02\x64\x78\x00')
        if 0 <= dx and dx + 8 <= len(window):
            w = struct.unpack('<I', window[dx + 4:dx + 8])[0]
            if w > 10000:
                w = 0
        dy = window.find(b'\x02\x64\x79\x00')
        if 0 <= dy and dy + 8 <= len(window):
            h = struct.unpack('<I', window[dy + 4:dy + 8])[0]
            if h > 10000:
                h = 0
        return w, h

    # --- Form 1: Int64 file_id with separate dc_id ---
    pos = 0
    while pos < len(value) - 10:
        idx = value.find(b'\x01\x69\x01', pos)
        if idx < 0:
            break
        if idx + 11 > len(value):  # marker too close to EOF for a full int64
            break
        file_id = struct.unpack('<q', value[idx + 3:idx + 11])[0]
        if file_id == 0:
            pos = idx + 11
            continue

        dc_id = 0
        window = value[max(0, idx - 80):min(len(value), idx + 80)]
        d_pos = window.find(b'\x01\x64\x00')
        if 0 <= d_pos and d_pos + 7 <= len(window):
            dc_id = struct.unpack('<I', window[d_pos + 3:d_pos + 7])[0]
            if dc_id > 10:
                dc_id = 0

        w, h = _scan_dimensions(idx)
        _add(file_id, dc_id, w, h)
        pos = idx + 11

    # --- Form 2: Bytes-blob form (i Bytes 0x0c) ---
    # Pattern: 01 69 0a 0c <dc_id 4 BE> <3b pad> <file_id 8 LE>
    # The file_id is *signed* int64 — outgoing secret-chat local refs use
    # negative ids (e.g. -1155884980421330152 → local-file--1155884980421330152
    # on disk). Verified empirically against:
    #   • xan-lover incoming `secret-file-5809961205153930870-4`
    #   • czarnetlo outgoing `local-file--1155884980421330152`
    # Earlier versions read file_id at idx+12, which is off by one and yielded
    # nonsense ids that never matched disk — the incoming xan-lover case still
    # worked only because Form 1 (`01 69 01`) also fired with the right id.
    pos = 0
    while pos < len(value) - 18:
        idx = value.find(b'\x01\x69\x0a\x0c', pos)
        if idx < 0:
            break
        if idx + 19 > len(value):
            break
        try:
            dc_id = struct.unpack('>I', value[idx + 4:idx + 8])[0]
            file_id = struct.unpack('<q', value[idx + 11:idx + 19])[0]
        except struct.error:
            pos = idx + 4
            continue

        # File IDs are large 64-bit numbers; small values here are just
        # random byte alignments. The dc_id sanity is loose — resolve()
        # will iterate DCs anyway if our guess doesn't match disk.
        if abs(file_id) > 1_000_000_000:
            if not (1 <= dc_id <= 10):
                dc_id = 0
            w, h = _scan_dimensions(idx)
            _add(file_id, dc_id, w, h)
        pos = idx + 4

    return refs


def resolve_media_files(refs: List[Dict], media_index: set) -> List[Dict[str, Any]]:
    """Resolve media refs to actual filenames on disk.

    Telegram caches media under four naming schemes depending on origin:
      • `telegram-cloud-photo-size-{dc}-{fid}-{suffix}` — multi-size cloud photos
      • `telegram-cloud-document-{dc}-{fid}`           — cloud documents
      • `secret-file-{fid}-{dc}[.ext]`                 — secret-chat E2E media
                                                        (incoming from peer)
      • `local-file-{fid}` / `local-file--{abs}`       — outgoing photos before
                                                        upload completes (and
                                                        often kept after); the
                                                        signed file_id appears
                                                        as `local-file--<abs>`
                                                        when negative

    Secret chat media uses a flipped dc/fid order and an optional extension
    suffix (`.jpg`, `.mp3`, etc.). We try each scheme in turn; if dc_id was
    not pinned during extraction we sweep DCs 1..10. local-file is dc-less.
    """
    resolved = []
    photo_suffixes = ['y', 'x', 'w', 'm', 'c', 's', 'a', 'b']
    # Common extensions Telegram appends to secret-file caches (and the
    # bare-no-extension form which is most common).
    secret_ext_suffixes = ['', '.jpg', '.mp4', '.mp3', '.webm', '.ogg', '.png']

    def _try_dc(fid, dc):
        for suffix in photo_suffixes:
            cand = f"telegram-cloud-photo-size-{dc}-{fid}-{suffix}"
            if cand in media_index:
                return cand
        cand = f"telegram-cloud-document-{dc}-{fid}"
        if cand in media_index:
            return cand
        for ext in secret_ext_suffixes:
            cand = f"secret-file-{fid}-{dc}{ext}"
            if cand in media_index:
                return cand
        return None

    def _try_local(fid):
        # Outgoing local files don't use dc_id. Negative ids are rendered
        # with a double dash because the dash is both a separator and part
        # of the negative sign: local-file--1155884980421330152.
        if fid < 0:
            cand = f"local-file--{abs(fid)}"
        else:
            cand = f"local-file-{fid}"
        return cand if cand in media_index else None

    for ref in refs:
        fid = ref['file_id']
        dc = ref.get('dc_id', 0)
        matched = _try_dc(fid, dc) if dc else None

        if not matched:
            for try_dc in range(1, 11):
                matched = _try_dc(fid, try_dc)
                if matched:
                    break

        if not matched:
            matched = _try_local(fid)

        if matched:
            entry = dict(ref)
            entry['filename'] = matched
            resolved.append(entry)

    return resolved


def build_media_index(media_dir: Path) -> set:
    """Build a set of media filenames for fast lookup."""
    if not media_dir.is_dir():
        return set()
    index = set()
    for f in media_dir.iterdir():
        if f.is_file() and not f.name.endswith('_partial.meta') and not f.name.endswith('_partial'):
            index.add(f.name)
    return index


def build_media_catalog(
    media_dir: Path,
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build a full media catalog by scanning the media directory.

    Cross-references files against parsed messages to link media to conversations.
    Returns a list of catalog entries with MIME type, size, dimensions, and linkage.
    """
    if not media_dir.is_dir():
        return []

    # Build filename -> message media info lookup from messages.json
    filename_to_msg: Dict[str, Dict[str, Any]] = {}
    for msg in messages:
        for m in msg.get('media', []):
            fname = m.get('filename')
            if fname and fname not in filename_to_msg:
                filename_to_msg[fname] = {
                    'peer_id': msg.get('peer_id'),
                    'peer_name': msg.get('peer_name'),
                    'timestamp': msg.get('timestamp'),
                    'date': msg.get('date'),
                    'width': m.get('width'),
                    'height': m.get('height'),
                }

    catalog = []
    file_count = 0

    for filepath in sorted(media_dir.iterdir()):
        if not filepath.is_file():
            continue
        if filepath.name.endswith('_partial') or filepath.name.endswith('_partial.meta'):
            continue

        file_count += 1
        if file_count % 500 == 0:
            print(f"    Scanning media: {file_count} files...")

        try:
            stat = filepath.stat()
        except OSError:
            continue
        mime = detect_mime_type(filepath)
        media_type = classify_media_type(mime, filepath.name)

        entry = {
            'filename': filepath.name,
            'mime_type': mime,
            'size_bytes': stat.st_size,
            'width': None,
            'height': None,
            'media_type': media_type,
            'thumbnail': None,
            'linked_message': None,
        }

        # For photos, find a smaller Telegram variant for thumbnails
        # Telegram stores photos with suffixes: y(largest), x, w, m, c, s(smallest)
        if media_type == 'photo' and 'telegram-cloud-photo-size-' in filepath.name:
            base = filepath.name.rsplit('-', 1)[0]  # strip the suffix
            for thumb_suffix in ['s', 'm', 'c']:
                thumb_name = base + '-' + thumb_suffix
                if (media_dir / thumb_name).is_file():
                    entry['thumbnail'] = thumb_name
                    break

        # Link to message if available
        msg_info = filename_to_msg.get(filepath.name)
        if msg_info:
            entry['width'] = msg_info.get('width')
            entry['height'] = msg_info.get('height')
            entry['linked_message'] = {
                'peer_id': msg_info['peer_id'],
                'peer_name': msg_info.get('peer_name'),
                'timestamp': msg_info.get('timestamp'),
                'date': msg_info.get('date'),
            }

        catalog.append(entry)

    print(f"    Media catalog: {len(catalog)} files ({file_count} scanned)")
    return catalog


def parse_message_key(key: bytes) -> Dict[str, Any]:
    """Parse t7 message key (20 bytes).

    Format: namespace_hi(4b BE) + peer_id_lo(4b BE) + padding(4b) + timestamp(4b BE) + msg_tag(4b BE)
    - Bytes 0-7: peer_id as big-endian int64
    - Bytes 8-11: zero padding
    - Bytes 12-15: Unix timestamp (big-endian uint32)
    - Bytes 16-19: message namespace/tag
    """
    result = {}
    if len(key) >= 8:
        result['peer_id'] = struct.unpack('>q', key[:8])[0]
    if len(key) >= 16:
        ts = struct.unpack('>I', key[12:16])[0]
        if 1000000000 < ts < 2000000000:
            result['timestamp'] = ts
    if len(key) >= 20:
        result['namespace'] = struct.unpack('>I', key[16:20])[0]
    return result


# Postbox MessageFlags bit (Message.swift): set on messages received from the
# peer, clear on messages sent by the local user.
MESSAGE_FLAG_INCOMING = 0x04

# MessageDataFlags optional fields, in serialization order, with the byte size
# each writes when its bit is set in the dataFlags byte (MessageHistoryTable.swift).
_MESSAGE_OPTIONAL_FIELDS = (
    (0x01, 8),  # hasGloballyUniqueId -> Int64
    (0x02, 4),  # hasGlobalTags       -> UInt32
    (0x04, 8),  # hasGroupingKey      -> Int64
    (0x08, 4),  # hasGroupInfo        -> UInt32 (stableId)
    (0x10, 4),  # hasLocalTags        -> UInt32
    (0x20, 8),  # hasThreadId         -> Int64
)


def decode_message_flags(value: bytes) -> Optional[int]:
    """Decode the Postbox MessageFlags UInt32 from a serialized t7 message value.

    Layout (MessageHistoryTable.swift justInsertMessage):
        type Int8 (1) | stableId UInt32 (4) | stableVersion UInt32 (4) |
        dataFlags Int8 (1) | <optional fields per dataFlags bits> |
        flags UInt32 | tags UInt32

    The optional block between `dataFlags` (offset 9) and `flags` is
    variable-length, so the flags field is at offset 10 only when dataFlags == 0.
    Earlier builds hardcoded offset 10 and therefore read direction from random
    id bytes for every message carrying a globallyUniqueId, threadId, grouping
    key, etc. — mixing sent and received messages.

    Returns the MessageFlags value, or None if the value is too short to contain it.
    """
    if len(value) < 14:
        return None
    offset = 10
    data_flags = value[9]
    for bit, size in _MESSAGE_OPTIONAL_FIELDS:
        if data_flags & bit:
            offset += size
    if offset + 4 > len(value):
        return None
    return struct.unpack('<I', value[offset:offset + 4])[0]


def message_is_outgoing(peer_id: int, value: bytes) -> bool:
    """Decide whether a t7 message was sent by the local user (vs received).

    Channels/broadcast (peer hi-word == 2) are stored from the subscriber's
    perspective and treated as incoming. Everything else (users, bots, groups,
    secret chats) reads the authoritative MessageFlags.Incoming bit, decoded at
    the correct, dataFlags-dependent offset.
    """
    hi = (peer_id >> 32) & 0xFFFFFFFF
    if hi == 2:
        return False
    flags = decode_message_flags(value)
    if flags is None:
        return False
    return not bool(flags & MESSAGE_FLAG_INCOMING)


def parse_messages_from_t7(
    conn, peers: Dict[int, Dict], media_dir: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Parse all messages from t7 table."""
    messages = []
    batch_size = 10000

    media_index = build_media_index(media_dir) if media_dir else set()
    if media_index:
        print(f"    Media index: {len(media_index)} files")

    # Keyset pagination on rowid. LIMIT/OFFSET re-scans every skipped row each
    # batch (O(n^2) on 100k+ message tables); `rowid > last` is linear.
    last_rowid = 0
    processed = 0
    parse_errors = 0
    while True:
        rows = conn.execute(
            'SELECT rowid, key, value FROM t7 '
            'WHERE length(value) > 20 AND rowid > ? ORDER BY rowid LIMIT ?',
            (last_rowid, batch_size),
        ).fetchall()

        if not rows:
            break

        for rowid, key, value in rows:
            last_rowid = rowid
            processed += 1
            # One malformed row must never abort the whole account export — a
            # single bad serialized value used to take down all 100k+ messages.
            try:
                if len(key) < 16:
                    continue

                key_info = parse_message_key(key)
                peer_id = key_info.get('peer_id')
                if not peer_id:
                    continue

                text = extract_text_from_message(value)
                media = resolve_media_files(extract_media_refs(value), media_index) if media_index else []

                # For media-only messages, clear garbage text (binary noise)
                if text:
                    printable = sum(1 for c in text if c.isprintable() or c in '\n\r\t')
                    if printable / max(len(text), 1) < 0.5:
                        text = None

                # Skip messages with no text and no media
                if not text and not media:
                    continue

                # Direction from the authoritative Postbox MessageFlags.Incoming
                # bit, decoded at its real (dataFlags-dependent) offset. See
                # decode_message_flags / message_is_outgoing. This also covers
                # secret chats correctly — their value byte 10 is a random id
                # byte, which is exactly why the prior byte-10 heuristic mixed
                # sent/received messages.
                is_outgoing = message_is_outgoing(peer_id, value)

                msg = {
                    'peer_id': peer_id,
                    'text': text or '',
                    'outgoing': is_outgoing,
                }

                if media:
                    msg['media'] = media

                timestamp = key_info.get('timestamp')
                if timestamp:
                    msg['timestamp'] = timestamp
                    msg['date'] = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

                peer_info = peers.get(peer_id)

                if peer_info:
                    name_parts = []
                    if 'first_name' in peer_info:
                        name_parts.append(peer_info['first_name'])
                    if 'last_name' in peer_info:
                        name_parts.append(peer_info['last_name'])
                    if name_parts:
                        msg['peer_name'] = ' '.join(name_parts)
                    if 'username' in peer_info:
                        msg['peer_username'] = peer_info['username']

                    # For secret chats: resolve remote peer name
                    hi = (peer_id >> 32) & 0xFFFFFFFF
                    if hi == 3 and not msg.get('peer_name'):
                        remote_id = peer_info.get('_remote_peer_id_lo4')
                        remote_le8 = peer_info.get('_remote_peer_id_le8')
                        remote_peer = peers.get(remote_id) if remote_id else None
                        if not remote_peer and remote_le8:
                            remote_peer = peers.get(remote_le8)
                        if remote_peer:
                            rname = remote_peer.get('first_name', '')
                            if 'last_name' in remote_peer:
                                rname += ' ' + remote_peer['last_name']
                            msg['peer_name'] = rname.strip()
                            if 'username' in remote_peer:
                                msg['peer_username'] = remote_peer['username']
                            msg['secret_chat'] = True

                messages.append(msg)
            except Exception as e:
                parse_errors += 1
                if parse_errors <= 5:
                    print(f"    t7 row parse error (rowid {rowid}): {e}")
                continue

        if processed % 50000 == 0:
            print(f"    Processed {processed:,} rows, {len(messages):,} messages extracted...")

    if parse_errors:
        print(f"    Skipped {parse_errors:,} unparseable t7 row(s)")

    return messages


def parse_messages_from_fts(conn) -> List[Dict[str, Any]]:
    """Parse messages from full-text search index (ft41_content)."""
    messages = []

    try:
        rows = conn.execute('SELECT id, c0, c1, c2, c3 FROM ft41_content WHERE c2 != ""').fetchall()
        for row_id, peer_ref, msg_ref, text, extra in rows:
            if not text or len(text.strip()) < 1:
                continue

            msg = {
                'fts_id': row_id,
                'peer_ref': peer_ref,
                'msg_ref': msg_ref,
                'text': text.strip(),
                'source': 'fts'
            }
            if extra:
                msg['extra'] = extra
            messages.append(msg)

    except Exception as e:
        print(f"    FTS extraction error: {e}")

    return messages


def export_account(
    conn,
    account_id: str,
    output_dir: Path,
    backup_dir: Optional[Path] = None,
    log_events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Export all data from one account database.

    `log_events` is the pre-parsed output of `log_parser.parse_logs_dir`
    against `<backup>/logs/`. Logs are shared across the whole backup, but
    cross-reference is per-account (we match each `encrypted_message`
    against the account's own t7 dump), so we accept the raw list here and
    deep-copy + annotate inside.
    """
    account_dir = output_dir / f"account-{account_id}"
    account_dir.mkdir(parents=True, exist_ok=True)

    result = {
        'account_id': account_id,
        'peers': 0,
        'messages_t7': 0,
        'messages_fts': 0,
    }

    # Step 1: Parse peers from t2
    print("  Parsing peers (t2)...")
    peers = {}
    try:
        rows = conn.execute('SELECT key, value FROM t2').fetchall()
        for key, value in rows:
            peer = parse_peer_from_t2(key, value)
            if peer:
                peers[peer['id']] = peer
        print(f"    Found {len(peers)} peers with names")
        result['peers'] = len(peers)
    except Exception as e:
        print(f"    Peer parsing error: {e}")

    # Save peers
    if peers:
        with open(account_dir / 'peers.json', 'w', encoding='utf-8') as f:
            json.dump(list(peers.values()), f, indent=2, ensure_ascii=False)

    # Step 2: Parse messages from t7
    print("  Parsing messages (t7)...")
    media_dir = None
    if backup_dir:
        media_dir = backup_dir / f"account-{account_id}" / "postbox" / "media"
        if not media_dir.is_dir():
            media_dir = None
    messages_t7 = parse_messages_from_t7(conn, peers, media_dir)
    media_count = sum(1 for m in messages_t7 if m.get('media'))
    print(f"    Extracted {len(messages_t7):,} messages from t7 ({media_count:,} with media)")
    result['messages_t7'] = len(messages_t7)

    # Save t7 messages
    if messages_t7:
        with open(account_dir / 'messages.json', 'w', encoding='utf-8') as f:
            json.dump(messages_t7, f, indent=2, ensure_ascii=False)

    # Step 2b: Cross-reference forensic log events against this account's t7.
    # Logs are shared across the backup so we receive them pre-parsed; the
    # cross-reference is per-account because only the local t7 rows tell us
    # whether a message survived. Deep-copy the event dicts before annotating
    # so a second account doesn't inherit the first account's `in_db` flags.
    if log_events is not None:
        per_account_events = [dict(ev) for ev in log_events]
        log_parser.cross_reference_with_messages(per_account_events, messages_t7)
        with open(account_dir / 'log_events.json', 'w', encoding='utf-8') as f:
            json.dump(per_account_events, f, indent=2, ensure_ascii=False)
        ghosts = sum(
            1 for ev in per_account_events
            if ev['event'] == 'encrypted_message' and ev.get('in_db') is False
        )
        print(
            f"    Log events: {len(per_account_events)} "
            f"({ghosts} ghost messages flagged)"
        )
        result['log_events'] = len(per_account_events)
        result['ghost_messages'] = ghosts

    # Step 3: Parse FTS messages
    print("  Parsing full-text search index (ft41)...")
    messages_fts = parse_messages_from_fts(conn)
    print(f"    Extracted {len(messages_fts):,} messages from FTS")
    result['messages_fts'] = len(messages_fts)

    if messages_fts:
        with open(account_dir / 'messages_fts.json', 'w', encoding='utf-8') as f:
            json.dump(messages_fts, f, indent=2, ensure_ascii=False)

    # Step 3b: Build media catalog (full scan of media/ directory)
    if media_dir and media_dir.is_dir():
        print("  Building media catalog...")
        catalog = build_media_catalog(media_dir, messages_t7)
        if catalog:
            with open(account_dir / 'media_catalog.json', 'w', encoding='utf-8') as f:
                json.dump(catalog, f, indent=2, ensure_ascii=False)
        result['media_files'] = len(catalog)

    # Step 3c: Build storage catalog (plaintext sidecar DBs — surfaces tombstones)
    if backup_dir is not None:
        from .storage_db_parser import build_storage_catalog
        src_account_dir = backup_dir / f"account-{account_id}"
        try:
            storage_catalog = build_storage_catalog(src_account_dir, media_dir or Path())
        except Exception as e:
            print(f"    Storage catalog error: {e}")
            storage_catalog = []
        if storage_catalog:
            with open(account_dir / 'storage_catalog.json', 'w', encoding='utf-8') as f:
                json.dump(storage_catalog, f, indent=2, ensure_ascii=False)
        tombstones = sum(1 for e in storage_catalog if not e.get('on_disk'))
        print(f"    Storage catalog: {len(storage_catalog)} entries ({tombstones} tombstones)")
        result['storage_entries'] = len(storage_catalog)
        result['storage_tombstones'] = tombstones

    # Step 3d: History diff vs the previous parsed snapshot (run-vs-run drift).
    # Identifies messages that were in t7 last run but are gone now — independent
    # forensic signal alongside the in-process WAL detection.
    if backup_dir is not None:
        from . import ghost_detector
        try:
            prev = ghost_detector.find_previous_snapshot(
                output_dir.resolve(), backup_dir.parent.parent.resolve()
            )
            if prev is not None and (prev / f"account-{account_id}" / "messages.json").exists():
                with open(prev / f"account-{account_id}" / "messages.json", encoding='utf-8') as fh:
                    prev_msgs = json.load(fh)
                history_diff = ghost_detector.diff_snapshots(prev_msgs, messages_t7)
                history_diff['previous_snapshot'] = str(prev)
                with open(account_dir / 'ghosts_history.json', 'w', encoding='utf-8') as f:
                    json.dump(history_diff, f, indent=2, ensure_ascii=False)
                print(
                    f"    History diff vs {prev.parent.name}: "
                    f"{len(history_diff['removed'])} removed, "
                    f"{len(history_diff['added'])} added, "
                    f"{len(history_diff['modified'])} modified"
                )
                result['history_removed'] = len(history_diff['removed'])
                result['history_added'] = len(history_diff['added'])
                result['history_modified'] = len(history_diff['modified'])
        except Exception as e:
            print(f"    History diff error: {e}")

    # Step 4: Create combined export, deduping FTS entries that already exist in
    # t7 and attributing each surviving FTS row to its peer.
    #
    # FTS `peer_ref` carries the *bare* peer id ("p<id>") without the namespace
    # hi-word, while t7 `peer_id` is the composite (namespace<<32 | id). Index
    # peers by both forms so a bare FTS ref resolves to the same composite id t7
    # uses: that lets dedup match across the namespace boundary AND lets
    # conversation grouping attribute FTS-only rows (the deleted-message gold)
    # to a real peer instead of a single 'unknown' bucket.
    peers_by_bare: Dict[int, Dict] = {}
    for pid, prec in peers.items():
        if isinstance(pid, int):
            peers_by_bare.setdefault(pid & 0xFFFFFFFF, prec)

    def _resolve_fts_peer(peer_ref: Any) -> Tuple[Optional[int], Optional[Dict]]:
        raw = str(peer_ref if peer_ref is not None else '').lstrip('p')
        try:
            bare = int(raw) if raw else None
        except ValueError:
            return None, None
        if bare is None:
            return None, None
        prec = peers.get(bare) or peers_by_bare.get(bare)
        return (prec['id'] if prec else bare), prec

    # Dedup key carries both the composite peer_id and its bare lower-32-bit
    # form so a bare FTS ref matches a namespaced t7 peer.
    seen = set()
    for m in messages_t7:
        if not m.get('text'):
            continue
        pid = m.get('peer_id')
        seen.add((pid, m['text']))
        if isinstance(pid, int):
            seen.add((pid & 0xFFFFFFFF, m['text']))

    fts_dedup = []
    for m in messages_fts:
        text = m.get('text', '')
        resolved_id, prec = _resolve_fts_peer(m.get('peer_ref'))
        if (resolved_id, text) in seen:
            continue
        enriched = dict(m)
        if resolved_id is not None:
            enriched['peer_id'] = resolved_id
        if prec:
            name_parts = [prec[k] for k in ('first_name', 'last_name') if prec.get(k)]
            if name_parts:
                enriched['peer_name'] = ' '.join(name_parts)
            if prec.get('username'):
                enriched['peer_username'] = prec['username']
        fts_dedup.append(enriched)
    all_messages = messages_t7 + fts_dedup

    all_messages.sort(key=lambda m: m.get('timestamp', 0), reverse=True)

    with open(account_dir / 'all_messages.json', 'w', encoding='utf-8') as f:
        json.dump(all_messages, f, indent=2, ensure_ascii=False)

    # Step 5: Group by conversation
    print("  Grouping into conversations...")
    conversations = {}
    for msg in all_messages:
        peer_key = msg.get('peer_username') or msg.get('peer_name') or str(msg.get('peer_id', 'unknown'))
        if peer_key not in conversations:
            conversations[peer_key] = {
                'peer_id': msg.get('peer_id'),
                'all_peer_ids': set(),
                'peer_name': msg.get('peer_name'),
                'peer_username': msg.get('peer_username'),
                'message_count': 0,
                'messages': [],
            }
        mid = msg.get('peer_id')
        if mid is not None:
            conversations[peer_key]['all_peer_ids'].add(mid)
        conversations[peer_key]['message_count'] += 1
        conversations[peer_key]['messages'].append({
            'text': msg['text'],
            'date': msg.get('date', ''),
            'timestamp': msg.get('timestamp', 0),
        })

    # Sort conversations by message count
    sorted_convos = sorted(conversations.values(), key=lambda c: c['message_count'], reverse=True)

    # Save conversations index
    convo_index = [
        {
            'peer_id': c['peer_id'],
            'all_peer_ids': sorted(c['all_peer_ids']),
            'peer_name': c['peer_name'],
            'peer_username': c['peer_username'],
            'message_count': c['message_count'],
            'first_message': c['messages'][-1]['date'] if c['messages'] else None,
            'last_message': c['messages'][0]['date'] if c['messages'] else None,
        }
        for c in sorted_convos
    ]

    with open(account_dir / 'conversations_index.json', 'w', encoding='utf-8') as f:
        json.dump(convo_index, f, indent=2, ensure_ascii=False)

    # Save individual conversations
    convos_dir = account_dir / 'conversations'
    convos_dir.mkdir(exist_ok=True)
    for convo in sorted_convos:
        export_convo = {**convo, 'all_peer_ids': sorted(convo['all_peer_ids'])}
        pid = convo.get('peer_id')
        label = convo.get('peer_username') or convo.get('peer_name') or (
            str(pid) if pid is not None else 'unknown'
        )
        safe_name = re.sub(r'[^\w\-]', '_', str(label))[:80]
        # Suffix with peer_id so two distinct peers whose names sanitize to the
        # same string don't overwrite each other's file.
        if pid is not None:
            safe_name = f"{safe_name}-{pid}"
        with open(convos_dir / f'{safe_name}.json', 'w', encoding='utf-8') as f:
            json.dump(export_convo, f, indent=2, ensure_ascii=False)

    print(f"    {len(conversations)} conversations saved")
    print(f"    Total combined: {len(all_messages):,} messages")
    result['total_messages'] = len(all_messages)
    result['conversations'] = len(conversations)

    return result


def open_database(db_path: str, db_key: bytes, db_salt: bytes):
    """Open SQLCipher database with Telegram settings."""
    try:
        import sqlcipher3
    except ImportError:
        print("ERROR: sqlcipher3 required. Install with: pip install sqlcipher3")
        sys.exit(1)

    hex_key = (db_key + db_salt).hex()
    conn = sqlcipher3.connect(db_path)
    conn.execute("PRAGMA cipher_default_plaintext_header_size = 32")
    conn.execute(f'PRAGMA key = "x\'{hex_key}\'"')

    # Verify
    count = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    print(f"  Database opened: {count} schema objects")
    return conn


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Parse Telegram Postbox database')
    parser.add_argument('backup_dir', help='Backup directory with account-* folders')
    parser.add_argument('--db-key', help='Hex database key (32 bytes)')
    parser.add_argument('--db-salt', help='Hex database salt (16 bytes)')
    parser.add_argument('--tempkey', help='Path to .tempkeyEncrypted')
    parser.add_argument('--password', default='no-matter-key', help='Passcode')
    parser.add_argument('--output', help='Output directory')
    parser.add_argument('--account', help='Only process specific account ID')
    parser.add_argument('--redact', action='store_true',
                        help='Mask sensitive values (account IDs, keys, paths) in console output')

    args = parser.parse_args()
    redact.set_enabled(args.redact)
    backup_dir = Path(args.backup_dir)

    if not backup_dir.exists():
        print(f"ERROR: {redact.path(backup_dir)} not found")
        sys.exit(1)

    # Get keys
    if args.db_key and args.db_salt:
        db_key = bytes.fromhex(args.db_key)
        db_salt = bytes.fromhex(args.db_salt)
    else:
        # Import decrypt function from tg_appstore_decrypt
        from .tg_appstore_decrypt import decrypt_tempkey

        tempkey_path = args.tempkey
        if not tempkey_path:
            for candidate in [backup_dir / '.tempkeyEncrypted', backup_dir / 'appstore' / '.tempkeyEncrypted']:
                if candidate.exists():
                    tempkey_path = str(candidate)
                    break

        if not tempkey_path:
            print("ERROR: No --db-key/--db-salt and no .tempkeyEncrypted found")
            sys.exit(1)

        print(f"Decrypting tempkey: {redact.path(tempkey_path)}")
        db_key, db_salt = decrypt_tempkey(tempkey_path, args.password)
        print(f"  Key: {redact.hexkey(db_key.hex()[:8] + '...' + db_key.hex()[-4:])}")
        print(f"  Salt: {redact.hexkey(db_salt.hex()[:8] + '...' + db_salt.hex()[-4:])}")

    output_dir = Path(args.output) if args.output else backup_dir / "parsed_data"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find accounts
    if args.account:
        account_dirs = [backup_dir / f"account-{args.account}"]
    else:
        account_dirs = sorted(backup_dir.glob("account-*"))

    if not account_dirs:
        print("No account directories found")
        sys.exit(1)

    # Pre-parse logs once per backup (logs are at backup root, not per-account).
    log_events: Optional[List[Dict[str, Any]]] = None
    logs_dir = backup_dir / "logs"
    if logs_dir.is_dir():
        print(f"\nParsing forensic logs from {redact.path(logs_dir)}...")
        log_events = log_parser.parse_logs_dir(logs_dir)
        counts = log_parser.summarize(log_events)
        counts.pop('__ghosts__', None)
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"  {len(log_events)} log events ({breakdown})")

    results = {}

    for account_dir in account_dirs:
        if not account_dir.is_dir():
            continue

        account_id = account_dir.name.replace('account-', '')
        db_path = account_dir / "postbox" / "db" / "db_sqlite"

        if not db_path.exists():
            print(f"\nAccount {redact.account(account_id)}: no database")
            continue

        db_size_mb = db_path.stat().st_size / 1024 / 1024
        print(f"\n{'='*60}")
        print(f"Account: {redact.account(account_id)} ({db_size_mb:.1f} MB)")
        print(f"{'='*60}")

        try:
            conn = open_database(str(db_path), db_key, db_salt)
            result = export_account(
                conn, account_id, output_dir, backup_dir, log_events=log_events
            )
            results[account_id] = result
            conn.close()
        except Exception as e:
            print(f"  ERROR: {e}")
            results[account_id] = {'error': str(e)}

    # Save summary
    summary = {
        'timestamp': datetime.now(tz=timezone.utc).isoformat(),
        'backup_dir': str(backup_dir),
        'accounts': results,
        'total_messages': sum(r.get('total_messages', 0) for r in results.values())
    }

    with open(output_dir / 'summary.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"EXPORT COMPLETE")
    print(f"  Total messages: {summary['total_messages']:,}")
    print(f"  Output: {redact.path(output_dir)}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()

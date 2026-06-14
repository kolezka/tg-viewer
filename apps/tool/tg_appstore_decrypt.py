#!/usr/bin/env python3
"""
tg_appstore_decrypt.py — Telegram App Store Database Decryptor
Based on exact SQLCipher configuration from Telegram-iOS/macOS source code.

Key derivation:
  .tempkeyEncrypted is AES-CBC encrypted with SHA-512("no-matter-key")
  Decrypted file contains: dbKey(32b) + dbSalt(16b) + hash(4b) + padding

SQLCipher config:
  PRAGMA cipher_plaintext_header_size = 32
  PRAGMA key = "x'<hex(dbKey + dbSalt)>'"
  (raw key mode, bypasses PBKDF2)
"""

import os
import sys
import json
import struct
import hashlib
import argparse
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any
from datetime import datetime

from . import redact

# AES-CBC decryption
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend


def murmurhash3_x86_32(data: bytes, seed: int = 0) -> int:
    """MurmurHash3 x86 32-bit implementation."""
    length = len(data)
    h1 = seed & 0xFFFFFFFF
    c1 = 0xCC9E2D51
    c2 = 0x1B873593
    rounded_end = (length & 0xFFFFFFFC)

    for i in range(0, rounded_end, 4):
        k1 = (
            (data[i] & 0xFF)
            | ((data[i + 1] & 0xFF) << 8)
            | ((data[i + 2] & 0xFF) << 16)
            | (data[i + 3] << 24)
        )
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 ^= k1
        h1 = ((h1 << 13) | (h1 >> 19)) & 0xFFFFFFFF
        h1 = (h1 * 5 + 0xE6546B64) & 0xFFFFFFFF

    k1 = 0
    remaining = length & 3
    if remaining >= 3:
        k1 ^= (data[rounded_end + 2] & 0xFF) << 16
    if remaining >= 2:
        k1 ^= (data[rounded_end + 1] & 0xFF) << 8
    if remaining >= 1:
        k1 ^= data[rounded_end] & 0xFF
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 ^= k1

    h1 ^= length
    h1 ^= (h1 >> 16)
    h1 = (h1 * 0x85EBCA6B) & 0xFFFFFFFF
    h1 ^= (h1 >> 13)
    h1 = (h1 * 0xC2B2AE35) & 0xFFFFFFFF
    h1 ^= (h1 >> 16)

    # Return as signed int32
    if h1 >= 0x80000000:
        h1 -= 0x100000000
    return h1


def decrypt_tempkey(encrypted_path: str, password: str = "no-matter-key") -> Tuple[bytes, bytes]:
    """
    Decrypt .tempkeyEncrypted to extract dbKey and dbSalt.

    Key derivation: SHA-512(password) -> aes_key(32b) + aes_iv(16b from end)
    Decryption: AES-256-CBC
    Result: dbKey(32b) + dbSalt(16b) + hash(4b) + padding
    """
    with open(encrypted_path, 'rb') as f:
        encrypted = f.read()

    print(f"  Encrypted tempkey size: {len(encrypted)} bytes")

    digest = hashlib.sha512(password.encode('utf-8')).digest()
    aes_key = digest[:32]
    aes_iv = digest[-16:]

    if len(encrypted) < 52:
        raise ValueError(
            f"Encrypted tempkey too short: {len(encrypted)} bytes (need at least 52)"
        )

    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv), backend=default_backend())
    decryptor = cipher.decryptor()
    plaintext = decryptor.update(encrypted) + decryptor.finalize()

    print(f"  Decrypted tempkey size: {len(plaintext)} bytes")

    if len(plaintext) < 52:
        raise ValueError(
            f"Decrypted tempkey too short: {len(plaintext)} bytes (need at least 52)"
        )

    db_key = plaintext[:32]
    db_salt = plaintext[32:48]
    stored_hash = struct.unpack('<i', plaintext[48:52])[0]

    # Verify hash
    computed_hash = murmurhash3_x86_32(db_key + db_salt, seed=0xF7CA7FD2)

    print(f"  Stored hash:   {stored_hash}")
    print(f"  Computed hash: {computed_hash}")

    if stored_hash == computed_hash:
        print("  Hash verification: PASSED")
    else:
        print(f"  Hash verification: FAILED (stored={stored_hash}, computed={computed_hash})")
        raise ValueError("Key verification failed — wrong password or corrupt file")

    return db_key, db_salt


def open_database(db_path: str, db_key: bytes, db_salt: bytes):
    """Open a Telegram SQLCipher database with proper PRAGMA settings."""
    try:
        import sqlcipher3
    except ImportError:
        print("ERROR: sqlcipher3 required. Install with: pip install sqlcipher3")
        sys.exit(1)

    hex_key = (db_key + db_salt).hex()
    pragma_key = f"x'{hex_key}'"

    conn = sqlcipher3.connect(db_path)

    # MUST set cipher_default_plaintext_header_size BEFORE the key
    conn.execute("PRAGMA cipher_default_plaintext_header_size = 32")

    # Set key in raw mode (key + salt = 48 bytes = 96 hex chars)
    conn.execute(f'PRAGMA key = "{pragma_key}"')

    # Post-key settings from Telegram source
    conn.execute("PRAGMA cipher_memory_security = OFF")

    # Verify connection works
    try:
        cursor = conn.execute("SELECT count(*) FROM sqlite_master")
        table_count = cursor.fetchone()[0]
        print(f"  Database opened successfully! {table_count} objects in schema.")
        return conn
    except Exception as e:
        conn.close()
        raise RuntimeError(f"Failed to decrypt database: {e}")


def analyze_schema(conn) -> Dict[str, Any]:
    """Analyze database schema."""
    schema = {}
    cursor = conn.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index') ORDER BY type, name")
    for name, obj_type in cursor.fetchall():
        if obj_type == 'table':
            try:
                q = '"' + name.replace('"', '""') + '"'
                info = conn.execute(f'PRAGMA table_info({q})').fetchall()
                count = conn.execute(f'SELECT COUNT(*) FROM {q}').fetchone()[0]
                schema[name] = {
                    'columns': [(col[1], col[2]) for col in info],
                    'row_count': count
                }
            except Exception as e:
                schema[name] = {'error': str(e)}
    return schema


def extract_all_data(conn, schema: Dict, output_dir: Path, account_id: str):
    """Extract all data from database tables."""
    account_dir = output_dir / f"account-{account_id}"
    account_dir.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    summary = {}

    for table_name, info in sorted(schema.items()):
        if 'error' in info:
            continue

        row_count = info['row_count']
        if row_count == 0:
            continue

        try:
            q = '"' + table_name.replace('"', '""') + '"'
            cursor = conn.execute(f'SELECT * FROM {q}')
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()

            # Convert to serializable format
            serializable_rows = []
            for row in rows:
                row_dict = {}
                for col_name, value in zip(columns, row):
                    if isinstance(value, bytes):
                        # Try UTF-8 decode for text, fall back to hex
                        try:
                            decoded = value.decode('utf-8')
                            if all(c.isprintable() or c in '\n\r\t' for c in decoded):
                                row_dict[col_name] = decoded
                            else:
                                row_dict[col_name] = f"<binary:{len(value)}bytes>"
                        except (UnicodeDecodeError, ValueError):
                            row_dict[col_name] = f"<binary:{len(value)}bytes>"
                    else:
                        row_dict[col_name] = value
                serializable_rows.append(row_dict)

            # Save table data
            table_file = account_dir / f"{table_name}.json"
            with open(table_file, 'w', encoding='utf-8') as f:
                json.dump(serializable_rows, f, indent=2, ensure_ascii=False, default=str)

            total_rows += len(rows)
            summary[table_name] = len(rows)
            print(f"    {table_name}: {len(rows)} rows")

        except Exception as e:
            print(f"    {table_name}: ERROR - {e}")

    return total_rows, summary


def try_decode_postbox_messages(conn, output_dir: Path, account_id: str):
    """Try to decode Telegram Postbox message format."""
    account_dir = output_dir / f"account-{account_id}"
    account_dir.mkdir(parents=True, exist_ok=True)

    messages = []

    # Telegram Postbox uses tables like t1, t2, etc. with blob data
    # Try to find message-like tables
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]

    for table in tables:
        try:
            info = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            col_names = [col[1] for col in info]
            col_types = [col[2] for col in info]

            # Look for tables with blob columns (likely message data)
            has_blob = any('blob' in t.lower() for t in col_types if t)
            has_key = any('key' in c.lower() for c in col_names)

            if has_blob and has_key:
                sample = conn.execute(f"SELECT * FROM '{table}' LIMIT 5").fetchall()
                if sample:
                    for row in sample:
                        for i, val in enumerate(row):
                            if isinstance(val, bytes) and len(val) > 10:
                                # Try to extract readable text from blob
                                text_fragments = extract_text_from_blob(val)
                                if text_fragments:
                                    messages.append({
                                        'table': table,
                                        'column': col_names[i] if i < len(col_names) else f'col_{i}',
                                        'blob_size': len(val),
                                        'text_fragments': text_fragments
                                    })
        except Exception:
            continue

    if messages:
        msg_file = account_dir / "decoded_messages.json"
        with open(msg_file, 'w', encoding='utf-8') as f:
            json.dump(messages, f, indent=2, ensure_ascii=False)
        print(f"    Found {len(messages)} message fragments")

    return messages


def extract_text_from_blob(data: bytes) -> List[str]:
    """Extract readable text fragments from binary blob data."""
    fragments = []

    # Try UTF-8
    try:
        text = data.decode('utf-8', errors='ignore')
        # Find sequences of printable text
        current = []
        for char in text:
            if char.isprintable() or char in '\n\r\t':
                current.append(char)
            else:
                if len(current) >= 4:
                    fragment = ''.join(current).strip()
                    if fragment:
                        fragments.append(fragment)
                current = []
        if len(current) >= 4:
            fragment = ''.join(current).strip()
            if fragment:
                fragments.append(fragment)
    except Exception:
        pass

    return fragments


def main():
    parser = argparse.ArgumentParser(
        description='Decrypt Telegram App Store databases using .tempkeyEncrypted'
    )
    parser.add_argument('backup_dir', help='Backup directory with account-* folders')
    parser.add_argument('--password', default='no-matter-key',
                        help='Local passcode (default: "no-matter-key" = no passcode)')
    parser.add_argument('--output', help='Output directory (default: backup_dir/decrypted_data)')
    parser.add_argument('--tempkey', help='Path to .tempkeyEncrypted file')
    parser.add_argument('--account', help='Only decrypt this account-{id} directory')
    parser.add_argument('--redact', action='store_true',
                        help='Mask sensitive values (account IDs, keys, paths) in console output')

    args = parser.parse_args()
    redact.set_enabled(args.redact)
    backup_dir = Path(args.backup_dir)

    if not backup_dir.exists():
        print(f"ERROR: Directory not found: {redact.path(backup_dir)}")
        sys.exit(1)

    # Find .tempkeyEncrypted
    tempkey_path = args.tempkey
    if not tempkey_path:
        candidates = [
            backup_dir / '.tempkeyEncrypted',
            backup_dir / 'appstore' / '.tempkeyEncrypted',
        ]
        for c in candidates:
            if c.exists():
                tempkey_path = str(c)
                break

    if not tempkey_path or not Path(tempkey_path).exists():
        print("ERROR: .tempkeyEncrypted not found")
        print("  Searched:", [redact.path(str(c)) for c in candidates])
        sys.exit(1)

    print(f"Using tempkey: {redact.path(tempkey_path)}")
    print(f"Password: {'<custom>' if args.password != 'no-matter-key' else 'no-matter-key (default)'}")

    # Step 1: Decrypt the tempkey
    print("\n--- Step 1: Decrypt .tempkeyEncrypted ---")
    db_key, db_salt = decrypt_tempkey(tempkey_path, args.password)
    print(f"  dbKey:  {redact.hexkey(db_key.hex()[:8] + '...' + db_key.hex()[-4:])}")
    print(f"  dbSalt: {redact.hexkey(db_salt.hex()[:8] + '...' + db_salt.hex()[-4:])}")

    # Step 2: Find and decrypt databases
    print("\n--- Step 2: Find databases ---")
    output_dir = Path(args.output) if args.output else backup_dir / "decrypted_data"
    output_dir.mkdir(parents=True, exist_ok=True)

    account_dirs = sorted(backup_dir.glob("account-*"))
    if args.account:
        wanted = args.account if args.account.startswith("account-") else f"account-{args.account}"
        account_dirs = [d for d in account_dirs if d.name == wanted]
        if not account_dirs:
            print(f"ERROR: --account {args.account!r} matched no directory in {redact.path(backup_dir)}")
            sys.exit(1)
    if not account_dirs:
        print("No account-* directories found")
        sys.exit(1)

    total_messages = 0
    results = {}

    for account_dir in account_dirs:
        account_id = account_dir.name.replace('account-', '')
        db_path = account_dir / "postbox" / "db" / "db_sqlite"

        if not db_path.exists():
            print(f"\n  account-{redact.account(account_id)}: No database found")
            continue

        print(f"\n--- Account: {redact.account(account_id)} ---")
        print(f"  Database: {redact.path(db_path)} ({db_path.stat().st_size / 1024 / 1024:.1f} MB)")

        try:
            conn = open_database(str(db_path), db_key, db_salt)

            print("\n  Schema:")
            schema = analyze_schema(conn)

            print("\n  Extracting data:")
            row_count, summary = extract_all_data(conn, schema, output_dir, account_id)

            print("\n  Decoding messages:")
            messages = try_decode_postbox_messages(conn, output_dir, account_id)

            total_messages += row_count
            results[account_id] = {
                'status': 'success',
                'tables': summary,
                'total_rows': row_count,
                'message_fragments': len(messages)
            }

            conn.close()

        except Exception as e:
            print(f"  FAILED: {e}")
            results[account_id] = {
                'status': 'failed',
                'error': str(e)
            }

    # Save summary
    summary_file = output_dir / "decrypt_summary.json"
    with open(summary_file, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'backup_dir': str(backup_dir),
            'total_rows': total_messages,
            'accounts': results
        }, f, indent=2)

    print(f"\n{'='*60}")
    print(f"DONE: {total_messages} total rows extracted")
    print(f"Output: {redact.path(output_dir)}")
    print(f"Summary: {redact.path(summary_file)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
tg_decrypt.py — Telegram Database Decryptor and Analyzer
Decrypts Telegram SQLite databases and extracts message data
"""

import os
import sys
import json
import sqlite3
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Any
import hashlib
import struct

try:
    import sqlcipher3
    HAS_SQLCIPHER = True
except ImportError:
    print("WARNING: sqlcipher3 not available. Install with: pip install sqlcipher3")
    HAS_SQLCIPHER = False

class TelegramDecryptor:
    def __init__(self, backup_dir: str, keys_file: str = None):
        self.backup_dir = Path(backup_dir)
        self.keys_file = keys_file or self.backup_dir / "telegram_keys.json"
        self.keys = {}
        self.load_keys()
        
    def load_keys(self):
        """Load encryption keys from JSON file"""
        if not Path(self.keys_file).exists():
            print(f"ERROR: Keys file not found: {self.keys_file}")
            print("Run ./extract-keys.sh first to extract encryption keys")
            sys.exit(1)
            
        with open(self.keys_file) as f:
            data = json.load(f)
            self.keys = data.get('telegram_keys', {})
            self.device_keys = data.get('device_keys', [])
            self.tempkey_files = data.get('tempkey_files', [])
            
        print(f"Loaded {len(self.keys)} Telegram keys")
        print(f"Loaded {len(self.device_keys)} device keys")
        print(f"Loaded {len(self.tempkey_files)} tempkey files")
        
        # Process tempkey files
        for tempkey_info in self.tempkey_files:
            hex_data = tempkey_info.get('hex_data', '')
            if hex_data:
                # Add raw hex key
                self.keys[f"tempkey_raw"] = hex_data
                # Add as bytes key
                self.keys[f"tempkey_bytes"] = bytes.fromhex(hex_data).hex()
                # Try different derivations
                self.keys[f"tempkey_sha256"] = hashlib.sha256(bytes.fromhex(hex_data)).hexdigest()
                print(f"Processed tempkey: {tempkey_info.get('file', 'unknown')}")
        
    def find_databases(self) -> List[Path]:
        """Find all SQLite databases in backup directory"""
        databases = []
        
        # Look for postbox databases
        for account_dir in self.backup_dir.glob("account-*"):
            postbox_db = account_dir / "postbox" / "db" / "db_sqlite"
            if postbox_db.exists():
                databases.append(postbox_db)
                
        # Look for other databases
        for db_file in self.backup_dir.rglob("*.sqlite"):
            databases.append(db_file)
            
        for db_file in self.backup_dir.rglob("*.db"):
            databases.append(db_file)
            
        return sorted(set(databases))
        
    def test_key_on_database(self, db_path: Path, key: str) -> Optional[sqlite3.Connection]:
        """Test if a key can decrypt a database"""
        if not HAS_SQLCIPHER:
            # Try standard SQLite first (some DBs might not be encrypted)
            try:
                conn = sqlite3.connect(str(db_path))
                conn.execute("SELECT count(*) FROM sqlite_master")
                return conn
            except:
                return None
                
        try:
            # Try with sqlcipher
            conn = sqlcipher3.connect(str(db_path))
            
            # Try different key formats
            key_variants = [
                key,
                f"x'{key}'",
                f"'{key}'",
                key.encode('utf-8').hex(),
            ]
            
            for key_variant in key_variants:
                try:
                    conn.execute(f"PRAGMA key = {key_variant}")
                    # Test if we can read the schema
                    conn.execute("SELECT count(*) FROM sqlite_master")
                    print(f"✓ Successfully decrypted {db_path.name} with key variant: {key_variant[:20]}...")
                    return conn
                except sqlcipher3.DatabaseError:
                    # Wrong key (or not-yet-decryptable schema) — try the next variant.
                    continue

            conn.close()
            return None

        except sqlcipher3.DatabaseError as e:
            print(f"Failed to connect to {db_path}: {e}")
            return None
        except Exception as e:
            # Non-key error (I/O, library, etc.) — surface it distinctly instead of
            # masquerading as a wrong-key failure.
            print(f"ERROR opening {db_path} (non-key error): {type(e).__name__}: {e}")
            raise
            
    def decrypt_database(self, db_path: Path) -> Optional[sqlite3.Connection]:
        """Try to decrypt a database with available keys"""
        print(f"\nAttempting to decrypt: {db_path}")
        
        # Extract account ID from path for targeted key search
        account_id = None
        if "account-" in str(db_path):
            account_id = str(db_path).split("account-")[1].split("/")[0]
            
        # Try device keys first (most likely to work)
        for device_key_info in self.device_keys:
            if account_id and device_key_info.get('account') == account_id:
                key = device_key_info.get('key_value', '')
                if key:
                    conn = self.test_key_on_database(db_path, key)
                    if conn:
                        return conn
                        
        # Try all telegram keys
        for key_name, key_value in self.keys.items():
            if key_value:
                conn = self.test_key_on_database(db_path, key_value)
                if conn:
                    return conn
                    
        print(f"✗ Failed to decrypt {db_path.name}")
        return None
        
    def analyze_database_schema(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        """Analyze database schema and extract metadata"""
        schema = {}
        
        try:
            # Get all tables
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            schema['tables'] = tables
            
            # Analyze each table
            table_info = {}
            for table in tables:
                try:
                    q = '"' + table.replace('"', '""') + '"'
                    cursor = conn.execute(f'PRAGMA table_info({q})')
                    columns = cursor.fetchall()
                    cursor = conn.execute(f'SELECT COUNT(*) FROM {q}')
                    row_count = cursor.fetchone()[0]
                    
                    table_info[table] = {
                        'columns': columns,
                        'row_count': row_count
                    }
                except Exception as e:
                    table_info[table] = {'error': str(e)}
                    
            schema['table_info'] = table_info
            
        except Exception as e:
            schema['error'] = str(e)
            
        return schema
        
    def extract_messages(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        """Extract messages from decrypted database"""
        messages = []
        
        # Common Telegram table names for messages
        message_tables = ['messages', 'message', 'msg', 'chat_messages', 'postbox']
        
        for table in message_tables:
            try:
                cursor = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%{table}%'")
                actual_tables = [row[0] for row in cursor.fetchall()]
                
                for actual_table in actual_tables:
                    try:
                        cursor = conn.execute(f"SELECT * FROM {actual_table} LIMIT 100")
                        columns = [desc[0] for desc in cursor.description]
                        
                        for row in cursor.fetchall():
                            message_data = dict(zip(columns, row))
                            message_data['_table'] = actual_table
                            messages.append(message_data)
                            
                    except Exception as e:
                        print(f"Error reading table {actual_table}: {e}")
                        
            except Exception as e:
                continue
                
        return messages
        
    def export_data(self, output_dir: str = None) -> str:
        """Export all decrypted data to JSON files"""
        output_dir = Path(output_dir or self.backup_dir / "decrypted_data")
        output_dir.mkdir(exist_ok=True)
        
        databases = self.find_databases()
        export_data = {
            'export_time': json.dumps(None, default=str),
            'databases': {}
        }
        
        for db_path in databases:
            print(f"\nProcessing: {db_path}")
            conn = self.decrypt_database(db_path)
            
            if conn:
                db_name = db_path.name
                schema = self.analyze_database_schema(conn)
                messages = self.extract_messages(conn)
                
                export_data['databases'][db_name] = {
                    'path': str(db_path),
                    'schema': schema,
                    'messages': messages,
                    'decrypted': True
                }
                
                # Save individual database export
                db_export_file = output_dir / f"{db_name}_export.json"
                with open(db_export_file, 'w') as f:
                    json.dump({
                        'schema': schema,
                        'messages': messages
                    }, f, indent=2, default=str)
                    
                print(f"✓ Exported {len(messages)} messages from {db_name}")
                conn.close()
            else:
                export_data['databases'][db_path.name] = {
                    'path': str(db_path),
                    'decrypted': False,
                    'error': 'Failed to decrypt'
                }
                
        # Save master export file
        master_export = output_dir / "telegram_export.json"
        with open(master_export, 'w') as f:
            json.dump(export_data, f, indent=2, default=str)
            
        print(f"\n✓ Export complete: {output_dir}")
        print(f"✓ Master export: {master_export}")
        
        return str(output_dir)

def main():
    parser = argparse.ArgumentParser(description='Decrypt and analyze Telegram databases')
    parser.add_argument('backup_dir', help='Backup directory containing Telegram data')
    parser.add_argument('--keys', help='Path to keys JSON file')
    parser.add_argument('--output', help='Output directory for decrypted data')
    parser.add_argument('--analyze-only', action='store_true', help='Only analyze, don\'t export')
    
    args = parser.parse_args()
    
    if not Path(args.backup_dir).exists():
        print(f"ERROR: Backup directory not found: {args.backup_dir}")
        sys.exit(1)
        
    decryptor = TelegramDecryptor(args.backup_dir, args.keys)
    
    if args.analyze_only:
        databases = decryptor.find_databases()
        for db_path in databases:
            conn = decryptor.decrypt_database(db_path)
            if conn:
                schema = decryptor.analyze_database_schema(conn)
                print(f"\nSchema for {db_path.name}:")
                print(json.dumps(schema, indent=2, default=str))
                conn.close()
    else:
        output_dir = decryptor.export_data(args.output)
        print(f"\n🎉 All data exported to: {output_dir}")

if __name__ == "__main__":
    main()
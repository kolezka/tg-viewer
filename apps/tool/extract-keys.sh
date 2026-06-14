#!/usr/bin/env bash
# extract-keys.sh — Extract Telegram encryption keys from macOS Keychain
# Usage: ./extract-keys.sh [backup_dir]
# Extracts encryption keys needed to decrypt Telegram databases

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
BACKUP_DIR="${1:-./tg_$(date +"%Y-%m-%d_%H-%M-%S")}"
KEYS_FILE="$BACKUP_DIR/telegram_keys.json"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

log()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()     { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()    { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Functions ─────────────────────────────────────────────────────────────────
extract_telegram_keys() {
    log "Searching for Telegram keys in keychain..." >&2

    # Common Telegram keychain entries
    local key_patterns=(
        "Telegram"
        "ru.keepcoder.Telegram"
        "6N38VWS5BX.ru.keepcoder.Telegram"
        "postbox"
        "local_storage"
        "temp_key"
        "tempKeyEncrypted"
        "masterKey"
    )

    # Collect (key, value) pairs as NUL-delimited records so passwords/keys
    # containing newlines, tabs, quotes or backslashes survive intact, then
    # serialize the whole object via json.dumps (no manual string building).
    local pairs=""
    for pattern in "${key_patterns[@]}"; do
        log "  Searching for pattern: $pattern" >&2

        # Search generic passwords
        while IFS= read -r line; do
            if [[ -n "$line" ]]; then
                local account service password=""
                account=$(echo "$line" | cut -d: -f1)
                service=$(echo "$line" | cut -d: -f2)
                if password=$(security find-generic-password -a "$account" -s "$service" -w 2>/dev/null); then
                    pairs+="${account}_${service}"$'\0'"${password}"$'\0'
                    ok "    Found key: $account @ $service" >&2
                fi
            fi
        done < <(security dump-keychain 2>/dev/null | grep -A1 -B1 "$pattern" | grep -E "acct|svce" | paste - - | sed 's/.*"\(.*\)".*/\1/' | tr ' ' ':' 2>/dev/null || true)

        # Search internet passwords
        while IFS= read -r line; do
            if [[ -n "$line" ]]; then
                local account server password=""
                account=$(echo "$line" | cut -d: -f1)
                server=$(echo "$line" | cut -d: -f2)
                if password=$(security find-internet-password -a "$account" -s "$server" -w 2>/dev/null); then
                    pairs+="${account}_${server}"$'\0'"${password}"$'\0'
                    ok "    Found internet key: $account @ $server" >&2
                fi
            fi
        done < <(security dump-keychain 2>/dev/null | grep -A1 -B1 "$pattern" | grep -E "acct|srvr" | paste - - | sed 's/.*"\(.*\)".*/\1/' | tr ' ' ':' 2>/dev/null || true)
    done

    printf '%s' "$pairs" | python3 -c '
import json, sys
data = sys.stdin.buffer.read().split(b"\0")
# Records come in (key, value) pairs; drop trailing empty from final NUL.
if data and data[-1] == b"":
    data.pop()
obj = {}
for i in range(0, len(data) - 1, 2):
    k = data[i].decode("utf-8", "replace")
    v = data[i + 1].decode("utf-8", "replace")
    obj[k] = v
print(json.dumps(obj))
'
}

extract_tempkey() {
    log "Extracting tempkey file..." >&2
    
    local tempkey_info=""
    
    # Look for .tempkeyEncrypted file in backup. Scope nullglob so the
    # `*/.tempkeyEncrypted` glob expands to nothing (rather than leaving the
    # literal pattern) when no per-account dir matches; restore prior state after.
    local _nullglob_was_set=0
    shopt -q nullglob && _nullglob_was_set=1
    shopt -s nullglob
    local tempkey_files=(
        "$BACKUP_DIR/.tempkeyEncrypted"
        "$BACKUP_DIR"/*/.tempkeyEncrypted
        "$HOME/Library/Group Containers/6N38VWS5BX.ru.keepcoder.Telegram/appstore/.tempkeyEncrypted"
    )
    [[ "$_nullglob_was_set" == 0 ]] && shopt -u nullglob
    
    for tempkey_file in "${tempkey_files[@]}"; do
        if [[ -f "$tempkey_file" ]]; then
            log "  Found tempkey file: $tempkey_file" >&2
            local hex_key
            hex_key=$(hexdump -v -e '1/1 "%02x"' "$tempkey_file")
            # NUL-delimited (file, hex_data) record — see json.dumps below.
            tempkey_info+="${tempkey_file}"$'\0'"${hex_key}"$'\0'

            # Copy tempkey to backup if not already there
            if [[ "$tempkey_file" != "$BACKUP_DIR"* ]]; then
                cp -p "$tempkey_file" "$BACKUP_DIR/" 2>/dev/null || true
            fi
        fi
    done

    printf '%s' "$tempkey_info" | python3 -c '
import json, sys
data = sys.stdin.buffer.read().split(b"\0")
if data and data[-1] == b"":
    data.pop()
out = []
for i in range(0, len(data) - 1, 2):
    out.append({
        "file": data[i].decode("utf-8", "replace"),
        "hex_data": data[i + 1].decode("utf-8", "replace"),
    })
print(json.dumps(out))
'
}

extract_device_keys() {
    log "Extracting device-specific keys..." >&2

    # Collect (account, key_name, key_value) triples as NUL-delimited records,
    # then serialize as a JSON array via json.dumps so quotes/backslashes/
    # newlines in key values can't corrupt the output.
    local triples=""

    # Check for postbox encryption keys by account
    if [[ -d "$BACKUP_DIR" ]]; then
        for account_dir in "$BACKUP_DIR"/account-*; do
            if [[ -d "$account_dir" ]]; then
                local account_id
                account_id=$(basename "$account_dir" | sed 's/account-//')
                log "  Searching keys for account: $account_id" >&2

                # Try various key naming patterns
                local key_names=(
                    "postbox_key_$account_id"
                    "storage_key_$account_id"
                    "db_key_$account_id"
                    "$account_id"
                )

                local key_name key_value
                for key_name in "${key_names[@]}"; do
                    if key_value=$(security find-generic-password -s "Telegram" -a "$key_name" -w 2>/dev/null); then
                        triples+="${account_id}"$'\0'"${key_name}"$'\0'"${key_value}"$'\0'
                        ok "    Found account key: $key_name" >&2
                    fi
                done
            fi
        done
    fi

    printf '%s' "$triples" | python3 -c '
import json, sys
data = sys.stdin.buffer.read().split(b"\0")
if data and data[-1] == b"":
    data.pop()
out = []
for i in range(0, len(data) - 2, 3):
    out.append({
        "account": data[i].decode("utf-8", "replace"),
        "key_name": data[i + 1].decode("utf-8", "replace"),
        "key_value": data[i + 2].decode("utf-8", "replace"),
    })
print(json.dumps(out))
'
}

# ── Main ──────────────────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

log "Starting Telegram keychain extraction..."
log "Output directory: $BACKUP_DIR"

# Extract all Telegram-related keys
all_keys=$(extract_telegram_keys)
device_keys=$(extract_device_keys)
tempkey_data=$(extract_tempkey)

# Create comprehensive keys file. Assemble the whole object in Python: the
# three *_keys fragments are already valid JSON (from json.dumps in the helpers),
# and the scalar fields (hostname, timestamp) are routed through json.dumps too
# so quotes/backslashes/newlines in a hostname can't corrupt the file.
EXTRACTION_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
DEVICE_NAME="$(hostname)" \
TELEGRAM_KEYS="$all_keys" \
DEVICE_KEYS="$device_keys" \
TEMPKEY_FILES="$tempkey_data" \
python3 -c '
import json, os
obj = {
    "extraction_time": os.environ["EXTRACTION_TIME"],
    "device": os.environ["DEVICE_NAME"],
    "telegram_keys": json.loads(os.environ["TELEGRAM_KEYS"]),
    "device_keys": json.loads(os.environ["DEVICE_KEYS"]),
    "tempkey_files": json.loads(os.environ["TEMPKEY_FILES"]),
    "notes": "Keys extracted from macOS keychain for Telegram database decryption",
}
print(json.dumps(obj, indent=4))
' > "$KEYS_FILE"

ok "Keys extracted to: $KEYS_FILE"

# Show summary
key_count=$(echo "$all_keys" | jq 'length' 2>/dev/null || echo "0")
device_key_count=$(echo "$device_keys" | jq 'length' 2>/dev/null || echo "0")
tempkey_count=$(echo "$tempkey_data" | jq 'length' 2>/dev/null || echo "0")

log "Summary:"
log "  Telegram keys found: $key_count"
log "  Device keys found: $device_key_count"
log "  Tempkey files found: $tempkey_count"

if [[ "$key_count" -eq 0 && "$device_key_count" -eq 0 && "$tempkey_count" -eq 0 ]]; then
    warn "No keys found! This could mean:"
    warn "  1. Telegram is not installed or never run"
    warn "  2. Keys are stored with different naming patterns"
    warn "  3. Additional permissions needed for keychain access"
    echo ""
    log "Try running with sudo or check Security & Privacy settings"
fi

echo ""
ok "Key extraction complete!"
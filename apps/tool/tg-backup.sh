#!/usr/bin/env bash
# tg-backup.sh — Telegram macOS message DB & cache backup
# Usage: ./tg-backup.sh [destination_dir]
# Default destination: ./  (current directory)
#
# Copies the encrypted postbox databases (messages), cached files, and
# account metadata for all Telegram accounts found on this Mac.
# The script copies all accounts found on this machine.

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
# Detect --batch mode and strip from positional args
BATCH_MODE=false
_args=()
for arg in "$@"; do
  if [[ "$arg" == "--batch" ]]; then
    BATCH_MODE=true
  else
    _args+=("$arg")
  fi
done
DEST="${_args[0]:-.}"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
BACKUP_DIR="$DEST/tg_$TIMESTAMP"

# Use --progress only when interactive (not batch mode).
#
# --ignore-errors  : keep going past transient I/O errors (e.g. EINTR from
#                    Telegram actively writing files during the rsync).
# --exclude '*_partial.*'  : Telegram writes per-download metadata files
#                    (e.g. *.meta, *.partial) that mutate while the app is
#                    running and aren't useful to back up.
#
# IMPORTANT: do NOT exclude '*-wal' / '*-shm' / '*-journal'. Telegram's
# SQLCipher DB runs in WAL mode and keeps recent writes (new messages,
# secret-chat tombstones, media references) in the -wal file until a
# checkpoint merges them into the main DB. Skipping the WAL silently
# drops the last few minutes / megabytes of activity — exactly the
# rows we want when backing up immediately after a deletion.
RSYNC_OPTS=(
  -a
  --ignore-errors
  --exclude='*_partial.*'
)
if [[ "$BATCH_MODE" == false ]]; then
  RSYNC_OPTS+=(--progress)
fi

# App Store version (sandboxed) — the one installed on this machine
TG_APPSTORE="$HOME/Library/Group Containers/6N38VWS5BX.ru.keepcoder.Telegram"

# Telegram stores data in different locations depending on source
TG_PATHS=(
  "$TG_APPSTORE"
  "$HOME/Library/Application Support/Telegram Desktop"
  "$HOME/Library/Application Support/Telegram"
)

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

log()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()     { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()    { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# Redact tg_<timestamp> segments in paths when TG_REDACT=1
_redact_path() {
    if [[ "${TG_REDACT:-0}" == "1" ]]; then
        echo "$1" | sed -E 's|tg_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}|<backup>|g'
    else
        echo "$1"
    fi
}

# Mask a personal name when TG_REDACT=1 — keeps first letter of each
# space-separated word and replaces the rest with `*`. Mirrors
# redact.name() in redact.py so terminal output stays consistent.
_redact_name() {
    if [[ "${TG_REDACT:-0}" != "1" ]]; then
        echo "$1"
        return
    fi
    local raw="$1"
    local trimmed="${raw#"${raw%%[! ]*}"}"
    trimmed="${trimmed%"${trimmed##*[! ]}"}"
    if [[ -z "$trimmed" || "$trimmed" == "unknown" || "$trimmed" == "None" ]]; then
        echo "***"
        return
    fi
    local out="" word
    for word in $trimmed; do
        if (( ${#word} <= 1 )); then
            out+="* "
        else
            out+="${word:0:1}$(printf '*%.0s' $(seq 1 $((${#word} - 1)))) "
        fi
    done
    echo "${out% }"
}

# ── Sanity checks ─────────────────────────────────────────────────────────────
if pgrep -x "Telegram" > /dev/null 2>&1 && [[ "$BATCH_MODE" == false ]]; then
  warn "Telegram is currently running."
  warn "The postbox DB may be locked / mid-write."
  read -rp "  It's safer to quit Telegram first. Continue anyway? [y/N] " confirm
  [[ "$confirm" == "y" || "$confirm" == "Y" ]] || die "Aborted. Please quit Telegram and re-run."
fi

# ── Find source ───────────────────────────────────────────────────────────────
SOURCE=""
for path in "${TG_PATHS[@]}"; do
  if [[ -d "$path" ]]; then
    SOURCE="$path"
    break
  fi
done

[[ -n "$SOURCE" ]] || die "No Telegram data directory found. Paths checked:\n$(printf '  %s\n' "${TG_PATHS[@]}")"

log "Found Telegram data at: $SOURCE"

# ── Discover accounts ────────────────────────────────────────────────────────
# App Store layout: <SOURCE>/appstore/account-<id>/postbox/db/db_sqlite
APPSTORE_DIR="$SOURCE/appstore"
if [[ ! -d "$APPSTORE_DIR" ]]; then
  die "Expected appstore directory not found at: $APPSTORE_DIR"
fi

ACCOUNT_DIRS=()
for d in "$APPSTORE_DIR"/account-*; do
  [[ -d "$d" ]] && ACCOUNT_DIRS+=("$d")
done

if [[ ${#ACCOUNT_DIRS[@]} -eq 0 ]]; then
  die "No account directories found in $APPSTORE_DIR"
fi

# Read peerName from accounts-shared-data for nicer labels
# Store as simple list since we can't use associative arrays in sh
ACCOUNT_INFO=""
SHARED_DATA="$APPSTORE_DIR/accounts-shared-data"
if [[ -f "$SHARED_DATA" ]] && command -v python3 &>/dev/null; then
  ACCOUNT_INFO=$(python3 -c "
import json, sys
with open('$SHARED_DATA') as f:
    data = json.load(f)
for acc in data.get('accounts', []):
    unsigned = acc['id'] % (2**64)
    print(f'{unsigned}|{acc.get(\"peerName\", \"unknown\")}')
")
fi

# Helper function to get account name
get_account_name() {
  local dir_id="$1"
  if [[ -n "$ACCOUNT_INFO" ]]; then
    echo "$ACCOUNT_INFO" | grep "^${dir_id}|" | cut -d'|' -f2 || echo "unknown"
  else
    echo "unknown"
  fi
}

log "Found ${#ACCOUNT_DIRS[@]} account(s):"
for d in "${ACCOUNT_DIRS[@]}"; do
  dir_name=$(basename "$d")
  dir_id="${dir_name#account-}"
  label=$(get_account_name "$dir_id")
  safe_label=$(_redact_name "$label")
  db_path="$d/postbox/db/db_sqlite"
  safe_name="$dir_name"
  [[ "${TG_REDACT:-0}" == "1" ]] && safe_name="account-***"
  if [[ -f "$db_path" ]]; then
    db_size=$(du -sh "$db_path" 2>/dev/null | cut -f1)
    log "  $safe_name ($safe_label) — postbox DB: $db_size"
  else
    log "  $safe_name ($safe_label) — no postbox DB found"
  fi
done
echo ""

# ── Create backup ─────────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

# 1. Copy accounts-shared-data (account metadata / mapping)
log "Copying account metadata..."
for f in "$APPSTORE_DIR"/accounts-shared-data "$APPSTORE_DIR"/.tempkeyEncrypted; do
  [[ -f "$f" ]] && cp -p "$f" "$BACKUP_DIR/"
done

# 2. Copy accounts-metadata directory (login tokens, guard DB)
if [[ -d "$APPSTORE_DIR/accounts-metadata" ]]; then
  log "Copying accounts-metadata..."
  mkdir -p "$BACKUP_DIR/accounts-metadata"
  rsync "${RSYNC_OPTS[@]}" "$APPSTORE_DIR/accounts-metadata/" "$BACKUP_DIR/accounts-metadata/" || rc=$?
  if [[ ${rc:-0} -ne 0 && ${rc:-0} -ne 23 && ${rc:-0} -ne 24 ]]; then
    die "rsync failed with exit code ${rc:-0}"
  fi
fi

# 2b. Copy logs/ — Telegram's MTProto debug logs. Forensic gold: each
# Update.updateNewEncryptedMessage line records file_id + accessHash +
# size + dcId + keyFingerprint even for messages that were later
# deleted from t7. Without these we can't even prove a deleted secret
# message existed (the bytes themselves are truncated as "..." in the
# log, but the metadata is intact). Telegram rotates logs at ~1 MB,
# so they vanish quickly — back them up while you can.
if [[ -d "$APPSTORE_DIR/logs" ]]; then
  log "Copying Telegram MTProto logs..."
  mkdir -p "$BACKUP_DIR/logs"
  rsync "${RSYNC_OPTS[@]}" "$APPSTORE_DIR/logs/" "$BACKUP_DIR/logs/" || rc=$?
  if [[ ${rc:-0} -ne 0 && ${rc:-0} -ne 23 && ${rc:-0} -ne 24 ]]; then
    die "rsync failed with exit code ${rc:-0}"
  fi
  ok "  logs copied ($(du -sh "$BACKUP_DIR/logs" 2>/dev/null | cut -f1))"
fi

# 3. For each account: copy postbox DB (messages) + cached files
for d in "${ACCOUNT_DIRS[@]}"; do
  dir_name=$(basename "$d")
  dir_id="${dir_name#account-}"
  label=$(get_account_name "$dir_id")
  safe_label=$(_redact_name "$label")
  echo ""
  safe_name="$dir_name"
  [[ "${TG_REDACT:-0}" == "1" ]] && safe_name="account-***"
  log "━━━ Backing up $safe_name ($safe_label) ━━━"

  acct_backup="$BACKUP_DIR/$dir_name"
  mkdir -p "$acct_backup"

  # 3a. Postbox DB — the encrypted message database + WAL/SHM
  if [[ -d "$d/postbox/db" ]]; then
    log "  Copying postbox database (messages)..."
    mkdir -p "$acct_backup/postbox/db"
    rsync "${RSYNC_OPTS[@]}" "$d/postbox/db/" "$acct_backup/postbox/db/" || rc=$?
    # rsync exit 24 = vanishing source files (normal for active app), 23 = partial transfer
    if [[ ${rc:-0} -ne 0 && ${rc:-0} -ne 23 && ${rc:-0} -ne 24 ]]; then
      die "  rsync failed with exit code ${rc:-0}"
    fi
    ok "  postbox DB copied ($(du -sh "$acct_backup/postbox/db" 2>/dev/null | cut -f1))"
  else
    warn "  No postbox/db found — skipping"
  fi

  # 3b. Postbox media references
  if [[ -d "$d/postbox/media" ]]; then
    log "  Copying postbox media index..."
    mkdir -p "$acct_backup/postbox/media"
    rsync "${RSYNC_OPTS[@]}" "$d/postbox/media/" "$acct_backup/postbox/media/" || rc=$?
    if [[ ${rc:-0} -ne 0 && ${rc:-0} -ne 23 && ${rc:-0} -ne 24 ]]; then
      die "  rsync failed with exit code ${rc:-0}"
    fi
    ok "  postbox media copied ($(du -sh "$acct_backup/postbox/media" 2>/dev/null | cut -f1))"
  fi

  # 3c. Cached data (peer-specific cached blobs, e.g. profile info)
  if [[ -d "$d/cached" ]]; then
    log "  Copying cached data..."
    mkdir -p "$acct_backup/cached"
    rsync "${RSYNC_OPTS[@]}" "$d/cached/" "$acct_backup/cached/" || rc=$?
    if [[ ${rc:-0} -ne 0 && ${rc:-0} -ne 23 && ${rc:-0} -ne 24 ]]; then
      die "  rsync failed with exit code ${rc:-0}"
    fi
    ok "  cached data copied"
  fi

  # 3d. Network stats & notification key
  for extra in network-stats notificationsKey; do
    if [[ -e "$d/$extra" ]]; then
      cp -p "$d/$extra" "$acct_backup/"
    fi
  done
done

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok "Backup complete!"
ok "Location: $(_redact_path "$BACKUP_DIR")"
ok "Total size: $(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)"
echo ""
log "Next steps:"
log "  • Run './extract-keys.sh $(_redact_path "$BACKUP_DIR")' to extract encryption keys"
log "  • Run './tg_decrypt.py $(_redact_path "$BACKUP_DIR")' to decrypt the databases"
log "  • Run './tg-viewer webui $(_redact_path "$BACKUP_DIR")' to browse messages in the web interface"
log ""
log "Or simply use: './tg-viewer full' to run the complete workflow automatically"
echo ""

# ── Optional: create a compressed archive ─────────────────────────────────────
if [[ "$BATCH_MODE" == false ]]; then
  read -rp "Also create a .tar.gz archive? [y/N] " compress
  if [[ "$compress" == "y" || "$compress" == "Y" ]]; then
    ARCHIVE="$DEST/tg_$TIMESTAMP.tar.gz"
    log "Compressing..."
    tar -czf "$ARCHIVE" -C "$DEST" "tg_$TIMESTAMP"
    ok "Archive: $(_redact_path "$ARCHIVE") ($(du -sh "$ARCHIVE" | cut -f1))"
  fi
fi

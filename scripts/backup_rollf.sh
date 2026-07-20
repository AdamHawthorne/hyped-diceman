#!/bin/bash

set -euo pipefail
trap 'log "Backup failed"' ERR

PROJECT_DIR="/opt/bots/rollf-bot"
DB_FILE="$PROJECT_DIR/bot.db"
BACKUP_DIR="$PROJECT_DIR/backups"
LOG_FILE="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"

DATE=$(date +"%Y%m%d_%H%M")

TMP_BACKUP="$BACKUP_DIR/database_${DATE}.db"
FINAL_BACKUP="${TMP_BACKUP}.gz"

log() {
    echo "[$(date '+%F %T')] $1" >> "$LOG_FILE"
}

log "Starting backup"

# Flush WAL into main database and truncate WAL file
sqlite3 "$DB_FILE" "PRAGMA wal_checkpoint(TRUNCATE);" >> "$LOG_FILE" 2>&1

# Create backup
cp "$DB_FILE" "$TMP_BACKUP"

# Compress backup
gzip "$TMP_BACKUP"

# Verify archive integrity
gzip -t "$FINAL_BACKUP"

log "Backup created: $(basename "$FINAL_BACKUP")"

# Delete backups older than 180 days
find "$BACKUP_DIR" \
    -type f \
    -name "database_*.db.gz" \
    -mtime +180 \
    -delete

log "Rotation complete"
echo "--------------------------------------" >> "$LOG_FILE"

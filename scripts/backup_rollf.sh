#!/bin/bash

set -euo pipefail
trap 'log "Backup failed"' ERR

PROJECT_DIR="/opt/bots/rollf-bot"
DB_FILE="$PROJECT_DIR/bot.db"
BACKUP_DIR="$PROJECT_DIR/backups"
LOG_FILE="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"

DATE=$(date +"%Y%m%d_%H%M")
START_TIME=$(date +%s)

TMP_BACKUP="$BACKUP_DIR/database_${DATE}.db"
FINAL_BACKUP="${TMP_BACKUP}.gz"

log() {
    echo "[$(date '+%F %T')] $1" >> "$LOG_FILE"
}

log "Starting backup"

# Flush WAL into main database and truncate WAL file
if sqlite3 "$DB_FILE" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null; then
    log "WAL checkpoint completed"
else
    log "WAL checkpoint failed"
    exit 1
fi

# Create backup
cp "$DB_FILE" "$TMP_BACKUP"

# Compress backup
gzip "$TMP_BACKUP"

# Verify archive integrity
gzip -t "$FINAL_BACKUP"

SIZE=$(du -h "$FINAL_BACKUP" | cut -f1)

log "Backup created: $(basename "$FINAL_BACKUP") ($SIZE)"

# Delete backups older than 180 days
DELETED=$(find "$BACKUP_DIR" \
    -type f \
    -name "database_*.db.gz" \
    -mtime +180 | wc -l)

find "$BACKUP_DIR" \
    -type f \
    -name "database_*.db.gz" \
    -mtime +180 \
    -delete

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

log "Duration: ${DURATION}s"
log "Rotation removed ${DELETED} old backup(s)"
echo "-------------------------------------------------------------------------" >> "$LOG_FILE"

#!/bin/bash
# monitor_db_activity.sh - Monitor database writes per second and show last 5 entries

DB_PATH=${1:-/mnt/user/appdata/nas-scanner/scan_data/nas_catalog.db}

if [ ! -f "$DB_PATH" ]; then
    echo "Database not found at $DB_PATH"
    exit 1
fi

prev_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM files" 2>/dev/null || echo 0)

while true; do
    sleep 5
    current_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM files" 2>/dev/null || echo 0)
    diff=$((current_count - prev_count))
    rate=$(awk -v d="$diff" 'BEGIN{printf "%.2f", d/5}')
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] Writes/sec: $rate"
    echo "Last 5 entries:"
    sqlite3 -header "$DB_PATH" "SELECT datetime(scan_time,'unixepoch') AS time, path FROM files ORDER BY scan_time DESC LIMIT 5" 2>/dev/null || echo "Unable to read database"
    echo
    prev_count=$current_count
done

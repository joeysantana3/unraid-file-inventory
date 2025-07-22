#!/bin/bash
# monitor_smart_scan.sh - Monitor smart scanner progress

SMART_DIR="/mnt/user/appdata/nas-scanner"
DB_PATH="$SMART_DIR/smart_catalog.db"
SMART_IMAGE="nas-scanner-smart:latest"

if [ -n "$1" ]; then
    DB_PATH="$1"
fi

while true; do
    clear
    echo "=== SMART SCANNER MONITOR ==="
    echo "Time: $(date)"
    echo

    echo "Controller container(s):"
    docker ps --filter "ancestor=$SMART_IMAGE" --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}" || true
    echo

    echo "Worker containers:"
    docker ps --filter "name=smart-scan-" --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"
    echo

    workers=$(docker ps --filter "name=smart-scan-" -q)
    if [ -n "$workers" ]; then
        docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}" $workers
    else
        echo "No smart-scan worker containers running"
    fi
    echo

    if [ -f "$DB_PATH" ]; then
        echo "Database: $DB_PATH"
        sqlite3 "$DB_PATH" "SELECT COUNT(*), printf('%.2f', SUM(size)/1024.0/1024.0/1024.0) FROM files" 2>/dev/null |
            awk '{printf "Files: %s\nTotal GB: %s\n", $1, $2}'
    else
        echo "Database not found at $DB_PATH"
    fi

    echo
    echo "Refresh in 5 seconds (Ctrl+C to exit)"
    sleep 5
done

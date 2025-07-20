#!/bin/bash
# Quick diagnostic script to check current scanner state

echo "=== IMMEDIATE SCANNER DIAGNOSTICS ==="
echo "Time: $(date)"
echo ""

echo "1. CONTAINER STATUS:"
docker ps --filter "name=nas-hp-" --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"
echo ""

echo "2. CONTAINER CPU/MEMORY (last 10 seconds):"
docker stats --no-stream $(docker ps --filter "name=nas-hp-" -q) 2>/dev/null
echo ""

echo "3. I/O WAIT CHECK:"
iostat -c 1 2 | tail -1 | awk '{print "Current iowait: " $4 "%"}'
echo ""

echo "4. PROCESSES IN I/O WAIT (D state):"
ps aux | awk '$8 ~ /D/ { print "PID " $2 ": " $11 " (state: " $8 ")" }' | head -5
if [ $(ps aux | awk '$8 ~ /D/' | wc -l) -eq 0 ]; then
    echo "No processes in I/O wait state"
fi
echo ""

echo "5. DATABASE STATUS:"
DB_PATH="/mnt/user/appdata/nas-scanner/scan_data/nas_catalog.db"
if [ -f "$DB_PATH" ]; then
    echo "Database size: $(du -h "$DB_PATH" | cut -f1)"
    echo "Last modified: $(stat -c %y "$DB_PATH")"
    
    echo "Testing database access..."
    if timeout 5 sqlite3 "$DB_PATH" "SELECT COUNT(*) as files FROM files" 2>/dev/null; then
        echo "✅ Database accessible"
    else
        echo "❌ Database not accessible or timeout"
    fi
else
    echo "❌ Database file not found"
fi
echo ""

echo "6. RECENT CONTAINER LOGS (last 3 lines each):"
docker ps --filter "name=nas-hp-" --format "{{.Names}}" | while read container; do
    echo "--- $container ---"
    docker logs --tail 3 "$container" 2>&1 | sed 's/^/  /'
done
echo ""

echo "7. HOST SCANNER PROCESSES:"
ps aux | grep nas_scanner | grep -v grep | awk '{print "PID " $2 ": CPU=" $3 "% MEM=" $4 "% " $11}'
echo ""

echo "8. MOUNT POINT QUICK CHECK:"
for mount in /mnt/user/Movies /mnt/user/Music /mnt/user/Photos; do
    if [ -d "$mount" ]; then
        echo -n "$(basename $mount): "
        if timeout 3 ls "$mount" >/dev/null 2>&1; then
            echo "✅ accessible"
        else
            echo "❌ slow/inaccessible"
        fi
    fi
done
echo ""

echo "9. DISK USAGE ON SCAN DATA:"
df -h /mnt/user/appdata/nas-scanner/scan_data/ 2>/dev/null || echo "Scan data directory not accessible"
echo ""

echo "=== RECOMMENDATIONS ==="
echo "- If iowait > 20%: I/O bottleneck likely"
echo "- If processes in D state: Check storage health"
echo "- If database not accessible: Check disk space/permissions"
echo "- If containers show 'exited': Check logs for errors"
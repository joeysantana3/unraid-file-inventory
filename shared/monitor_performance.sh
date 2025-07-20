#!/bin/bash
# Real-time performance monitoring for the scan

while true; do
    clear
    echo "=== NAS SCANNER PERFORMANCE MONITOR ==="
    echo "Time: $(date)"
    echo ""
    
    # System stats
    echo "SYSTEM LOAD:"
    uptime
    echo ""
    
    # Container stats
    echo "CONTAINER PERFORMANCE:"
    docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}" \
        $(docker ps --filter "name=nas-hp-" -q) 2>/dev/null || echo "No containers running"
    
    # I/O stats
    echo -e "\nDISK I/O:"
    iostat -x 1 2 | tail -n +7 | head -20
    
    # Database operations per second
    echo -e "\nDATABASE OPERATIONS:"
    docker run --rm -v /mnt/user/appdata/nas-scanner/scan_data:/data nas-scanner-hp:latest python -c "
import sqlite3, time
conn = sqlite3.connect('/data/nas_catalog.db')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM files')
count1 = cursor.fetchone()[0]
time.sleep(1)
cursor.execute('SELECT COUNT(*) FROM files')
count2 = cursor.fetchone()[0]
print(f'Files/second: {count2-count1:,}')
" 2>/dev/null || echo "Database not accessible"
    
    sleep 5
done 
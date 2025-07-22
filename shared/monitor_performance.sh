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
    containers=$(docker ps --filter "name=nas-hp-" --filter "name=smart-scan-" -q)
    if [ -n "$containers" ]; then
        docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}" $containers
    else
        echo "No scanning containers running"
    fi
    
    # I/O stats
    echo -e "\nDISK I/O:"
    iostat -x 1 2 | tail -n +7 | head -20
    
    # Database operations per second
    echo -e "\nDATABASE OPERATIONS:"
    DB_PATH="/mnt/user/appdata/nas-scanner/scan_data/nas_catalog.db"
    [ -f "$DB_PATH" ] || DB_PATH="/mnt/user/appdata/nas-scanner-smart/smart_catalog.db"
    if [ -f "$DB_PATH" ]; then
        docker run --rm -e DB=$(basename "$DB_PATH") -v "$(dirname "$DB_PATH"):/data" nas-scanner-hp:latest python - <<'EOF'
import os, sqlite3, time
db=os.path.join('/data', os.environ.get('DB'))
conn=sqlite3.connect(db)
cur=conn.cursor()
cur.execute('SELECT COUNT(*) FROM files')
c1=cur.fetchone()[0]
time.sleep(1)
cur.execute('SELECT COUNT(*) FROM files')
c2=cur.fetchone()[0]
print(f'Files/second: {c2-c1:,}')
EOF
        
    else
        echo "Database not accessible"
    fi
    
    sleep 5
done 

#!/bin/bash
# Enhanced monitoring for NAS scanner with detailed I/O and process analysis

SCAN_DATA_DIR="/mnt/user/appdata/nas-scanner/scan_data"
DB_PATH="$SCAN_DATA_DIR/nas_catalog.db"
[ -f "$DB_PATH" ] || DB_PATH="/mnt/user/appdata/nas-scanner-smart/smart_catalog.db"

get_container_pids() {
    docker ps --filter "name=nas-hp-" --filter "name=smart-scan-" --format "{{.Names}}" | while read container; do
        echo "Container: $container"
        docker exec "$container" ps aux 2>/dev/null | grep nas_scanner || echo "  No scanner processes found"
        echo ""
    done
}

check_io_wait() {
    echo "=== I/O WAIT ANALYSIS ==="
    # Show processes in D state (uninterruptible sleep - usually I/O wait)
    echo "Processes in I/O wait (D state):"
    ps aux | awk '$8 ~ /D/ { print $2, $8, $11 }' | head -10
    echo ""
    
    # Show I/O wait percentage
    echo "System I/O wait:"
    iostat -c 1 2 | tail -1 | awk '{print "iowait: " $4 "%"}'
    echo ""
}

check_database_locks() {
    echo "=== DATABASE STATUS ==="
    
    # Check if database file exists and is accessible
    if [ ! -f "$DB_PATH" ]; then
        echo "❌ Database file not found at $DB_PATH"
        return
    fi
    
    echo "Database file size: $(du -h "$DB_PATH" | cut -f1)"
    echo "Database file age: $(stat -c %y "$DB_PATH")"
    
    # Check for database locks using lsof
    echo "Processes with database file open:"
    lsof "$DB_PATH" 2>/dev/null || echo "No processes have database open (or lsof not available)"
    echo ""
    
    # Try to query database with timeout
    echo "Database query test:"
    timeout 5 sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM files" 2>/dev/null || echo "❌ Database query failed or timed out"
    echo ""
}

check_mount_accessibility() {
    echo "=== MOUNT POINT STATUS ==="
    for mount in /mnt/user/*; do
        if [[ -d "$mount" ]]; then
            mount_name=$(basename "$mount")
            case "$mount_name" in
                appdata|system|domains) continue ;;
            esac
            
            echo -n "$mount_name: "
            if timeout 5 ls "$mount" >/dev/null 2>&1; then
                file_count=$(timeout 10 find "$mount" -type f 2>/dev/null | head -1000 | wc -l)
                echo "✅ accessible ($file_count+ files visible)"
            else
                echo "❌ not accessible or very slow"
            fi
        fi
    done
    echo ""
}

show_process_details() {
    echo "=== PROCESS ANALYSIS ==="
    echo "Scanner processes on host:"
    ps aux | grep nas_scanner | grep -v grep | while read line; do
        pid=$(echo "$line" | awk '{print $2}')
        state=$(cat /proc/$pid/stat 2>/dev/null | cut -d' ' -f3 || echo "?")
        wchan=$(cat /proc/$pid/wchan 2>/dev/null || echo "unknown")
        echo "PID $pid: state=$state wchan=$wchan"
        echo "  $line"
    done
    echo ""
    
    echo "Top I/O processes:"
    iotop -a -o -d 1 -n 1 2>/dev/null | head -15 || echo "iotop not available"
    echo ""
}

show_container_logs() {
    echo "=== RECENT CONTAINER LOGS ==="
    docker ps --filter "name=nas-hp-" --filter "name=smart-scan-" --format "{{.Names}}" | head -3 | while read container; do
        echo "Last 5 lines from $container:"
        docker logs --tail 5 "$container" 2>/dev/null | sed 's/^/  /'
        echo ""
    done
}

main_monitor() {
    while true; do
        clear
        echo "=== DETAILED NAS SCANNER DIAGNOSTICS ==="
        echo "Time: $(date)"
        echo "Uptime: $(uptime)"
        echo ""
        
        # Quick container status
        echo "=== CONTAINER OVERVIEW ==="
        containers=$(docker ps --filter "name=nas-hp-" --filter "name=smart-scan-" -q)
        if [ -n "$containers" ]; then
            docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}\t{{.BlockIO}}" $containers
        else
            echo "No containers running"
        fi
        echo ""
        
        # Detailed analysis
        check_io_wait
        check_database_locks
        check_mount_accessibility
        show_process_details
        show_container_logs
        
        # Database progress with timestamp
        echo "=== DATABASE PROGRESS ==="
        timeout 10 sqlite3 "$DB_PATH" "
        SELECT 
            datetime('now') as timestamp,
            COUNT(*) as total_files,
            printf('%.2f', SUM(size)/1024.0/1024/1024) as total_gb,
            COUNT(DISTINCT mount_point) as mount_points
        FROM files" 2>/dev/null || echo "❌ Cannot read database"
        
        echo ""
        echo "Press Ctrl+C to exit. Refreshing in 10 seconds..."
        sleep 10
    done
}

# Allow running specific checks
case "$1" in
    io)
        check_io_wait
        ;;
    db)
        check_database_locks
        ;;
    mounts)
        check_mount_accessibility
        ;;
    processes)
        show_process_details
        ;;
    logs)
        show_container_logs
        ;;
    *)
        main_monitor
        ;;
esac

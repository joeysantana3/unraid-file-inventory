#!/bin/bash
# Quick diagnostic script to check current scanner state

echo "=== IMMEDIATE SCANNER DIAGNOSTICS ==="
echo "Time: $(date)"
echo ""

echo "1. I/O WAIT CHECK:"
if [ -f /proc/stat ]; then
    # Primary method using /proc/stat (most reliable)
    awk '/cpu / {u=$2+$4; t=$2+$3+$4+$5+$6+$7+$8; if(t>0) printf "Current iowait: %.2f%%\n", ($6/t)*100; else print "Current iowait: 0.00%"}' /proc/stat
elif command -v iostat >/dev/null 2>&1; then
    # Fallback to iostat
    iostat -c 1 1 2>/dev/null | awk '/^ / {if(NF>=4) print "Current iowait: " $4 "%"; else print "Current iowait: Unable to parse"}'
else
    echo "Current iowait: Unable to determine"
fi
echo ""

echo "2. PROCESSES IN I/O WAIT (D state):"
# Try multiple approaches to catch D state processes
d_processes=""

# Method 1: ps aux with flexible column detection
d_procs1=$(ps aux 2>/dev/null | awk 'NR>1 && ($8 ~ /D/ || $7 ~ /D/ || $9 ~ /D/) {print "PID " $2 ": " $11 " (state: " $(NF-5) ")"}' | head -3)
if [ -n "$d_procs1" ]; then
    d_processes="$d_procs1"
fi

# Method 2: ps -eo format (more reliable for state column)
d_procs2=$(ps -eo pid,stat,comm 2>/dev/null | awk '$2 ~ /D/ {print "PID " $1 ": " $3 " (state: " $2 ")"}' | head -3)
if [ -n "$d_procs2" ]; then
    if [ -n "$d_processes" ]; then
        d_processes="$d_processes"$'\n'"$d_procs2"
    else
        d_processes="$d_procs2"
    fi
fi

# Method 3: Check /proc directly for D state processes  
d_procs3=$(find /proc -maxdepth 1 -name '[0-9]*' -type d 2>/dev/null | while read proc_dir; do
    if [ -r "$proc_dir/stat" ] 2>/dev/null; then
        state=$(awk '{print $3}' "$proc_dir/stat" 2>/dev/null)
        if [ "$state" = "D" ]; then
            pid=$(basename "$proc_dir")
            comm=$(awk '{print $2}' "$proc_dir/stat" 2>/dev/null | tr -d '()')
            echo "PID $pid: $comm (state: D)"
        fi
    fi
done | head -2)

if [ -n "$d_procs3" ]; then
    if [ -n "$d_processes" ]; then
        d_processes="$d_processes"$'\n'"$d_procs3"
    else
        d_processes="$d_procs3"
    fi
fi

if [ -n "$d_processes" ]; then
    echo "$d_processes" | head -5 | sort -u
else
    echo "No processes currently in I/O wait state (D state processes are often transient)"
fi
echo ""

echo "3. DATABASE STATUS:"
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


echo "4. HOST SCANNER PROCESSES:"
ps aux | grep nas_scanner | grep -v grep | awk '{print "PID " $2 ": CPU=" $3 "% MEM=" $4 "% " $11}'
echo ""

echo "=== DISK SATURATION CHECK ==="
echo 'Disk stats (3 samples, 1 second interval):'
iostat -dx 1 3

echo "=== END OF DISK SATURATION CHECK ==="

echo "5. DISK USAGE ON SCAN DATA:"
df -h /mnt/user/appdata/nas-scanner/scan_data/ 2>/dev/null || echo "Scan data directory not accessible"
echo ""

echo "=== RECOMMENDATIONS ==="
echo "- If iowait > 20%: I/O bottleneck likely"
echo "- If processes in D state: Check storage health"
echo "- If database not accessible: Check disk space/permissions"
echo "- If containers show 'exited': Check logs for errors"
#!/bin/bash
# Extreme Parallel Scanner - Uses all 192 threads

# Configuration
SCAN_DATA_DIR="/mnt/user/appdata/nas-scanner/scan_data"
DB_PATH="$SCAN_DATA_DIR/nas_catalog.db"

# With 192 threads, we can be very aggressive
MAX_CONTAINERS=8           # Run 8 containers simultaneously
WORKERS_PER_CONTAINER=24   # 24 workers per container (simplified architecture)
# Total: 8 * 24 = 192 threads (matches system capacity)

print_status() {
    echo -e "\033[0;34m[$(date '+%H:%M:%S')]\033[0m $1"
}

# Function to start a high-performance container
start_hp_container() {
    local mount_path=$1
    local mount_name=$2
    # Sanitize container name to avoid invalid characters
    local safe_name=$(echo "$mount_name" | tr -c 'a-zA-Z0-9_.-' '_')
    local container_name="nas-hp-${safe_name}"
    
    print_status "Starting high-performance scan of $mount_name"
    
    docker run -d \
        --name "$container_name" \
        --rm \
        -v "$mount_path:$mount_path:ro" \
        -v "$SCAN_DATA_DIR:/data" \
        --cpus="24" \
        --memory="24g" \
        --ulimit nofile=65536:65536 \
        --ulimit nproc=32768:32768 \
        nas-scanner-hp:latest \
        python nas_scanner_hp.py "$mount_path" "$mount_name" \
            --db /data/nas_catalog.db \
            --workers $WORKERS_PER_CONTAINER
    
    echo "$container_name"
}

# Main execution
main() {
    echo "======================================"
    echo "EXTREME PARALLEL NAS SCANNER"
    echo "System: 192 threads, 128GB RAM"
    echo "======================================"
    
    # Set system optimizations
    print_status "Setting system optimizations..."
    
    # Increase file descriptors (if root)
    ulimit -n 65536 2>/dev/null || true
    
    # Parse mode
    case "$1" in
        full)
            print_status "FULL SCAN MODE - Using all 192 threads"
            ;;
        *)
            echo "Usage: $0 full"
            echo "Note: Test mode removed - simplified scanner always does full scan"
            exit 1
            ;;
    esac
    
    # Auto-detect mounts
    declare -a MOUNTS
    
    # Scan user shares
    for share in /mnt/user/*; do
        if [[ -d "$share" ]]; then
            share_name=$(basename "$share")
            case "$share_name" in
                appdata|system|domains)
                    continue
                    ;;
                *)
                    MOUNTS+=("$share:$share_name")
                    ;;
            esac
        fi
    done
    
    print_status "Found ${#MOUNTS[@]} mounts to scan"
    read -p "Proceed with scanning? (y to continue, anything else to quit): " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Aborted by user."
        exit 0
    fi
    # Start time
    start_time=$(date +%s)
    
    # Launch all containers
    containers=()
    for mount_info in "${MOUNTS[@]}"; do
        mount_path="${mount_info%%:*}"
        mount_name="${mount_info##*:}"
        
        # Wait if we're at max containers
        while [ $(docker ps --filter "name=nas-hp-" -q | wc -l) -ge $MAX_CONTAINERS ]; do
            sleep 2
        done
        
        container=$(start_hp_container "$mount_path" "$mount_name")
        containers+=("$container")
        
        # Small delay to stagger starts
        sleep 0.5
    done
    
    # Monitor progress
    print_status "All containers launched. Monitoring progress..."
    
    # Wait for all to complete
    for container in "${containers[@]}"; do
        docker wait "$container" > /dev/null 2>&1 &
    done
    
    # Show live stats while waiting
    while [ $(docker ps --filter "name=nas-hp-" -q | wc -l) -gt 0 ]; do
        clear
        echo "======================================"
        echo "EXTREME PARALLEL SCAN IN PROGRESS"
        echo "======================================"
        
        # Show container stats
        docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}" \
            $(docker ps --filter "name=nas-hp-" -q)
        
        # Show database stats
        echo -e "\nDatabase Progress:"
        docker run --rm -v "$SCAN_DATA_DIR:/data" nas-scanner-hp:latest python -c "
import sqlite3
conn = sqlite3.connect('/data/nas_catalog.db')
cursor = conn.cursor()
cursor.execute('''
    SELECT mount_point, COUNT(*) as files_scanned, SUM(size)/1024**3 as gb 
    FROM files 
    GROUP BY mount_point 
    ORDER BY SUM(size) DESC
''')
total_files = 0
total_gb = 0
for mount, files, gb in cursor.fetchall():
    print(f'{mount:20} {files:>10,} files  {gb:>8.1f} GB')
    total_files += files
    total_gb += gb
print('-' * 50)
print(f'{\"TOTAL\":20} {total_files:>10,} files  {total_gb:>8.1f} GB')
"
        
        sleep 5
    done
    
    # Calculate total time
    end_time=$(date +%s)
    duration=$((end_time - start_time))
    
    print_status "Scan completed in $((duration/60)) minutes"
    
    # Generate final report
    print_status "Generating final report..."
    docker run --rm -v "$SCAN_DATA_DIR:/data" nas-scanner-hp:latest python -c "
import sqlite3
conn = sqlite3.connect('/data/nas_catalog.db')
cursor = conn.cursor()

print('\n=== FINAL SCAN REPORT ===\n')

# Total stats
cursor.execute('SELECT COUNT(*), SUM(size) FROM files')
total_files, total_size = cursor.fetchone()
print(f'Total files: {total_files:,}')
print(f'Total size: {total_size/1024**4:.2f} TB')
print(f'Scan duration: $duration seconds')
print(f'Average speed: {total_files/$duration:.0f} files/second')
print(f'Average throughput: {total_size/1024**2/$duration:.0f} MB/second')

# Create indexes now
print('\nCreating indexes for fast queries...')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_checksum ON files(checksum)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_size ON files(size)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_extension ON files(extension)')
conn.commit()
print('Indexes created.')
"
}

# Run
main "$@" 
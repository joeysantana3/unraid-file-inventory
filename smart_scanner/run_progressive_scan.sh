#!/bin/bash
# Progressive Scanner - Start Immediately, No Waiting!

# Configuration
SMART_SCAN_DIR="/mnt/user/appdata/nas-scanner"
PROGRESSIVE_DB_PATH="$SMART_SCAN_DIR/progressive_catalog.db"
WORKER_IMAGE_NAME="nas-scanner-hp:latest"
PROGRESSIVE_IMAGE_NAME="nas-scanner-progressive:latest"

# Print functions
print_status() {
    echo "[INFO] $1"
}

print_success() {
    echo "[SUCCESS] $1"
}

print_error() {
    echo "[ERROR] $1" >&2
}

setup_progressive_scanner() {
    print_status "Setting up Progressive Scanner environment..."
    
    # Create directory structure
    mkdir -p "$SMART_SCAN_DIR"
    cd "$SMART_SCAN_DIR"
    
    # Get the script directory (where this script is located)
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_ROOT="$(dirname "$SCRIPT_DIR")"
    
    print_status "Copying files from $SCRIPT_DIR and $REPO_ROOT"
    
    # Ensure source files exist before copying
    if [ ! -f "$SCRIPT_DIR/progressive_scanner.py" ]; then
        print_error "progressive_scanner.py not found at $SCRIPT_DIR/progressive_scanner.py"
        exit 1
    fi
    
    if [ ! -f "$REPO_ROOT/mono_scanner/nas_scanner_hp.py" ]; then
        print_error "nas_scanner_hp.py not found at $REPO_ROOT/mono_scanner/nas_scanner_hp.py"
        exit 1
    fi
    
    cp "$SCRIPT_DIR/progressive_scanner.py" .
    cp "$REPO_ROOT/mono_scanner/nas_scanner_hp.py" .
    
    # Create Dockerfile for progressive scanner
    cat > Dockerfile.progressive << 'EOF'
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    procps \
    docker.io \
    coreutils \
    findutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY progressive_scanner.py ./
COPY nas_scanner_hp.py ./

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["python", "progressive_scanner.py"]
EOF
    
    # Build progressive scanner image
    print_status "Building progressive scanner image..."
    docker build -f Dockerfile.progressive -t "$PROGRESSIVE_IMAGE_NAME" --no-cache .
    
    # Ensure worker Docker image exists
    if ! docker image inspect "$WORKER_IMAGE_NAME" >/dev/null 2>&1; then
        print_status "Building worker image automatically..."
        
        # Try to build the worker image automatically
        if [ -f "$REPO_ROOT/mono_scanner/Dockerfile" ]; then
            cd "$REPO_ROOT/mono_scanner"
            if docker build -t "$WORKER_IMAGE_NAME" .; then
                print_success "Worker image built successfully"
                cd "$SMART_SCAN_DIR"
            else
                print_error "Failed to build worker image"
                exit 1
            fi
        else
            print_error "Dockerfile not found at $REPO_ROOT/mono_scanner/Dockerfile"
            exit 1
        fi
    fi
    
    print_success "Progressive Scanner setup complete"
}

show_usage() {
    echo "Usage: $0 <command> [args...]"
    echo ""
    echo "🚀 PROGRESSIVE SCANNER - Start Immediately, No Waiting!"
    echo ""
    echo "Commands:"
    echo "  scan <mount_path> <mount_name> [options]  - Start progressive scanning (NO ANALYSIS DELAY)"
    echo "  status                                    - Show scan status"
    echo "  cleanup                                   - Clean up containers"
    echo ""
    echo "Progressive scan options:"
    echo "  --max-containers N    Maximum concurrent containers (default: 6)"
    echo "  --image IMAGE         Docker image to use (default: nas-scanner-hp:latest)"
    echo ""
    echo "Examples:"
    echo "  # Scan Archive folder - starts immediately!"
    echo "  $0 scan /mnt/user/Archive Archive"
    echo ""
    echo "  # Reduce concurrency for resource-constrained systems"
    echo "  $0 scan /mnt/user/Archive Archive --max-containers 4"
    echo ""
    echo "Key benefits:"
    echo "  ✅ No upfront analysis - starts scanning in seconds"
    echo "  ✅ Progressive optimization - gets smarter as it runs"
    echo "  ✅ Conservative resources - 6 containers × 8 CPUs × 8GB RAM"
    echo "  ✅ Works great for terabyte-scale directories"
}

start_progressive_scan() {
    local mount_path="$1"
    local mount_name="$2"
    shift 2
    
    if [ -z "$mount_path" ] || [ -z "$mount_name" ]; then
        print_error "Mount path and name are required"
        show_usage
        exit 1
    fi
    
    if [ ! -d "$mount_path" ]; then
        print_error "Mount path does not exist: $mount_path"
        exit 1
    fi
    
    print_status "🚀 Starting PROGRESSIVE scan of $mount_path"
    print_status "Mount name: $mount_name"
    print_status "Database: $PROGRESSIVE_DB_PATH"
    print_status "Strategy: Start immediately, optimize progressively"
    
    # Check for existing database and show resume info
    if [ -f "$PROGRESSIVE_DB_PATH" ]; then
        print_status "📊 Existing database found - resume capability enabled"
        
        # Try to show existing data count
        if command -v sqlite3 >/dev/null 2>&1; then
            EXISTING_FILES=$(sqlite3 "$PROGRESSIVE_DB_PATH" "SELECT COUNT(*) FROM files WHERE mount_point='$mount_name';" 2>/dev/null || echo "0")
            SCANNED_DIRS=$(sqlite3 "$PROGRESSIVE_DB_PATH" "SELECT COUNT(*) FROM scanned_dirs WHERE mount_point='$mount_name';" 2>/dev/null || echo "0")
            
            if [ "$EXISTING_FILES" -gt 0 ]; then
                print_status "🔄 RESUME MODE: $EXISTING_FILES existing files, $SCANNED_DIRS completed chunks"
                print_status "   Will skip already scanned directories"
            else
                print_status "📊 Database exists but no previous data for this mount"
            fi
        fi
    else
        print_status "🆕 New database will be created"
    fi
    
    print_status "Additional args: $*"
    
    # Store absolute path before any directory changes might affect it
    DATABASE_HOST_DIR="/mnt/user/appdata/nas-scanner"
    
    # Run the progressive scanner in a container with FIXED mount path
    docker run --rm -it \
        -v "$mount_path:$mount_path:ro" \
        -v "$DATABASE_HOST_DIR:/data" \
        -v /var/run/docker.sock:/var/run/docker.sock \
        --network host \
        -e "HOST_DB_DIR=$DATABASE_HOST_DIR" \
        "$PROGRESSIVE_IMAGE_NAME" \
        python progressive_scanner.py "$mount_path" "$mount_name" \
        --db "/data/progressive_catalog.db" \
        --image "$WORKER_IMAGE_NAME" \
        --host-db-dir "$DATABASE_HOST_DIR" \
        "$@"
}

show_status() {
    print_status "Progressive Scanner Status"
    echo "Database: $PROGRESSIVE_DB_PATH"
    if [ -f "$PROGRESSIVE_DB_PATH" ]; then
        echo "Database exists: $(ls -lh "$PROGRESSIVE_DB_PATH" | awk '{print $5}')"
    else
        echo "Database not found"
    fi
    
    # Show running containers
    echo "Running progressive containers:"
    docker ps --filter "name=progressive-scan" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    
    # Show database progress if available
    if [ -f "$PROGRESSIVE_DB_PATH" ]; then
        echo ""
        echo "Current scan progress:"
        # FIXED: Use absolute path for status check too
        docker run --rm -v "/mnt/user/appdata/nas-scanner:/data" "$WORKER_IMAGE_NAME" python -c "
import sqlite3, sys
try:
    conn = sqlite3.connect('/data/progressive_catalog.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM files')
    total = cursor.fetchone()[0]
    cursor.execute('SELECT mount_point, COUNT(*) FROM files GROUP BY mount_point')
    mounts = cursor.fetchall()
    print(f'Total files scanned: {total:,}')
    for mount, count in mounts:
        print(f'  {mount}: {count:,} files')
    conn.close()
except Exception as e:
    print(f'Could not read database: {e}')
" 2>/dev/null || echo "Database not accessible"
    fi
}

cleanup_containers() {
    print_status "Cleaning up progressive scanner containers..."
    docker ps -q --filter "name=progressive-scan" | xargs -r docker stop
    docker ps -q --filter "ancestor=$PROGRESSIVE_IMAGE_NAME" | xargs -r docker stop
    docker ps -q --filter "ancestor=$WORKER_IMAGE_NAME" | xargs -r docker stop
    print_success "Cleanup complete"
}

# Main script
case "$1" in
    scan)
        shift
        setup_progressive_scanner
        start_progressive_scan "$@"
        ;;
    status)
        show_status
        ;;
    cleanup)
        cleanup_containers
        ;;
    *)
        show_usage
        exit 1
        ;;
esac 
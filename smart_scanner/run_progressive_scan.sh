#!/bin/bash
# Progressive Scanner - Fixed Version with Improvements
# Fixes: Resource checking, better error handling, consistent paths

# Configuration
SMART_SCAN_DIR="/mnt/user/appdata/nas-scanner/unraid-file-inventory/smart_scanner"
PROGRESSIVE_DB_PATH="$SMART_SCAN_DIR/progressive_catalog.db"
WORKER_IMAGE_NAME="nas-scanner-hp:latest"
PROGRESSIVE_IMAGE_NAME="nas-scanner-progressive:latest"
LOG_FILE="$SMART_SCAN_DIR/progressive_scan.log"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print functions with colors
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1" | tee -a "$LOG_FILE"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1" | tee -a "$LOG_FILE"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2 | tee -a "$LOG_FILE"
}

check_system_resources() {
    print_status "Checking system resources..."
    
    # Check CPU count
    CPU_COUNT=$(nproc 2>/dev/null || echo "unknown")
    if [ "$CPU_COUNT" != "unknown" ]; then
        REQUESTED_CPUS=$((MAX_CONTAINERS * 8))
        if [ "$REQUESTED_CPUS" -gt "$CPU_COUNT" ]; then
            print_warning "Requesting $REQUESTED_CPUS CPUs but system has $CPU_COUNT"
            print_warning "Consider reducing --max-containers to $((CPU_COUNT / 8))"
        fi
    fi
    
    # Check available memory
    if [ -f /proc/meminfo ]; then
        AVAILABLE_KB=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
        if [ -n "$AVAILABLE_KB" ]; then
            AVAILABLE_GB=$((AVAILABLE_KB / 1024 / 1024))
            REQUESTED_GB=$((MAX_CONTAINERS * 8))
            if [ "$REQUESTED_GB" -gt "$((AVAILABLE_GB * 80 / 100))" ]; then
                print_warning "Requesting ${REQUESTED_GB}GB RAM but only ${AVAILABLE_GB}GB available"
                print_warning "Consider reducing --max-containers"
            fi
        fi
    fi
    
    # Check Docker daemon
    if ! docker info >/dev/null 2>&1; then
        print_error "Docker daemon is not running or not accessible"
        exit 1
    fi
    
    print_success "System resource check complete"
}

setup_progressive_scanner() {
    print_status "Setting up Progressive Scanner environment..."
    
    # Create directory structure
    mkdir -p "$SMART_SCAN_DIR"
    cd "$SMART_SCAN_DIR" || exit 1
    
    # Initialize log file
    touch "$LOG_FILE"
    echo "=== Progressive Scanner Setup Started at $(date) ===" >> "$LOG_FILE"
    
    # Get the script directory
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_ROOT="$(dirname "$SCRIPT_DIR")"
    
    print_status "Script directory: $SCRIPT_DIR"
    print_status "Repository root: $REPO_ROOT"
    
         # Validate source files exist
     if [ ! -f "$SCRIPT_DIR/progressive_scanner.py" ]; then
         # Try the fixed version if it exists
         if [ -f "$SCRIPT_DIR/progressive_scanner_fixed.py" ]; then
             print_status "Using fixed version of progressive_scanner.py"
             cp "$SCRIPT_DIR/progressive_scanner_fixed.py" progressive_scanner.py
         else
             print_error "progressive_scanner.py not found at $SCRIPT_DIR"
             exit 1
         fi
     else
         # Only copy if source and destination are different
         if [ "$SCRIPT_DIR/progressive_scanner.py" -ef "./progressive_scanner.py" ]; then
             print_status "progressive_scanner.py already in place"
         else
             cp "$SCRIPT_DIR/progressive_scanner.py" .
         fi
     fi
    
         if [ ! -f "$REPO_ROOT/mono_scanner/nas_scanner_hp.py" ]; then
         print_error "nas_scanner_hp.py not found at $REPO_ROOT/mono_scanner/nas_scanner_hp.py"
         print_error "Please ensure the mono_scanner directory exists with the scanner script"
         exit 1
     fi
     
     # Only copy if source and destination are different
     if [ "$REPO_ROOT/mono_scanner/nas_scanner_hp.py" -ef "./nas_scanner_hp.py" ]; then
         print_status "nas_scanner_hp.py already in place"
     else
         cp "$REPO_ROOT/mono_scanner/nas_scanner_hp.py" .
     fi
    
         # Create improved Dockerfile for progressive scanner
     cat > Dockerfile.progressive << 'EOF'
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    procps \
    docker.io \
    coreutils \
    findutils \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy application files
COPY progressive_scanner.py ./
COPY nas_scanner_hp.py ./

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Run as root for Docker-in-Docker access (container is isolated anyway)
CMD ["python", "progressive_scanner.py"]
EOF
    
    # Build progressive scanner image
    print_status "Building progressive scanner image..."
    if docker build -f Dockerfile.progressive -t "$PROGRESSIVE_IMAGE_NAME" --no-cache . >> "$LOG_FILE" 2>&1; then
        print_success "Progressive scanner image built successfully"
    else
        print_error "Failed to build progressive scanner image"
        print_error "Check $LOG_FILE for details"
        exit 1
    fi
    
    # Check/build worker image
    if ! docker image inspect "$WORKER_IMAGE_NAME" >/dev/null 2>&1; then
        print_status "Worker image not found, building automatically..."
        
        if [ -f "$REPO_ROOT/mono_scanner/Dockerfile" ]; then
            cd "$REPO_ROOT/mono_scanner" || exit 1
            if docker build -t "$WORKER_IMAGE_NAME" . >> "$LOG_FILE" 2>&1; then
                print_success "Worker image built successfully"
                cd "$SMART_SCAN_DIR" || exit 1
            else
                print_error "Failed to build worker image"
                print_error "Check $LOG_FILE for details"
                exit 1
            fi
        else
            print_error "Worker Dockerfile not found at $REPO_ROOT/mono_scanner/Dockerfile"
            exit 1
        fi
    else
        print_success "Worker image already exists"
    fi
    
    # Create database schema if needed
    if [ ! -f "$PROGRESSIVE_DB_PATH" ]; then
        print_status "Creating new database..."
        sqlite3 "$PROGRESSIVE_DB_PATH" << 'SQL'
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    size INTEGER,
    mtime REAL,
    checksum TEXT,
    mount_point TEXT,
    file_type TEXT,
    extension TEXT,
    scan_time REAL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS scan_stats (
    mount_point TEXT PRIMARY KEY,
    files_scanned INTEGER,
    bytes_scanned INTEGER,
    start_time REAL,
    end_time REAL
);

CREATE TABLE IF NOT EXISTS scanned_dirs (
    path TEXT PRIMARY KEY,
    mount_point TEXT,
    scan_time REAL
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_mount_point ON files(mount_point);
CREATE INDEX IF NOT EXISTS idx_scan_time ON files(scan_time);
CREATE INDEX IF NOT EXISTS idx_checksum ON files(checksum);
CREATE INDEX IF NOT EXISTS idx_size ON files(size);
CREATE INDEX IF NOT EXISTS idx_extension ON files(extension);
SQL
        print_success "Database schema created"
    fi
    
    print_success "Progressive Scanner setup complete"
}

show_usage() {
    echo "Usage: $0 <command> [args...]"
    echo ""
    echo "🚀 PROGRESSIVE SCANNER - Fixed Version"
    echo ""
    echo "Commands:"
    echo "  scan <mount_path> <mount_name> [options]  - Start progressive scanning"
    echo "  status                                    - Show scan status and statistics"
    echo "  cleanup                                   - Clean up all scan containers"
    echo "  logs                                      - Show recent scan logs"
    echo "  reset <mount_name>                        - Reset scan data for a mount"
    echo ""
    echo "Scan options:"
    echo "  --max-containers N    Maximum concurrent containers (default: 6)"
    echo "  --image IMAGE         Docker image to use (default: nas-scanner-hp:latest)"
    echo "  --resume              Explicitly resume previous scan"
    echo ""
    echo "Examples:"
    echo "  # Scan Archive folder"
    echo "  $0 scan /mnt/user/Archive Archive"
    echo ""
    echo "  # Scan with reduced concurrency"
    echo "  $0 scan /mnt/user/Archive Archive --max-containers 4"
    echo ""
    echo "  # Check scan progress"
    echo "  $0 status"
    echo ""
    echo "Features:"
    echo "  ✅ Automatic resume of interrupted scans"
    echo "  ✅ Resource usage validation"
    echo "  ✅ Improved error handling and logging"
    echo "  ✅ Database schema management"
    echo "  ✅ Container health monitoring"
}

start_progressive_scan() {
    local mount_path="$1"
    local mount_name="$2"
    shift 2
    
    # Validate inputs
    if [ -z "$mount_path" ] || [ -z "$mount_name" ]; then
        print_error "Mount path and name are required"
        show_usage
        exit 1
    fi
    
    # Sanitize mount name (remove special characters)
    mount_name=$(echo "$mount_name" | sed 's/[^a-zA-Z0-9_-]/_/g')
    
    if [ ! -d "$mount_path" ]; then
        print_error "Mount path does not exist: $mount_path"
        exit 1
    fi
    
    # Parse additional arguments
    local max_containers=6
    local image_name="$WORKER_IMAGE_NAME"
    
    while [ $# -gt 0 ]; do
        case "$1" in
            --max-containers)
                max_containers="$2"
                shift 2
                ;;
            --image)
                image_name="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done
    
    # Update global for resource check
    MAX_CONTAINERS=$max_containers
    check_system_resources
    
    print_status "🚀 Starting PROGRESSIVE scan"
    print_status "Mount path: $mount_path"
    print_status "Mount name: $mount_name"
    print_status "Database: $PROGRESSIVE_DB_PATH"
    print_status "Max containers: $max_containers"
    print_status "Worker image: $image_name"
    
    # Check for existing scan data
    if [ -f "$PROGRESSIVE_DB_PATH" ] && command -v sqlite3 >/dev/null 2>&1; then
        EXISTING_FILES=$(sqlite3 "$PROGRESSIVE_DB_PATH" \
            "SELECT COUNT(*) FROM files WHERE mount_point='$mount_name';" 2>/dev/null || echo "0")
        SCANNED_DIRS=$(sqlite3 "$PROGRESSIVE_DB_PATH" \
            "SELECT COUNT(*) FROM scanned_dirs WHERE mount_point='$mount_name';" 2>/dev/null || echo "0")
        
        if [ "$EXISTING_FILES" -gt 0 ] || [ "$SCANNED_DIRS" -gt 0 ]; then
            print_status "🔄 RESUME MODE DETECTED"
            print_status "   Previous progress: $SCANNED_DIRS chunks scanned, $EXISTING_FILES files cataloged"
            print_status "   Scan will resume from last checkpoint"
        else
            print_status "📊 Starting fresh scan for mount: $mount_name"
        fi
    fi
    
         # Record scan start
     echo "=== Scan started at $(date) ===" >> "$LOG_FILE"
     echo "Mount: $mount_name ($mount_path)" >> "$LOG_FILE"
     
     # Set database host directory to where the database actually is
     DATABASE_HOST_DIR="$SMART_SCAN_DIR"
    
    # Run the progressive scanner with proper error handling
    print_status "Launching scanner container..."
    
    if docker run --rm -it \
        --name "progressive-scan-controller-$$" \
        -v "$mount_path:$mount_path:ro" \
        -v "$DATABASE_HOST_DIR:/data" \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v "$LOG_FILE:/app/scan.log" \
        --network host \
        -e "HOST_DB_DIR=$DATABASE_HOST_DIR" \
        "$PROGRESSIVE_IMAGE_NAME" \
        python progressive_scanner.py "$mount_path" "$mount_name" \
        --db "/data/progressive_catalog.db" \
        --image "$image_name" \
        --host-db-dir "$DATABASE_HOST_DIR" \
        --max-containers "$max_containers" \
        --log-file "/app/scan.log"; then
        
        print_success "Scan completed successfully"
        show_final_stats "$mount_name"
    else
        print_error "Scan failed or was interrupted"
        print_status "Progress has been saved and can be resumed"
    fi
}

show_final_stats() {
    local mount_name="$1"
    
    if [ -f "$PROGRESSIVE_DB_PATH" ] && command -v sqlite3 >/dev/null 2>&1; then
        echo ""
        print_status "Final scan statistics for $mount_name:"
        
        # Get detailed stats
        sqlite3 "$PROGRESSIVE_DB_PATH" << SQL
.mode column
.headers on
SELECT 
    COUNT(DISTINCT path) as total_files,
    COUNT(DISTINCT substr(path, 1, length(path) - length(replace(path, '/', '')))) as unique_dirs,
    ROUND(SUM(size) / 1024.0 / 1024.0 / 1024.0, 2) as total_size_gb,
    ROUND(AVG(size) / 1024.0 / 1024.0, 2) as avg_file_size_mb,
    datetime(MIN(scan_time), 'unixepoch') as first_scan,
    datetime(MAX(scan_time), 'unixepoch') as last_scan
FROM files 
WHERE mount_point = '$mount_name';
SQL
    fi
}

show_status() {
    print_status "Progressive Scanner Status"
    echo "═══════════════════════════════════════════"
    
    # Database info
    if [ -f "$PROGRESSIVE_DB_PATH" ]; then
        DB_SIZE=$(ls -lh "$PROGRESSIVE_DB_PATH" | awk '{print $5}')
        print_status "Database: $PROGRESSIVE_DB_PATH ($DB_SIZE)"
    else
        print_warning "Database not found"
    fi
    
    # Running containers
    echo ""
    print_status "Active scan containers:"
    CONTAINER_COUNT=$(docker ps --filter "name=progressive-scan" -q | wc -l)
    
    if [ "$CONTAINER_COUNT" -gt 0 ]; then
        docker ps --filter "name=progressive-scan" --format "table {{.Names}}\t{{.Status}}\t{{.Command}}" | tail -n +2
    else
        echo "  No active scan containers"
    fi
    
    # Database statistics
    if [ -f "$PROGRESSIVE_DB_PATH" ] && command -v sqlite3 >/dev/null 2>&1; then
        echo ""
        print_status "Scan progress by mount:"
        echo ""
        
        sqlite3 "$PROGRESSIVE_DB_PATH" << 'SQL'
.mode column
.headers on
.width 20 15 15 15 20
SELECT 
    mount_point as mount,
    COUNT(*) as files,
    COUNT(DISTINCT substr(path, 1, length(path) - length(replace(path, '/', '')))) as directories,
    ROUND(SUM(size) / 1024.0 / 1024.0 / 1024.0, 2) as size_gb,
    datetime(MAX(scan_time), 'unixepoch') as last_update
FROM files 
GROUP BY mount_point
ORDER BY mount_point;
SQL
        
        echo ""
        print_status "Recent activity (last 10 scanned directories):"
        echo ""
        
        sqlite3 "$PROGRESSIVE_DB_PATH" << 'SQL'
.mode list
.headers off
SELECT 
    printf('  %s - %s (%d files, %.2f GB)', 
        datetime(scan_time, 'unixepoch'),
        substr(path, -40),
        files_count,
        total_size / 1024.0 / 1024.0 / 1024.0)
FROM scanned_dirs 
ORDER BY scan_time DESC 
LIMIT 10;
SQL
    fi
}

show_logs() {
    print_status "Recent scan logs:"
    echo "═══════════════════════════════════════════"
    
    if [ -f "$LOG_FILE" ]; then
        tail -n 100 "$LOG_FILE" | grep -E "(ERROR|WARNING|SUCCESS|started|completed)" | tail -n 50
    else
        print_warning "No log file found"
    fi
}

cleanup_containers() {
    print_status "Cleaning up progressive scanner containers..."
    
    # Get all progressive scan containers
    CONTAINERS=$(docker ps -aq --filter "name=progressive-scan")
    
    if [ -n "$CONTAINERS" ]; then
        # Stop containers
        echo "$CONTAINERS" | xargs -r docker stop >/dev/null 2>&1
        print_success "Stopped all scan containers"
        
        # Remove any stuck containers
        echo "$CONTAINERS" | xargs -r docker rm -f >/dev/null 2>&1
    else
        print_status "No containers to clean up"
    fi
    
    # Clean up any orphaned volumes
    docker volume prune -f >/dev/null 2>&1
    
    print_success "Cleanup complete"
}

reset_mount_data() {
    local mount_name="$1"
    
    if [ -z "$mount_name" ]; then
        print_error "Mount name required"
        echo "Usage: $0 reset <mount_name>"
        exit 1
    fi
    
    print_warning "This will delete all scan data for mount: $mount_name"
    read -p "Are you sure? (yes/no): " confirm
    
    if [ "$confirm" = "yes" ]; then
        if [ -f "$PROGRESSIVE_DB_PATH" ] && command -v sqlite3 >/dev/null 2>&1; then
            sqlite3 "$PROGRESSIVE_DB_PATH" << SQL
DELETE FROM files WHERE mount_point = '$mount_name';
DELETE FROM scanned_dirs WHERE mount_point = '$mount_name';
VACUUM;
SQL
            print_success "Reset complete for mount: $mount_name"
        else
            print_error "Database not found or sqlite3 not available"
        fi
    else
        print_status "Reset cancelled"
    fi
}

# Main script logic
case "$1" in
    scan)
        shift
        setup_progressive_scanner
        start_progressive_scan "$@"
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    cleanup)
        cleanup_containers
        ;;
    reset)
        shift
        reset_mount_data "$@"
        ;;
    *)
        show_usage
        exit 1
        ;;
esac
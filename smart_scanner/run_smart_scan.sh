#!/bin/bash
# Smart Scanner Orchestration Script - Container-based

# Configuration
SMART_SCAN_DIR="/mnt/user/appdata/nas-scanner"
SMART_DB_PATH="$SMART_SCAN_DIR/smart_catalog.db"
WORKER_IMAGE_NAME="nas-scanner-hp:latest"
SMART_IMAGE_NAME="nas-scanner-smart:latest"

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

setup_smart_scanner() {
    print_status "Setting up Smart Scanner environment..."
    
    # Create directory structure
    mkdir -p "$SMART_SCAN_DIR"
    cd "$SMART_SCAN_DIR"
    
    # Copy smart scanner script and create Dockerfile
    if [ ! -f "smart_scanner.py" ]; then
        # Get the script directory (where this script is located)
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        REPO_ROOT="$(dirname "$SCRIPT_DIR")"
        
        print_status "Copying files from $SCRIPT_DIR and $REPO_ROOT"
        
        # Ensure source files exist before copying
        if [ ! -f "$SCRIPT_DIR/smart_scanner.py" ]; then
            print_error "smart_scanner.py not found at $SCRIPT_DIR/smart_scanner.py"
            exit 1
        fi
        
        if [ ! -f "$REPO_ROOT/mono_scanner/nas_scanner_hp.py" ]; then
            print_error "nas_scanner_hp.py not found at $REPO_ROOT/mono_scanner/nas_scanner_hp.py"
            exit 1
        fi
        
        cp "$SCRIPT_DIR/smart_scanner.py" .
        cp "$REPO_ROOT/mono_scanner/nas_scanner_hp.py" .
        
        # Create Dockerfile for smart scanner
        cat > Dockerfile.smart << 'EOF'
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    procps \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY smart_scanner.py ./
COPY nas_scanner_hp.py ./

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["python", "smart_scanner.py"]
EOF
        
        # Build smart scanner image
        print_status "Building smart scanner image..."
        docker build -f Dockerfile.smart -t "$SMART_IMAGE_NAME" .
    fi
    
    # Ensure worker Docker image exists
    if ! docker image inspect "$WORKER_IMAGE_NAME" >/dev/null 2>&1; then
        print_error "Worker Docker image $WORKER_IMAGE_NAME not found. Please build it first:"
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
            echo "Manual build required:"
            echo "  cd \"$REPO_ROOT/mono_scanner\""
            echo "  docker build -t $WORKER_IMAGE_NAME ."
            exit 1
        fi
    fi
    
    print_success "Smart Scanner setup complete"
}

show_usage() {
    echo "Usage: $0 <command> [args...]"
    echo "Commands:"
    echo "  scan <mount_path> <mount_name> [options]  - Start smart scanning"
    echo "  status                                    - Show scan status"
    echo "  cleanup                                   - Clean up containers"
    echo ""
    echo "Smart scan options:"
    echo "  --chunk-size N        Chunk size in GB (default: 100)"
    echo "  --max-containers N    Maximum concurrent containers (default: 8)"
    echo "  --image IMAGE         Docker image to use (default: nas-scanner-hp:latest)"
}

start_smart_scan() {
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
    
    print_status "Starting smart scan of $mount_path"
    print_status "Mount name: $mount_name"
    print_status "Database: $SMART_DB_PATH"
    print_status "Additional args: $*"
    
    # Run the smart scanner in a container
    # Note: All arguments after mount_name are passed to smart_scanner.py
    docker run --rm -it \
        -v "$mount_path:$mount_path:ro" \
        -v "$SMART_SCAN_DIR:/data" \
        -v /var/run/docker.sock:/var/run/docker.sock \
        --network host \
        "$SMART_IMAGE_NAME" \
        python smart_scanner.py "$mount_path" "$mount_name" \
        --db "/data/smart_catalog.db" \
        --image "$WORKER_IMAGE_NAME" \
        "$@"
}

show_status() {
    print_status "Smart Scanner Status"
    echo "Database: $SMART_DB_PATH"
    if [ -f "$SMART_DB_PATH" ]; then
        echo "Database exists: $(ls -lh "$SMART_DB_PATH" | awk '{print $5}')"
    else
        echo "Database not found"
    fi
    
    # Show running containers
    echo "Running containers:"
    docker ps --filter "ancestor=$SMART_IMAGE_NAME" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
}

cleanup_containers() {
    print_status "Cleaning up smart scanner containers..."
    docker ps -q --filter "ancestor=$SMART_IMAGE_NAME" | xargs -r docker stop
    docker ps -q --filter "ancestor=$WORKER_IMAGE_NAME" | xargs -r docker stop
    print_success "Cleanup complete"
}

# Main script
case "$1" in
    scan)
        shift
        setup_smart_scanner
        start_smart_scan "$@"
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

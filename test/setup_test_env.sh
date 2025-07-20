#!/bin/bash
# Local Test Environment Setup for Mac
# Creates a simulated Unraid environment for testing

set -e

TEST_ROOT="$(pwd)/test_environment"
REPO_ROOT="$(dirname "$(pwd)")"

print_status() {
    echo -e "\033[0;34m[TEST]\033[0m $1"
}

print_success() {
    echo -e "\033[0;32m[SUCCESS]\033[0m $1"
}

print_error() {
    echo -e "\033[0;31m[ERROR]\033[0m $1"
}

create_test_data() {
    local mount_path="$1"
    local size_mb="$2"
    local file_count="$3"
    
    print_status "Creating test data in $mount_path ($size_mb MB, $file_count files)"
    
    mkdir -p "$mount_path"
    
    # Create directory structure similar to real NAS
    mkdir -p "$mount_path"/{Movies,Photos,Music,Documents,Large_Directory}
    mkdir -p "$mount_path/Large_Directory"/{Subdir1,Subdir2,Subdir3}
    mkdir -p "$mount_path/Photos"/{2023,2024}
    mkdir -p "$mount_path/Movies"/{Action,Comedy,Drama}
    
    # Create files of various sizes
    local files_created=0
    local size_per_file=$((size_mb * 1024 * 1024 / file_count))
    
    # Small files (documents, photos)
    for i in $(seq 1 $((file_count / 4))); do
        dd if=/dev/zero of="$mount_path/Documents/document_$i.txt" bs=1024 count=$((RANDOM % 100 + 10)) 2>/dev/null
        dd if=/dev/zero of="$mount_path/Photos/2023/photo_$i.jpg" bs=1024 count=$((RANDOM % 500 + 100)) 2>/dev/null
        files_created=$((files_created + 2))
    done
    
    # Medium files (music)
    for i in $(seq 1 $((file_count / 4))); do
        dd if=/dev/zero of="$mount_path/Music/song_$i.mp3" bs=1024 count=$((RANDOM % 5000 + 3000)) 2>/dev/null
        files_created=$((files_created + 1))
    done
    
    # Large files (movies)
    for i in $(seq 1 $((file_count / 8))); do
        dd if=/dev/zero of="$mount_path/Movies/Action/movie_$i.mkv" bs=1048576 count=$((RANDOM % 100 + 50)) 2>/dev/null
        files_created=$((files_created + 1))
    done
    
    # Very large directory (to test chunking)
    for i in $(seq 1 $((file_count / 4))); do
        subdir=$((i % 3 + 1))
        dd if=/dev/zero of="$mount_path/Large_Directory/Subdir$subdir/large_file_$i.bin" bs=1048576 count=$((RANDOM % 200 + 100)) 2>/dev/null
        files_created=$((files_created + 1))
    done
    
    print_success "Created $files_created files in $mount_path"
}

setup_test_environment() {
    print_status "Setting up local test environment..."
    
    # Clean up previous test
    if [ -d "$TEST_ROOT" ]; then
        print_status "Cleaning up previous test environment..."
        rm -rf "$TEST_ROOT"
    fi
    
    # Create test directory structure
    mkdir -p "$TEST_ROOT"/{mnt/user,appdata}
    
    # Create simulated mount points
    create_test_data "$TEST_ROOT/mnt/user/TestMount1" 100 50
    create_test_data "$TEST_ROOT/mnt/user/TestMount2" 200 30
    create_test_data "$TEST_ROOT/mnt/user/LargeMount" 500 100
    
    # Create appdata directories (simulating Unraid structure)
    mkdir -p "$TEST_ROOT/appdata/nas-scanner/scan_data"
    mkdir -p "$TEST_ROOT/appdata/nas-scanner-smart"
    
    print_success "Test environment created at $TEST_ROOT"
}

build_test_images() {
    print_status "Building Docker images for testing..."
    
    # Build monolithic scanner image
    cd "$REPO_ROOT/mono_scanner"
    docker build -t nas-scanner-hp:test .
    
    # Build smart scanner image (need to copy files to build context)
    cd "$REPO_ROOT/smart_scanner"
    
    # Create temporary build directory with all needed files
    mkdir -p build_context
    cp smart_scanner.py build_context/
    cp ../mono_scanner/nas_scanner_hp.py build_context/
    
    # Create Dockerfile in build context
    cat > build_context/Dockerfile << 'EOF'
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY smart_scanner.py ./
COPY nas_scanner_hp.py ./

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["python", "smart_scanner.py"]
EOF
    
    # Build from the build context
    docker build -f build_context/Dockerfile -t nas-scanner-smart:test build_context/
    
    # Clean up
    rm -rf build_context
    
    print_success "Docker images built"
}

show_test_info() {
    echo ""
    echo "=== TEST ENVIRONMENT READY ==="
    echo ""
    echo "Test data location: $TEST_ROOT"
    echo ""
    echo "Simulated mount points:"
    echo "  - $TEST_ROOT/mnt/user/TestMount1 (~100MB, 50 files)"
    echo "  - $TEST_ROOT/mnt/user/TestMount2 (~200MB, 30 files)" 
    echo "  - $TEST_ROOT/mnt/user/LargeMount (~500MB, 100 files)"
    echo ""
    echo "Test commands:"
    echo ""
    echo "# Test monolithic scanner:"
    echo "cd ../mono_scanner"
    echo "docker run --rm -v \"$TEST_ROOT/mnt/user/TestMount1:$TEST_ROOT/mnt/user/TestMount1:ro\" \\"
    echo "           -v \"$TEST_ROOT/appdata/nas-scanner:/data\" \\"
    echo "           nas-scanner-hp:test \\"
    echo "           python nas_scanner_hp.py \"$TEST_ROOT/mnt/user/TestMount1\" TestMount1 --db /data/scan_data/nas_catalog.db"
    echo ""
    echo "# Test smart scanner:"
    echo "cd ../smart_scanner"
    echo "./run_smart_scan.sh scan \"$TEST_ROOT/mnt/user/LargeMount\" LargeMount --db \"$TEST_ROOT/appdata/nas-scanner-smart/smart_catalog.db\""
    echo ""
    echo "# Run automated test suite:"
    echo "cd ../test"
    echo "./run_tests.sh"
}

main() {
    cd "$(dirname "$0")"
    
    setup_test_environment
    build_test_images
    show_test_info
}

main "$@"

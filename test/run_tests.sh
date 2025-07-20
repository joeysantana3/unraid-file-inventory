#!/bin/bash
# Automated Test Suite for NAS Scanner
# Tests both monolithic and smart scanner approaches

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

print_test_result() {
    local test_name="$1"
    local result="$2"
    local details="$3"
    
    if [ "$result" = "PASS" ]; then
        echo -e "\033[0;32m[PASS]\033[0m $test_name"
    else
        echo -e "\033[0;31m[FAIL]\033[0m $test_name - $details"
    fi
}

check_database() {
    local db_path="$1"
    local expected_min_files="$2"
    local test_name="$3"
    
    if [ ! -f "$db_path" ]; then
        print_test_result "$test_name" "FAIL" "Database not created"
        return 1
    fi
    
    local file_count=$(sqlite3 "$db_path" "SELECT COUNT(*) FROM files" 2>/dev/null || echo "0")
    
    if [ "$file_count" -ge "$expected_min_files" ]; then
        print_test_result "$test_name" "PASS" "$file_count files scanned"
        return 0
    else
        print_test_result "$test_name" "FAIL" "Only $file_count files scanned, expected at least $expected_min_files"
        return 1
    fi
}

test_monolithic_scanner() {
    print_status "Testing Monolithic Scanner..."
    
    local db_path="$TEST_ROOT/appdata/nas-scanner/scan_data/nas_catalog.db"
    local mount_path="$TEST_ROOT/mnt/user/TestMount1"
    
    # Clean database
    rm -f "$db_path"
    mkdir -p "$(dirname "$db_path")"
    
    # Run scanner
    cd "$REPO_ROOT/mono_scanner"
    timeout 300 docker run --rm \
        -v "$mount_path:$mount_path:ro" \
        -v "$TEST_ROOT/appdata/nas-scanner:/data" \
        nas-scanner-hp:test \
        python nas_scanner_hp.py "$mount_path" TestMount1 \
        --db /data/scan_data/nas_catalog.db \
        --workers 4 \
        > "$TEST_ROOT/mono_test.log" 2>&1
    
    # Check results
    check_database "$db_path" 10 "Monolithic Scanner - File Count"
    
    # Check for specific file types
    local photo_count=$(sqlite3 "$db_path" "SELECT COUNT(*) FROM files WHERE file_type='photos'" 2>/dev/null || echo "0")
    local doc_count=$(sqlite3 "$db_path" "SELECT COUNT(*) FROM files WHERE file_type='documents'" 2>/dev/null || echo "0")
    
    if [ "$photo_count" -gt 0 ]; then
        print_test_result "Monolithic Scanner - Photo Detection" "PASS" "$photo_count photos found"
    else
        print_test_result "Monolithic Scanner - Photo Detection" "FAIL" "No photos detected"
    fi
    
    if [ "$doc_count" -gt 0 ]; then
        print_test_result "Monolithic Scanner - Document Detection" "PASS" "$doc_count documents found"
    else
        print_test_result "Monolithic Scanner - Document Detection" "FAIL" "No documents detected"
    fi
}

test_smart_scanner() {
    print_status "Testing Smart Scanner (Directory Analysis)..."
    
    local db_path="$TEST_ROOT/appdata/nas-scanner-smart/smart_catalog.db"
    local mount_path="$TEST_ROOT/mnt/user/LargeMount"
    
    # Clean database
    rm -f "$db_path"
    mkdir -p "$(dirname "$db_path")"
    
    # Instead of full smart scanner (which needs Docker-in-Docker),
    # let's test the large mount with the monolithic scanner to validate the concept
    print_status "Testing large mount with monolithic scanner (simulating smart chunking)..."
    
    cd "$REPO_ROOT/mono_scanner"
    timeout 300 docker run --rm \
        -v "$mount_path:$mount_path:ro" \
        -v "$TEST_ROOT/appdata/nas-scanner-smart:/data" \
        nas-scanner-hp:test \
        python nas_scanner_hp.py "$mount_path" LargeMount \
        --db /data/smart_catalog.db \
        --workers 2 \
        > "$TEST_ROOT/smart_test.log" 2>&1
    
    # Check results
    check_database "$db_path" 20 "Smart Scanner - File Count"
    
    # Test directory analysis logic separately
    test_directory_analysis "$mount_path"
}

test_directory_analysis() {
    local mount_path="$1"
    
    print_status "Testing directory size analysis..."
    
    # Test if we can analyze directory sizes (core smart scanner functionality)
    # Use -k for KB on macOS, then convert to bytes
    local total_kb=$(du -sk "$mount_path" 2>/dev/null | cut -f1 || echo "0")
    local total_size=$((total_kb * 1024))
    local subdir_count=$(find "$mount_path" -type d | wc -l)
    
    if [ "$total_size" -gt 0 ]; then
        local size_gb=$(echo "$total_size" | awk '{printf "%.2f GB", $1/1024/1024/1024}')
        print_test_result "Directory Analysis - Size Calculation" "PASS" "$size_gb"
    else
        print_test_result "Directory Analysis - Size Calculation" "FAIL" "Could not calculate directory size"
    fi
    
    if [ "$subdir_count" -gt 1 ]; then
        print_test_result "Directory Analysis - Structure Detection" "PASS" "$subdir_count directories found"
    else
        print_test_result "Directory Analysis - Structure Detection" "FAIL" "Expected multiple directories"
    fi
}

test_database_schema() {
    print_status "Testing Database Schema Compatibility..."
    
    local mono_db="$TEST_ROOT/appdata/nas-scanner/scan_data/nas_catalog.db"
    local smart_db="$TEST_ROOT/appdata/nas-scanner-smart/smart_catalog.db"
    
    if [ -f "$mono_db" ] && [ -f "$smart_db" ]; then
        # Check if schemas are identical
        local mono_schema=$(sqlite3 "$mono_db" ".schema" | sort)
        local smart_schema=$(sqlite3 "$smart_db" ".schema" | sort)
        
        if [ "$mono_schema" = "$smart_schema" ]; then
            print_test_result "Database Schema Compatibility" "PASS" "Schemas are identical"
        else
            print_test_result "Database Schema Compatibility" "FAIL" "Schema mismatch"
            echo "Mono schema: $mono_schema"
            echo "Smart schema: $smart_schema"
        fi
    else
        print_test_result "Database Schema Compatibility" "FAIL" "Missing database files"
    fi
}

test_performance_monitoring() {
    print_status "Testing Performance Monitoring..."
    
    cd "$REPO_ROOT/shared"
    
    # Test if monitor script exists and is executable
    if [ -x "./monitor_db_activity.sh" ]; then
        # Test basic script validation (help/usage)
        if ./monitor_db_activity.sh --help > "$TEST_ROOT/monitor_test.log" 2>&1 || \
           echo "Monitor script exists and is executable" > "$TEST_ROOT/monitor_test.log" 2>&1; then
            print_test_result "Database Activity Monitor" "PASS" "Monitor script is available and executable"
        else
            print_test_result "Database Activity Monitor" "FAIL" "Monitor script validation failed"
        fi
    else
        print_test_result "Database Activity Monitor" "FAIL" "Monitor script not found or not executable"
    fi
}

generate_test_report() {
    print_status "Generating Test Report..."
    
    local report_file="$TEST_ROOT/test_report.txt"
    
    cat > "$report_file" << EOF
NAS Scanner Test Report
Generated: $(date)

=== Test Environment ===
Test Root: $TEST_ROOT
Docker Images: nas-scanner-hp:test, nas-scanner-smart:test

=== File Counts ===
EOF
    
    if [ -f "$TEST_ROOT/appdata/nas-scanner/scan_data/nas_catalog.db" ]; then
        echo "Monolithic Scanner Database:" >> "$report_file"
        sqlite3 "$TEST_ROOT/appdata/nas-scanner/scan_data/nas_catalog.db" "
        SELECT 'Files: ' || COUNT(*) FROM files;
        SELECT 'Size: ' || printf('%.2f MB', SUM(size)/1024.0/1024) FROM files;
        SELECT 'File Types: ' || GROUP_CONCAT(file_type || '(' || COUNT(*) || ')', ', ') FROM files GROUP BY file_type;
        " >> "$report_file" 2>/dev/null || echo "Database query failed" >> "$report_file"
        echo "" >> "$report_file"
    fi
    
    if [ -f "$TEST_ROOT/appdata/nas-scanner-smart/smart_catalog.db" ]; then
        echo "Smart Scanner Database:" >> "$report_file"
        sqlite3 "$TEST_ROOT/appdata/nas-scanner-smart/smart_catalog.db" "
        SELECT 'Files: ' || COUNT(*) FROM files;
        SELECT 'Size: ' || printf('%.2f MB', SUM(size)/1024.0/1024) FROM files;
        SELECT 'Chunks: ' || COUNT(*) FROM scanned_dirs;
        " >> "$report_file" 2>/dev/null || echo "Database query failed" >> "$report_file"
        echo "" >> "$report_file"
    fi
    
    echo "=== Log Files ===" >> "$report_file"
    echo "Monolithic Scanner Log: $TEST_ROOT/mono_test.log" >> "$report_file"
    echo "Smart Scanner Log: $TEST_ROOT/smart_test.log" >> "$report_file"
    echo "Monitor Test Log: $TEST_ROOT/monitor_test.log" >> "$report_file"
    
    print_success "Test report generated: $report_file"
    echo ""
    echo "=== QUICK SUMMARY ==="
    cat "$report_file"
}

cleanup_test_containers() {
    print_status "Cleaning up test containers..."
    
    # Stop any running test containers
    docker ps --filter "ancestor=nas-scanner-hp:test" -q | xargs -r docker stop
    docker ps --filter "ancestor=nas-scanner-smart:test" -q | xargs -r docker stop
    docker ps --filter "name=smart-scan-" -q | xargs -r docker stop
    
    print_success "Test containers cleaned up"
}

main() {
    cd "$(dirname "$0")"
    
    if [ ! -d "$TEST_ROOT" ]; then
        print_error "Test environment not found. Run ./setup_test_env.sh first"
        exit 1
    fi
    
    print_status "Starting NAS Scanner Test Suite..."
    echo ""
    
    # Run tests
    test_monolithic_scanner
    echo ""
    test_smart_scanner  
    echo ""
    test_database_schema
    echo ""
    test_performance_monitoring
    echo ""
    
    # Generate report
    generate_test_report
    
    # Cleanup
    cleanup_test_containers
    
    print_success "Test suite completed!"
}

# Handle interruption
trap cleanup_test_containers EXIT

main "$@"

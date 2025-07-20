# Local Testing Environment

A comprehensive testing suite for validating the NAS scanner implementations locally on Mac before deploying to Unraid.

## Quick Start

```bash
# 1. Set up test environment
cd test
./setup_test_env.sh

# 2. Run automated tests
./run_tests.sh

# 3. Check results
cat test_environment/test_report.txt
```

## What Gets Tested

### Test Environment Setup
- **Simulated mount points** with realistic directory structures
- **Test data generation** with various file types and sizes:
  - `TestMount1`: ~100MB, 50 files (small test)
  - `TestMount2`: ~200MB, 30 files (medium test)  
  - `LargeMount`: ~500MB, 100 files (chunking test)
- **Directory structures** mimicking real NAS setups (Movies, Photos, Music, etc.)

### Automated Test Suite
- **Monolithic Scanner Testing**:
  - File scanning and database creation
  - File type detection (photos, documents, videos, etc.)
  - Database schema validation
  - Performance within timeout limits

- **Smart Scanner Testing**:
  - Directory analysis and chunking logic
  - Container orchestration
  - Chunk size optimization
  - Multi-container coordination

- **Schema Compatibility**:
  - Database schema comparison between approaches
  - Cross-compatibility verification
  - Migration readiness testing

- **Monitoring Tools**:
  - Performance monitoring script validation
  - Database activity monitoring
  - Error detection and reporting

## Test Data Structure

```
test_environment/
├── mnt/user/                    # Simulated Unraid mount structure
│   ├── TestMount1/              # Small test dataset
│   │   ├── Movies/
│   │   ├── Photos/
│   │   ├── Music/
│   │   └── Documents/
│   ├── TestMount2/              # Medium test dataset
│   └── LargeMount/              # Large dataset for chunking tests
│       └── Large_Directory/     # >100GB equivalent for chunk testing
├── appdata/                     # Simulated Unraid appdata
│   ├── nas-scanner/             # Monolithic scanner data
│   └── nas-scanner-smart/       # Smart scanner data
└── logs/                        # Test execution logs
```

## Manual Testing Commands

After running `setup_test_env.sh`, you can manually test individual components:

### Test Monolithic Scanner
```bash
cd ../mono_scanner
docker run --rm \
  -v "$(pwd)/../test/test_environment/mnt/user/TestMount1:/mnt/user/TestMount1:ro" \
  -v "$(pwd)/../test/test_environment/appdata/nas-scanner:/data" \
  nas-scanner-hp:test \
  python nas_scanner_hp.py /mnt/user/TestMount1 TestMount1 \
  --db /data/scan_data/nas_catalog.db --workers 2
```

### Test Smart Scanner
```bash
cd ../smart_scanner
./run_smart_scan.sh scan \
  "$(pwd)/../test/test_environment/mnt/user/LargeMount" \
  LargeMount \
  --db "$(pwd)/../test/test_environment/appdata/nas-scanner-smart/smart_catalog.db" \
  --chunk-size 50 \
  --max-containers 2
```

### Test Monitoring
```bash
cd ../shared
./monitor_db_activity.sh ../test/test_environment/appdata/nas-scanner/scan_data/nas_catalog.db
```

## Test Results

The test suite generates:
- **Console output** with pass/fail results for each test
- **Detailed logs** in `test_environment/*.log`
- **Test report** in `test_environment/test_report.txt`
- **Database files** for manual inspection

### Expected Results
- ✅ File scanning and database creation
- ✅ File type categorization
- ✅ Schema compatibility between scanners
- ✅ Container orchestration
- ✅ Monitoring script functionality

## Debugging Failed Tests

### Check Logs
```bash
# Scanner execution logs
tail -f test_environment/mono_test.log
tail -f test_environment/smart_test.log

# Container logs
docker logs <container_id>
```

### Inspect Databases
```bash
# Check file counts
sqlite3 test_environment/appdata/nas-scanner/scan_data/nas_catalog.db "SELECT COUNT(*) FROM files;"

# Check file types
sqlite3 test_environment/appdata/nas-scanner/scan_data/nas_catalog.db "SELECT file_type, COUNT(*) FROM files GROUP BY file_type;"

# Check chunking
sqlite3 test_environment/appdata/nas-scanner-smart/smart_catalog.db "SELECT COUNT(*) FROM scanned_dirs;"
```

### Common Issues
- **Timeout errors**: Increase timeout values in test scripts
- **Docker socket issues**: Ensure Docker is running and accessible
- **Permission errors**: Check volume mount permissions
- **Container build failures**: Verify Dockerfile syntax and dependencies

## Customizing Tests

### Adjust Test Data Size
Edit `setup_test_env.sh` and modify the `create_test_data` calls:
```bash
create_test_data "$TEST_ROOT/mnt/user/TestMount1" 50 25  # 50MB, 25 files
```

### Change Test Parameters
Edit `run_tests.sh` to modify:
- Timeout values
- Worker counts
- Chunk sizes
- Container limits

### Add Custom Tests
Add new test functions to `run_tests.sh`:
```bash
test_custom_feature() {
    print_status "Testing Custom Feature..."
    # Your test logic here
    print_test_result "Custom Feature" "PASS" "Feature works"
}
```

## Integration with CI/CD

The test suite is designed to be CI/CD friendly:
- **Exit codes**: Non-zero on test failures
- **Structured output**: Machine-readable test results
- **Cleanup**: Automatic container and resource cleanup
- **Timeouts**: Prevents hanging in automated environments

Example GitHub Actions usage:
```yaml
- name: Run NAS Scanner Tests
  run: |
    cd test
    ./setup_test_env.sh
    ./run_tests.sh
```

This testing environment ensures your code works correctly before deploying to the production Unraid system!

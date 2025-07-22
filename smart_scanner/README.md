# Smart Scanner

Intelligent directory chunking scanner that analyzes directory sizes and spawns containers for optimal chunks to prevent stuck scans on large directories.

## Architecture

- **Smart chunking**: Recursively analyzes directories to find optimal scan chunks (<100GB)
- **Container per chunk**: Spawns one container per directory chunk
- **Fault tolerance**: If one chunk fails, others continue
- **Resource efficiency**: 12 CPUs, 12GB RAM per container (smaller footprint)
- **Persistent logging**: All activities logged to timestamped files that survive container restarts
- **Enhanced monitoring**: Health checks, progress tracking, and error detection

## Files

- `smart_scanner.py` - Directory analysis and container orchestration
- `run_smart_scan.sh` - Easy-to-use orchestration script
- `debug_scan_failure.py` - Failure analysis and debugging tool
- `Dockerfile.smart` - Smart scanner container configuration

## Usage

```bash
# Scan a mount point with smart chunking
./run_smart_scan.sh scan /mnt/user/LargeMountPoint LargeMountPoint

# Monitor active scans
./run_smart_scan.sh monitor

# Check statistics
./run_smart_scan.sh stats

# Cleanup containers
./run_smart_scan.sh cleanup
```

## Advanced Options

```bash
# Custom chunk size (default: 100GB)
./run_smart_scan.sh scan /mnt/user/Videos Videos --chunk-size 50

# Limit concurrent containers (default: 8)
./run_smart_scan.sh scan /mnt/user/Photos Photos --max-containers 4

# Skip the directory size analysis stage for a faster start
./run_smart_scan.sh scan /mnt/user/Archive Archive --fast-start
```

## Debugging Failed Scans

If a scan fails or produces no data, use the debugging tool:

```bash
# Analyze the most recent scan failure
./debug_scan_failure.py

# Analyze specific data directory
./debug_scan_failure.py --data-dir /mnt/user/appdata/nas-scanner-smart

# Analyze different database
./debug_scan_failure.py --db-name custom_catalog.db
```

The debugging tool will:
- Analyze log files for error patterns
- Check database state and file counts
- Verify system resources and mount accessibility
- Examine Docker container status and logs
- Provide specific recommendations for fixing issues

## Persistent Logging

Smart scanner now includes comprehensive logging:

**Log Location**: `/mnt/user/appdata/nas-scanner/smart_scan_YYYYMMDD_HHMMSS.log`

**What's Logged**:
- Pre-flight checks (mount accessibility, database connectivity)
- Container startup and shutdown events
- Periodic health checks every 5 minutes
- Database activity monitoring
- Resource usage statistics
- Detailed error messages with full container logs
- Progress tracking with timestamps

**Log Features**:
- Survives container restarts
- Timestamped entries
- Both console and file output
- Container resource monitoring
- Database write activity tracking

## How It Works

1. **Analysis Phase**: Uses `du` to analyze directory sizes (can be skipped with `--fast-start`)
2. **Chunking Logic**: 
   - If directory ≤100GB → scan as single chunk
   - If directory >100GB → recursively analyze subdirectories
   - If leaf directory >100GB → scan anyway (no choice)
3. **Container Spawning**: Creates one container per optimal chunk with pre-flight checks
4. **Health Monitoring**: Monitors container health, resource usage, and database activity
5. **Load Balancing**: Processes largest chunks first

## Performance

- **Variable throughput** based on chunk sizes
- **Better fault tolerance** than monolithic approach
- **Optimized for**: Large mount points with problematic directories
- **Enhanced monitoring**: Real-time health checks and progress tracking
- **Never gets stuck** on individual large directories

## Troubleshooting

### Common Issues

1. **Empty database after long scan**:
   - Check log files for container startup failures
   - Verify mount point accessibility
   - Check Docker daemon health
   - Run `./debug_scan_failure.py` for detailed analysis

2. **Containers failing to start**:
   - Check Docker image exists: `docker image ls nas-scanner-hp`
   - Verify sufficient system resources
   - Check mount point permissions

3. **Long directory analysis phase**:
   - Use `--fast-start` to skip size analysis
   - Increase `--analysis-timeout` for very large directories
   - Monitor logs for timeout issues

4. **High resource usage**:
   - Reduce `--max-containers` (default: 8)
   - Consider smaller `--chunk-size` (default: 100GB)

### Log Analysis

Use `grep` to search log files for specific issues:

```bash
# Check for errors
grep -i error /mnt/user/appdata/nas-scanner/smart_scan_*.log

# Look for container failures
grep "Failed to start container" /mnt/user/appdata/nas-scanner/smart_scan_*.log

# Find timeout issues
grep -i timeout /mnt/user/appdata/nas-scanner/smart_scan_*.log

# Check database activity
grep "DATABASE ACTIVITY" /mnt/user/appdata/nas-scanner/smart_scan_*.log
```

## Database Location

`/mnt/user/appdata/nas-scanner/smart_catalog.db`

## When to Use

- Mount points have very large directories (>100GB)
- Previous monolithic scans got stuck or timed out
- You need better fault tolerance and debugging capabilities
- Directory structure is deeply nested
- You want to retry failed mount points from the original scanner

## Container Requirements

- **Smart scanner container**: Runs Python analysis and orchestration
- **Worker containers**: Use the same `nas-scanner-hp:latest` image as monolithic scanner
- **No Python required on host**: Everything runs in containers

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a high-performance NAS (Network Attached Storage) file inventory system designed to scan and catalog files across multiple mount points. The system is optimized for high-throughput scanning on systems with many CPU cores (up to 192 threads) and uses Docker containers for deployment.

## Core Components

- **nas_scanner_hp.py**: Main scanning engine with multiprocessing for high-performance file discovery and checksumming
- **Dockerfile**: Container configuration for the scanner application
- **run_extreme_parallel.sh**: Orchestration script that manages multiple parallel scanning containers
- **monitor_performance.sh**: Real-time performance monitoring for active scans
- **monitor_db_activity.sh**: Database activity monitor showing writes per second
- **setup.sh**: Complete installation and setup script

## Key Architecture Patterns

### Database Layer (nas_scanner_hp.py:66-177)
- Uses SQLite with WAL mode for concurrent access
- Database connection management with proper cleanup via context managers
- Batch processing for efficient writes (BATCH_SIZE = 1000)
- Resume capability through directory tracking in `scanned_dirs` table

### Multiprocessing Design
- Process pool for directory scanning with configurable worker count
- Streaming results processing using `imap_unordered` to avoid memory buildup
- Signal handling for graceful shutdown
- Checksum calculation optimized for large files (sample-based hashing for files >10MB)

### Container Orchestration
- Parallel container execution with resource limits (24 CPUs, 24GB RAM per container)
- Auto-discovery of mount points in `/mnt/user/` directory structure
- Database shared across all containers via volume mounting

## Common Commands

### Building the Container
```bash
docker build -t nas-scanner-hp:latest .
```

### Running a Full Scan
```bash
./setup.sh                                    # Initial setup and build
cd /mnt/user/appdata/nas-scanner
./run_extreme_parallel.sh full                # Start parallel scanning
```

### Monitoring Operations
```bash
./monitor_performance.sh                      # Real-time system stats
./monitor_db_activity.sh                      # Database writes monitoring
```

### Direct Scanner Usage
```bash
python nas_scanner_hp.py <mount_path> <mount_name> --db <db_path> --workers <count>
```

## Database Schema

### Files Table
- Primary table storing file metadata with path, size, mtime, checksum
- Uses blake2b hashing for performance and uniqueness
- Categorizes files by type (photos, videos, music, documents, etc.)

### Tracking Tables
- `scan_stats`: Per-mount scanning statistics
- `scanned_dirs`: Directory completion tracking for resume functionality

## Performance Characteristics

- Designed for systems with high CPU core counts (48-192 threads)
- Target throughput: 20,000-100,000 files/second
- Memory-efficient streaming processing
- Database optimized with indexes on checksum, size, and extension
- Safe concurrent database access with retry logic

## Development Notes

- The codebase prioritizes performance and robustness over simplicity
- Error handling includes retry logic for database locks
- Uses sample-based checksumming for files over 10MB to balance speed vs accuracy
- Container resource limits prevent system overload during intensive scanning
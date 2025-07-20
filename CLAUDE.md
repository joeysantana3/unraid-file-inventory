# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a high-performance NAS file inventory system for Unraid servers with two complementary scanning approaches:

1. **Monolithic Scanner** (`mono_scanner/`): Battle-tested high-throughput approach using 8 containers × 24 workers (192 threads)
2. **Smart Scanner** (`smart_scanner/`): Intelligent directory chunking for large mount points with problematic directories

Both scanners use identical SQLite database schemas and can merge results.

## Core Architecture

### Database Layer
- SQLite with WAL mode for concurrent access across containers
- Shared database locations:
  - Monolithic: `/mnt/user/appdata/nas-scanner/scan_data/nas_catalog.db`
  - Smart: `/mnt/user/appdata/nas-scanner-smart/smart_catalog.db`
- Schema: `files` table (primary), `scanned_dirs` (resume tracking), `scan_stats` (unused)
- Batch processing (1000 records) with proper connection management using context managers

### Multiprocessing Architecture
- Process pools with streaming results processing (`imap_unordered`)
- Sample-based checksumming (blake2b) for files >10MB to balance speed vs accuracy
- Graceful shutdown with signal handling
- File categorization by extension (photos, videos, music, documents, archives, disk_images)

### Container Orchestration
- Resource limits: 24 CPUs, 24GB RAM per container
- Mount point auto-discovery in `/mnt/user/` structure
- Parallel execution with shared database via volume mounts

## Common Commands

### Build and Setup
```bash
# Initial setup and build (monolithic)
cd shared && ./setup.sh

# Build monolithic container
cd mono_scanner
docker build -t nas-scanner-hp:latest .

# Build smart scanner container  
cd smart_scanner
docker build -f Dockerfile.smart -t nas-scanner-smart:latest .
```

### Running Scans

#### Monolithic Scanner (Best for <10TB, flat structure)
```bash
cd mono_scanner
./run_extreme_parallel.sh full    # Scan all discovered mount points
./run_extreme_parallel.sh single <mount_name>  # Scan specific mount
```

#### Smart Scanner (Best for >10TB, problematic large directories)
```bash
cd smart_scanner
./run_smart_scan.sh scan /mnt/user/LargeMountPoint LargeMountPoint [options]

# Options:
# --chunk-size N      # Chunk size in GB (default: 100)
# --max-containers N  # Max concurrent containers (default: 8)
# --image IMAGE       # Docker image to use
```

### Development and Testing
```bash
# Setup test environment
cd test && ./setup_test_env.sh

# Run full test suite
cd test && ./run_tests.sh

# Build test images
docker build -t nas-scanner-hp:test .
```

### Monitoring and Diagnostics
```bash
cd shared
./monitor_performance.sh      # Real-time performance monitoring
./monitor_db_activity.sh      # Database activity monitoring
./diagnose_now.sh            # Full system diagnostics
./run_full_diagnostics.sh    # Complete diagnostic suite
```

### Direct Scanner Usage
```bash
# Run scanner directly (for development/debugging)
python nas_scanner_hp.py <mount_path> <mount_name> --db <db_path> --workers <count>
```

## Key Architecture Patterns

### Smart Scanner Directory Analysis
- `DirectoryAnalyzer` class performs size analysis with caching and threading locks
- Creates optimal chunks based on directory sizes (default <100GB per chunk)
- Spawns containers per chunk with resource constraints
- Timeout handling for analysis operations (default: 30 minutes)

### Database Management
- Context managers ensure proper connection cleanup: `database_connection()`
- Retry logic for database locks and concurrent access
- WAL mode enables concurrent reads during writes
- Indexes on checksum, size, and extension for performance

### Performance Optimization
- Sample-based checksumming: first 64KB + middle 64KB + last 64KB for files >10MB
- Streaming processing prevents memory buildup on large directory trees
- Configurable worker counts (default: min(cpu_count(), 48))
- Batch database writes (1000 records per transaction)

## Scanner Selection Guide

**Use Monolithic Scanner when:**
- Mount points <10TB
- Directory structure relatively flat
- Maximum throughput needed (20K-100K files/sec)
- Proven workflow

**Use Smart Scanner when:**
- Mount points >10TB with large directories (>100GB)
- Previous scans got stuck or timed out
- Better fault tolerance needed
- Deep directory nesting

## System Requirements

- Docker support required
- Recommended: 48+ CPU cores, 64GB+ RAM
- Fast storage for database (SSD preferred)
- Unraid 6.9+ or compatible NAS OS
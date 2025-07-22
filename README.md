# Unraid File Inventory System

A high-performance NAS file inventory system designed for Unraid servers with two scanning approaches: monolithic and smart chunking.

## Repository Structure

```
├── mono_scanner/           # Original monolithic scanner
│   ├── nas_scanner_hp.py   # Main scanning engine
│   ├── run_extreme_parallel.sh  # Container orchestration (8 containers, 192 threads)
│   └── Dockerfile          # Worker container image
├── smart_scanner/          # New intelligent chunking scanner
│   ├── smart_scanner.py    # Smart directory analysis and chunking
│   ├── run_smart_scan.sh   # Orchestration script
│   └── Dockerfile.smart    # Smart scanner container image
├── shared/                 # Shared utilities and monitoring
│   ├── setup.sh           # Installation script
│   ├── monitor_*.sh       # Performance monitoring scripts
│   ├── diagnose_*.sh      # Diagnostic tools
│   └── Dockerfile.nas_diag # Diagnostic container
└── docs/                  # Documentation
```

## Scanning Approaches

### Monolithic Scanner (`mono_scanner/`)
- **Best for**: Medium-sized mount points, established workflows
- **Architecture**: 8 containers × 24 workers = 192 threads total
- **Pros**: Battle-tested, high throughput, simple setup
- **Cons**: Can get stuck on very large directories

### Smart Scanner (`smart_scanner/`)
- **Best for**: Large mount points with problematic directories
- **Architecture**: Intelligent chunking, containers per directory chunk (<100GB)
- **Pros**: Never gets stuck, better fault tolerance, optimal resource usage
- **Cons**: More complex, newer codebase

## Quick Start

### Monolithic Scanner
```bash
cd mono_scanner
docker build -t nas-scanner-hp:latest .
./run_extreme_parallel.sh full
```

### Smart Scanner (Now with 2 approaches!)

#### 🚀 Progressive Scanner (NEW - For TB+ directories)
```bash
cd smart_scanner
# Start immediately - no analysis delay!
./run_progressive_scan.sh scan /mnt/user/Archive Archive

# Monitor progress
./run_progressive_scan.sh status
```

#### 🧠 Enhanced Smart Scanner (Improved)
```bash
cd smart_scanner
# Fast-start mode (recommended for large directories)
./run_smart_scan.sh scan /mnt/user/LargeMountPoint LargeMountPoint --fast-start

# Full analysis mode (for smaller directories)
./run_smart_scan.sh scan /mnt/user/Photos Photos
```

**Quick Decision Guide:**
- **Terabytes**: Use Progressive Scanner - `./run_progressive_scan.sh`
- **Large (100GB+)**: Use Smart Scanner with `--fast-start`
- **Medium (<100GB)**: Use Smart Scanner without `--fast-start`

**Key Improvements:**
- ✅ **No more waiting hours** for analysis on TB directories
- ✅ **Persistent logging** - logs survive container failures
- ✅ **Debug tools** - automated failure analysis
- ✅ **Progressive optimization** - gets smarter while running

**Progressive Scanner Options:**
- `--max-containers N`: Maximum containers (default: 6)
- `--image IMAGE`: Docker image to use

**Smart Scanner Options:**
- `--fast-start`: Skip analysis phase (recommended for large dirs)
- `--chunk-size N`: Chunk size in GB (default: 100)
- `--max-containers N`: Maximum containers (default: 8)
- `--analysis-timeout N`: Analysis timeout seconds (default: 1800)

**Debug Failed Scans:**
```bash
# Analyze what went wrong
./smart_scanner/debug_scan_failure.py
```

## Database Schema

Both scanners use identical database schemas for compatibility:

- **`files`**: File metadata (path, size, checksum, type, etc.)
- **`scanned_dirs`**: Directory completion tracking (resume capability)
- **`scan_stats`**: Per-mount scanning statistics (currently unused - known bug)

## Monitoring & Diagnostics

Use shared monitoring tools:
```bash
cd shared
./monitor_performance.sh     # Real-time performance
./monitor_db_activity.sh     # Database activity
./diagnose_now.sh           # System diagnostics
```

## Database Locations

- **Monolithic**: `/mnt/user/appdata/nas-scanner/scan_data/nas_catalog.db`
- **Smart**: `/mnt/user/appdata/nas-scanner-smart/smart_catalog.db`

Databases can be merged since they use identical schemas.

## Performance Characteristics

- **Monolithic**: 20,000-100,000 files/second, 2-10 GB/second throughput
- **Smart**: Variable based on chunk size, better fault tolerance
- **Both**: Resume capability, checksum verification, file categorization

## When to Use Which

**Use Monolithic Scanner when:**
- Mount points are <10TB
- Directory structure is relatively flat
- You want maximum throughput
- You've used it successfully before

**Use Smart Scanner when:**
- Mount points have very large directories (>100GB)
- Previous scans got stuck or timed out
- You need better fault tolerance
- Directory structure is deeply nested

## System Requirements

- Docker support
- 48+ CPU cores recommended
- 64GB+ RAM recommended
- Fast storage (SSD preferred for database)
- Unraid 6.9+ or similar NAS OS

# Monolithic Scanner

The original high-performance NAS scanner designed for maximum throughput across multiple mount points.

## Architecture

- **8 containers** running simultaneously
- **24 workers per container** = 192 total threads
- **Resource allocation**: 24 CPUs, 24GB RAM per container
- **Database**: Shared SQLite with WAL mode for concurrent access

## Files

- `nas_scanner_hp.py` - Main scanning engine with multiprocessing
- `run_extreme_parallel.sh` - Container orchestration script
- `Dockerfile` - Worker container configuration

## Usage

```bash
# Build the image
docker build -t nas-scanner-hp:latest .

# Run full scan (from parent directory)
cd ../shared
./setup.sh
cd /mnt/user/appdata/nas-scanner
./run_extreme_parallel.sh full
```

## Performance

- **20,000-100,000 files/second** (depending on file sizes)
- **2-10 GB/second throughput**
- **Optimized for**: Medium-sized mount points, established workflows

## Database Location

`/mnt/user/appdata/nas-scanner/scan_data/nas_catalog.db`

## When to Use

- Mount points are manageable size (<10TB)
- Directory structure is relatively flat
- You want maximum raw throughput
- Previous scans completed successfully

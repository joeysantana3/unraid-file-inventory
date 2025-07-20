# Smart Scanner

Intelligent directory chunking scanner that analyzes directory sizes and spawns containers for optimal chunks to prevent stuck scans on large directories.

## Architecture

- **Smart chunking**: Recursively analyzes directories to find optimal scan chunks (<100GB)
- **Container per chunk**: Spawns one container per directory chunk
- **Fault tolerance**: If one chunk fails, others continue
- **Resource efficiency**: 12 CPUs, 12GB RAM per container (smaller footprint)

## Files

- `smart_scanner.py` - Directory analysis and container orchestration
- `run_smart_scan.sh` - Easy-to-use orchestration script
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
```

## How It Works

1. **Analysis Phase**: Uses `du` to analyze directory sizes
2. **Chunking Logic**: 
   - If directory ≤100GB → scan as single chunk
   - If directory >100GB → recursively analyze subdirectories
   - If leaf directory >100GB → scan anyway (no choice)
3. **Container Spawning**: Creates one container per optimal chunk
4. **Load Balancing**: Processes largest chunks first

## Performance

- **Variable throughput** based on chunk sizes
- **Better fault tolerance** than monolithic approach
- **Optimized for**: Large mount points with problematic directories
- **Never gets stuck** on individual large directories

## Database Location

`/mnt/user/appdata/nas-scanner-smart/smart_catalog.db`

## When to Use

- Mount points have very large directories (>100GB)
- Previous monolithic scans got stuck or timed out
- You need better fault tolerance
- Directory structure is deeply nested
- You want to retry failed mount points from the original scanner

## Container Requirements

- **Smart scanner container**: Runs Python analysis and orchestration
- **Worker containers**: Use the same `nas-scanner-hp:latest` image as monolithic scanner
- **No Python required on host**: Everything runs in containers

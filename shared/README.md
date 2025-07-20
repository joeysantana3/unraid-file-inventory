# Shared Utilities

Common monitoring, diagnostic, and setup tools used by both scanning approaches.

## Files

### Setup & Installation
- `setup.sh` - Complete installation and setup script for monolithic scanner

### Monitoring Tools
- `monitor_performance.sh` - Real-time performance monitoring for active scans
- `monitor_db_activity.sh` - Database activity monitor (writes per second)
- `monitor_detailed.sh` - Enhanced monitoring with I/O and process analysis

### Diagnostic Tools
- `diagnose_now.sh` - Quick diagnostic script for current scanner state
- `diagnose_now.sh.bak` - Backup version of diagnostic script
- `run_full_diagnostics.sh` - Comprehensive system diagnostics
- `Dockerfile.nas_diag` - Diagnostic container for system analysis

## Usage

### Performance Monitoring
```bash
# Real-time system stats during scanning
./monitor_performance.sh

# Database write activity
./monitor_db_activity.sh

# Detailed analysis (I/O, processes, mounts)
./monitor_detailed.sh
```

### Diagnostics
```bash
# Quick system check
./diagnose_now.sh

# Full diagnostic suite
./run_full_diagnostics.sh
```

### Setup (Monolithic Scanner)
```bash
# Complete setup for monolithic scanner
./setup.sh
```

## Monitoring Features

### Performance Monitor
- System load and uptime
- Container CPU/memory usage
- Disk I/O statistics
- Database operations per second

### Database Activity Monitor
- Files processed per second
- Time since last database write
- Recent file entries
- Write rate calculations

### Detailed Monitor
- I/O wait analysis
- Database lock detection
- Mount point accessibility
- Process state analysis
- Container log inspection

## Diagnostic Features

### Quick Diagnostics
- Container status
- I/O wait percentage
- Processes in I/O wait state
- Database accessibility
- Mount point health

### Full Diagnostics
- Complete system analysis
- Container resource usage
- Recent container logs
- Storage health checks
- Performance recommendations

## Container Diagnostics

The diagnostic container (`Dockerfile.nas_diag`) provides:
- System monitoring tools
- SQLite database access
- Network diagnostics
- Process analysis capabilities

Build and use:
```bash
docker build -f Dockerfile.nas_diag -t nas_diag .
docker run -it --rm nas_diag
```

## Database Monitoring

Both scanners can be monitored using these tools:

**Monolithic Database**: `/mnt/user/appdata/nas-scanner/scan_data/nas_catalog.db`
**Smart Database**: `/mnt/user/appdata/nas-scanner-smart/smart_catalog.db`

The monitoring scripts automatically detect and work with both database locations.

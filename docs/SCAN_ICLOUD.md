# Scanning iCloud Drive with Low Concurrency

The `icloud-drive-sync` mount uses FUSE and can't handle high-concurrency scanning due to cloud sync bottlenecks. This guide shows how to scan it separately with reduced workers.

## Wait for Main Scan to Complete

First, let your main parallel scan finish. Check progress with:
```bash
./monitor_performance.sh
./monitor_db_activity.sh
```

## Option 1: Manual Single Container (Recommended)

Run icloud scan with low concurrency after main scan completes:

```bash
docker run --rm \
  -v /mnt/user/appdata/nas-scanner/data:/data \
  -v /mnt/user:/mnt/user \
  nas-scanner-hp:latest \
  python nas_scanner_hp.py /mnt/user/icloud-drive-sync icloud-drive-sync \
  --db /data/nas_catalog.db --workers 2
```

## Option 2: Create Dedicated Script

Create a script for easier reuse:

```bash
#!/bin/bash
# scan_icloud.sh
docker run --rm \
  --cpus="4" --memory="8g" \
  -v /mnt/user/appdata/nas-scanner/data:/data \
  -v /mnt/user:/mnt/user \
  nas-scanner-hp:latest \
  python nas_scanner_hp.py /mnt/user/icloud-drive-sync icloud-drive-sync \
  --db /data/nas_catalog.db --workers 2
```

Make it executable:
```bash
chmod +x scan_icloud.sh
```

## Why Lower Concurrency Works

- **`--workers 2`**: Limits parallel directory processing to prevent FUSE overload
- **`--cpus="4"`**: Caps container CPU to avoid overwhelming cloud sync
- **`--memory="8g"`**: Reduces memory pressure vs full 24GB allocation

## Duplicate Handling

The scanner automatically prevents duplicates:
- Uses blake2b checksums for file-level deduplication
- Tracks completed directories to enable resume capability
- Identical files get same hash regardless of path

## Monitoring Progress

Monitor the icloud scan with:
```bash
docker logs <container_id> -f
```

Or check database growth:
```bash
./monitor_db_activity.sh
```

## Expected Performance

Expect much slower scanning (hundreds vs thousands of files/second) due to:
- Cloud sync latency
- FUSE filesystem overhead  
- Network round-trips for file metadata
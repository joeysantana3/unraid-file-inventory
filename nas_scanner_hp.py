#!/usr/bin/env python3
"""
High-Performance NAS Scanner - Simplified and Robust
"""

import os
import sys
import json
import hashlib
import sqlite3
import argparse
import time
import logging
from pathlib import Path
from datetime import datetime
from multiprocessing import Pool, cpu_count, Queue, Process
from concurrent.futures import ThreadPoolExecutor
import signal
import queue
from contextlib import contextmanager

# Configuration constants
DEFAULT_WORKERS = min(cpu_count(), 48)
DEFAULT_HASH_WORKERS = 16
BATCH_SIZE = 1000
QUEUE_SIZE = 5000
LARGE_FILE_THRESHOLD = 10 * 1024 * 1024  # 10MB
SAMPLE_SIZE = 64 * 1024  # 64KB

# File type mapping
FILE_TYPES = {
    '.jpg': 'photos', '.jpeg': 'photos', '.png': 'photos', '.gif': 'photos',
    '.mp4': 'videos', '.avi': 'videos', '.mkv': 'videos', '.mov': 'videos',
    '.mp3': 'music', '.flac': 'music', '.wav': 'music', '.m4a': 'music',
    '.pdf': 'documents', '.doc': 'documents', '.docx': 'documents', '.txt': 'documents',
    '.zip': 'archives', '.rar': 'archives', '.7z': 'archives', '.tar': 'archives',
    '.iso': 'disk_images', '.img': 'disk_images', '.dmg': 'disk_images'
}

def setup_logging():
    """Setup simple logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

# Use @contextmanager to ensure database connections are properly closed even if exceptions occur
@contextmanager
def database_connection(db_path):
    """Safe database connection with proper cleanup"""
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        # Safe performance settings (not synchronous=OFF)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')  # Safe but still fast
        conn.execute('PRAGMA cache_size=-64000')  # 64MB cache
        conn.execute('PRAGMA temp_store=MEMORY')
        # 'yield conn' provides the database connection to the context block,
        # allowing the caller to use 'with database_connection(...) as conn:'
        # and ensuring cleanup in the 'finally' block after the block exits.
        yield conn
    finally:
        conn.close()

class DatabaseManager:
    """Handles all database operations"""
    
    def __init__(self, db_path):
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)
        self._init_schema()
    
    def _init_schema(self):
        """Initialize database schema"""
        with database_connection(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    size INTEGER,
                    mtime REAL,
                    checksum TEXT,
                    mount_point TEXT,
                    file_type TEXT,
                    extension TEXT,
                    scan_time REAL
                ) WITHOUT ROWID
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS scan_stats (
                    mount_point TEXT PRIMARY KEY,
                    files_scanned INTEGER,
                    bytes_scanned INTEGER,
                    start_time REAL,
                    end_time REAL
                )
            ''')
            conn.commit()
    
    def save_files(self, file_batch):
        """Save a batch of files safely"""
        if not file_batch:
            return
            
        try:
            with database_connection(self.db_path) as conn:
                data = [(f['path'], f['size'], f['mtime'], f.get('checksum'), 
                        f['mount_point'], f.get('file_type', 'other'), 
                        f['extension'], f['scan_time']) 
                       for f in file_batch]
                
                # Use executemany to efficiently insert or update multiple file records in a single database transaction.
                # This reduces the number of round-trips to the database and improves performance for large batches.
                conn.executemany('''
                    INSERT OR REPLACE INTO files 
                    (path, size, mtime, checksum, mount_point, file_type, extension, scan_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', data)
                conn.commit()
                
        except Exception as e:
            self.logger.error(f"Database save failed: {e}")

def calculate_checksum(filepath, size):
    """Calculate file checksum efficiently"""
    try:
        # Alternatives to md5 include: sha1, sha256, sha512, blake2b, blake2s, and xxhash (if installed).
        # Pros/cons:
        # - md5: Fast, but weak security (collisions possible). Good for non-security file deduplication.
        # - sha1: Slightly slower, also weak for security, but fewer collisions than md5.
        # - sha256/sha512: Much stronger, but slower than md5/sha1.
        # - blake2b/blake2s: Very fast, strong, built-in since Python 3.6.
        # - xxhash: Extremely fast, non-cryptographic, good for checksumming large files, requires 'xxhash' package.
        # For high performance and reasonable uniqueness, blake2b is a good default.
        hash_obj = hashlib.blake2b()
        
        if size > LARGE_FILE_THRESHOLD:
            # Sample-based hashing for large files
            with open(filepath, 'rb') as f:
                # First sample
                hash_obj.update(f.read(SAMPLE_SIZE))
                # Middle sample
                f.seek(size // 2)
                hash_obj.update(f.read(SAMPLE_SIZE))
                # End sample
                f.seek(-SAMPLE_SIZE, 2)
                hash_obj.update(f.read())
        else:
            # Full hash for smaller files
            with open(filepath, 'rb') as f:
                hash_obj.update(f.read())
        
        return hash_obj.hexdigest()
    except Exception:
        return None

def categorize_file(filepath):
    """Categorize file by extension"""
    ext = Path(filepath).suffix.lower()
    return FILE_TYPES.get(ext, 'other')

def scan_directory(args):
    """Scan a single directory - designed for multiprocessing"""
    path, mount_name = args
    files = []
    
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    
                    file_info = {
                        'path': entry.path,
                        'size': stat.st_size,
                        'mtime': stat.st_mtime,
                        'mount_point': mount_name,
                        'extension': Path(entry.name).suffix.lower(),
                        'file_type': categorize_file(entry.path),
                        'scan_time': time.time()
                    }
                    
                    # Calculate checksum
                    if stat.st_size > 0:
                        file_info['checksum'] = calculate_checksum(entry.path, stat.st_size)
                    else:
                        file_info['checksum'] = 'empty'
                    
                    files.append(file_info)
                    
            except Exception as e:
                logging.getLogger(__name__).warning(f"Skipping file {entry.path}: {e}")
                
    except Exception as e:
        logging.getLogger(__name__).warning(f"Skipping directory {path}: {e}")
    
    return files

class Scanner:
    """Main scanner class - simplified and focused"""
    
    def __init__(self, db_path, num_workers=None):
        self.logger = setup_logging()
        self.db = DatabaseManager(db_path)
        self.num_workers = num_workers or DEFAULT_WORKERS
        self.shutdown = False
        
        # Stats
        self.files_scanned = 0
        self.bytes_scanned = 0
        self.start_time = time.time()
        
        # Setup signal handling
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info("Shutdown signal received")
        self.shutdown = True
    
    def _collect_directories(self, root_path):
        """Collect all directories to scan"""
        directories = []
        
        try:
            for root, dirs, files in os.walk(root_path):
                if self.shutdown:
                    break
                directories.append(root)
                
        except Exception as e:
            self.logger.error(f"Failed to walk directory tree: {e}")
            
        return directories
    
    def scan(self, mount_path, mount_name):
        """Main scan method - simplified approach"""
        if not os.path.exists(mount_path):
            self.logger.error(f"Path does not exist: {mount_path}")
            return
        
        self.start_time = time.time()
        self.logger.info(f"Starting scan of {mount_name} at {mount_path}")
        self.logger.info(f"Using {self.num_workers} workers")
        
        # Collect all directories first
        self.logger.info("Collecting directories...")
        directories = self._collect_directories(mount_path)
        
        if self.shutdown:
            return
            
        self.logger.info(f"Found {len(directories)} directories to scan")
        
        # Prepare work items
        work_items = [(d, mount_name) for d in directories]
        
        # Process with worker pool
        with Pool(processes=self.num_workers) as pool:
            try:
                results = pool.map(scan_directory, work_items)
                
                # Process results in batches
                batch = []
                for file_list in results:
                    if self.shutdown:
                        break
                        
                    batch.extend(file_list)
                    
                    if len(batch) >= BATCH_SIZE:
                        self._save_batch(batch)
                        batch = []
                
                # Save final batch
                if batch:
                    self._save_batch(batch)
                    
            except KeyboardInterrupt:
                self.logger.info("Scan interrupted by user")
                pool.terminate()
                pool.join()
        
        self._print_stats(mount_name)
    
    def _save_batch(self, batch):
        """Save batch and update stats"""
        self.db.save_files(batch)
        
        # Update stats
        self.files_scanned += len(batch)
        self.bytes_scanned += sum(f['size'] for f in batch)
        
        # Log progress
        elapsed = time.time() - self.start_time
        files_per_sec = self.files_scanned / elapsed if elapsed > 0 else 0
        mb_per_sec = (self.bytes_scanned / 1024 / 1024) / elapsed if elapsed > 0 else 0
        
        self.logger.info(f"Processed: {self.files_scanned:,} files "
                        f"({self.bytes_scanned/1024**3:.2f} GB) | "
                        f"{files_per_sec:.0f} files/sec | "
                        f"{mb_per_sec:.0f} MB/sec")
    
    def _print_stats(self, mount_name):
        """Print final statistics"""
        elapsed = time.time() - self.start_time
        
        print(f"\nScan Complete: {mount_name}")
        print("-" * 50)
        print(f"Files scanned: {self.files_scanned:,}")
        print(f"Total size: {self.bytes_scanned/1024**4:.2f} TB")
        print(f"Time: {elapsed/60:.1f} minutes")
        print(f"Rate: {self.files_scanned/elapsed:.0f} files/second")

def main():
    parser = argparse.ArgumentParser(description='Simple High-Performance NAS Scanner')
    parser.add_argument('mount_path', help='Path to scan')
    parser.add_argument('mount_name', help='Name for this mount')
    parser.add_argument('--db', default='/data/nas_catalog.db', help='Database path')
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS, help='Number of workers')
    
    args = parser.parse_args()
    
    # Validate inputs
    if not os.path.exists(args.mount_path):
        print(f"Error: Path {args.mount_path} does not exist")
        sys.exit(1)
    
    scanner = Scanner(args.db, args.workers)
    scanner.scan(args.mount_path, args.mount_name)

if __name__ == '__main__':
    main() 
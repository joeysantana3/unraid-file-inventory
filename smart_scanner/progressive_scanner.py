#!/usr/bin/env python3
"""
Progressive Smart Scanner - Start Immediately, Optimize Later

This scanner eliminates the long analysis phase by starting work immediately
and creating chunks progressively as it discovers the directory structure.
"""

import os
import sys
import time
import logging
import subprocess
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import signal
import queue
from typing import Dict, List, Optional, Tuple

# Configuration
MAX_CONTAINERS = 6  # Reduced for more conservative resource usage
CONTAINER_RESOURCES = {
    'cpus': '8',      # Reduced from 12
    'memory': '8g'    # Reduced from 12g
}

# Progressive chunking parameters
QUICK_SAMPLE_TIMEOUT = 30     # 30 seconds to sample a directory
MIN_CHUNK_SIZE_FILES = 1000   # Minimum files before considering a chunk
MAX_CHUNK_SIZE_FILES = 100000 # Maximum files per chunk
DEPTH_LIMIT = 3               # How deep to explore subdirectories

def setup_logging(log_file_path=None):
    """Setup logging with both console and file output"""
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    
    # Clear any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler if path provided
    if log_file_path:
        try:
            os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
            file_handler = logging.FileHandler(log_file_path, mode='a')
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            logger.info(f"Progressive scanner logging enabled: {log_file_path}")
        except Exception as e:
            logger.warning(f"Could not setup file logging: {e}")
    
    return logger

class QuickDirectoryAnalyzer:
    """Fast directory analyzer that samples rather than doing exhaustive analysis"""
    
    def __init__(self, logger):
        self.logger = logger
        self._sample_cache = {}
        self._lock = threading.Lock()
    
    def quick_sample_directory(self, path, timeout=QUICK_SAMPLE_TIMEOUT):
        """Quickly sample a directory to estimate its size and file count"""
        with self._lock:
            if path in self._sample_cache:
                return self._sample_cache[path]
        
        self.logger.info(f"Quick sampling: {path} (timeout: {timeout}s)")
        start_time = time.time()
        
        try:
            # Method 1: Quick file count with timeout
            result = subprocess.run(
                ['timeout', str(timeout), 'sh', '-c', f'find "{path}" -maxdepth 2 -type f | wc -l'],
                capture_output=True, text=True
            )
            
            if result.returncode == 0:
                file_count = int(result.stdout.strip())
                elapsed = time.time() - start_time
                
                # Estimate based on sample
                estimated_total = file_count * 10 if file_count > 1000 else file_count
                
                sample_info = {
                    'estimated_files': estimated_total,
                    'sample_files': file_count,
                    'sample_time': elapsed,
                    'method': 'quick_sample'
                }
                
                with self._lock:
                    self._sample_cache[path] = sample_info
                
                self.logger.info(f"Sample result: {path} - {file_count:,} files (depth ≤2), "
                               f"estimated ~{estimated_total:,} total files ({elapsed:.1f}s)")
                return sample_info
            
        except Exception as e:
            self.logger.warning(f"Quick sample failed for {path}: {e}")
        
        # Fallback: assume it's large
        fallback_info = {
            'estimated_files': 50000,
            'sample_files': 0,
            'sample_time': timeout,
            'method': 'fallback'
        }
        
        with self._lock:
            self._sample_cache[path] = fallback_info
        
        self.logger.warning(f"Using fallback estimate for {path}: ~50k files")
        return fallback_info

class ProgressiveChunkGenerator:
    """Generates chunks progressively without upfront analysis"""
    
    def __init__(self, logger, analyzer):
        self.logger = logger
        self.analyzer = analyzer
        self.processed_paths = set()
        self._lock = threading.Lock()
    
    def _is_toplevel_directory_complete(self, dir_path, mount_name, scanned_chunks):
        """
        Check if a top-level directory is TRULY complete (not just partially scanned).
        Only returns True if the exact directory path was marked as scanned AND
        it appears to be a complete scan (not just subdirectories within it).
        """
        # Method 1: Check if this exact path is in scanned_chunks and was likely scanned as a unit
        if dir_path in scanned_chunks:
            # Check if this path appears to be scanned as a complete unit
            # by seeing if there are very few or no subdirectories also marked as scanned
            subdirs_count = self._count_scanned_subdirectories(dir_path, scanned_chunks)
            
            # If there are many scanned subdirectories, this was likely a progressive scan
            # that got interrupted, so the top-level dir shouldn't be considered "complete"
            if subdirs_count > 10:  # Arbitrary threshold - if more than 10 subdirs are marked, 
                                   # it was likely progressive and incomplete
                self.logger.info(f"🔍 {dir_path} has {subdirs_count} scanned subdirs - likely incomplete progressive scan")
                return False
            
            # If few or no subdirs are scanned, it was likely scanned as a single unit
            self.logger.info(f"✅ {dir_path} appears to be a complete single-unit scan ({subdirs_count} subdirs)")
            return True
        
        return False
    
    def _count_scanned_subdirectories(self, parent_dir, scanned_chunks):
        """Count how many subdirectories of parent_dir are in scanned_chunks"""
        count = 0
        parent_dir_normalized = parent_dir.rstrip('/') + '/'
        
        for scanned_path in scanned_chunks:
            # Check if this scanned path is a subdirectory of parent_dir
            if scanned_path.startswith(parent_dir_normalized) and scanned_path != parent_dir:
                count += 1
        
        return count
    
    def generate_initial_chunks(self, root_path, mount_name, scanned_chunks=None):
        """Generate initial chunks to start scanning immediately, with SMART resume logic for top-level dirs"""
        self.logger.info(f"Generating initial chunks for: {root_path}")
        
        if scanned_chunks is None:
            scanned_chunks = set()
        
        chunks = []
        skipped_count = 0
        
        try:
            # Get top-level directories
            entries = os.listdir(root_path)
            directories = [os.path.join(root_path, entry) for entry in entries 
                          if os.path.isdir(os.path.join(root_path, entry)) and not entry.startswith('.')]
            
            if not directories:
                # No subdirectories - check if root is already scanned
                if root_path in scanned_chunks:
                    self.logger.info(f"⏭️  Root directory already scanned: {root_path}")
                    return []
                
                chunks.append({
                    'path': root_path,
                    'mount_name': mount_name,
                    'type': 'root',
                    'priority': 1,
                    'estimated_files': 0
                })
                self.logger.info(f"Created root chunk: {root_path}")
                return chunks
            
            self.logger.info(f"Found {len(directories)} top-level directories")
            
            # Create chunks for each top-level directory with SMART resume logic
            for i, dir_path in enumerate(directories):
                # FIXED: Smart check for top-level directories
                # Only skip if this EXACT path was processed as a TOP-LEVEL chunk, 
                # not if subdirectories within it were processed
                if self._is_toplevel_directory_complete(dir_path, mount_name, scanned_chunks):
                    self.logger.info(f"⏭️  Skipping fully completed top-level directory: {dir_path}")
                    skipped_count += 1
                    continue
                
                # Check if this directory has partial progress
                subdirs_scanned = self._count_scanned_subdirectories(dir_path, scanned_chunks)
                if subdirs_scanned > 0:
                    self.logger.info(f"🔄 RESUMING partially scanned directory: {dir_path} ({subdirs_scanned} subdirs already done)")
                    
                    # CONSERVATIVE APPROACH: For safety, always create the full parent chunk
                    # The worker scanner has built-in logic to skip already-scanned subdirectories
                    # This ensures we don't miss any files while still being reasonably efficient
                    self.logger.info(f"📋 Will create standard chunk for {dir_path} - worker will skip scanned subdirs")
                
                chunk = {
                    'path': dir_path,
                    'mount_name': mount_name,
                    'type': 'toplevel',
                    'priority': i + 1,
                    'estimated_files': 0
                }
                chunks.append(chunk)
                self.logger.info(f"✅ Created initial chunk {len(chunks)}: {dir_path}")
            
            # Mark all as processed (both new and skipped)
            with self._lock:
                self.processed_paths.update([root_path] + directories)
            
            if skipped_count > 0:
                self.logger.info(f"📊 Resume summary: {len(chunks)} new chunks, {skipped_count} fully completed")
            
            if chunks:
                self.logger.info(f"🚀 Generated {len(chunks)} initial chunks - scanning can start immediately!")
            else:
                self.logger.info("✅ All top-level directories fully completed for this mount!")
            
            return chunks
            
        except Exception as e:
            self.logger.error(f"Error generating initial chunks for {root_path}: {e}")
            # Emergency fallback
            if root_path not in scanned_chunks:
                return [{
                    'path': root_path,
                    'mount_name': mount_name,
                    'type': 'emergency',
                    'priority': 1,
                    'estimated_files': 0
                }]
            else:
                return []
    
    def generate_adaptive_chunks(self, root_path, mount_name, scanned_chunks=None, max_chunks=10):
        """Generate additional chunks based on discovered directory structure, skipping already scanned ones"""
        self.logger.info(f"Generating adaptive chunks for large directories in: {root_path}")
        
        if scanned_chunks is None:
            scanned_chunks = set()
        
        new_chunks = []
        skipped_count = 0
        
        try:
            # Look for large subdirectories that might benefit from chunking
            for root, dirs, files in os.walk(root_path):
                # Don't go too deep
                depth = root.replace(root_path, '').count(os.sep)
                if depth >= DEPTH_LIMIT:
                    dirs.clear()  # Don't recurse deeper
                    continue
                
                # FIXED: Skip if already FULLY scanned (not just partially)
                if root in scanned_chunks:
                    # Check if this directory was truly completed or just partially scanned
                    subdirs_count = self._count_scanned_subdirectories(root, scanned_chunks)
                    if subdirs_count <= 5:  # If few subdirs, it was likely a complete scan
                        skipped_count += 1
                        continue
                    else:
                        # Many subdirs scanned - this was likely incomplete, so re-scan it
                        self.logger.info(f"🔄 Re-scanning likely incomplete directory: {root} ({subdirs_count} subdirs found)")
                        
                        # Remove from scanned_chunks to allow re-processing
                        scanned_chunks.discard(root)
                
                # Skip if already processed in this session
                with self._lock:
                    if root in self.processed_paths:
                        continue
                
                # Quick sample this directory
                try:
                    sample = self.analyzer.quick_sample_directory(root, timeout=15)  # Shorter timeout
                    
                    # If it looks large enough, create a chunk
                    if sample['estimated_files'] > MIN_CHUNK_SIZE_FILES:
                        chunk = {
                            'path': root,
                            'mount_name': mount_name,
                            'type': 'adaptive',
                            'priority': 10 + len(new_chunks),
                            'estimated_files': sample['estimated_files']
                        }
                        new_chunks.append(chunk)
                        
                        with self._lock:
                            self.processed_paths.add(root)
                        
                        self.logger.info(f"Created adaptive chunk: {root} (~{sample['estimated_files']:,} files)")
                        
                        # Don't create too many at once
                        if len(new_chunks) >= max_chunks:
                            break
                            
                except Exception as e:
                    self.logger.warning(f"Error sampling {root}: {e}")
                    continue
            
            if skipped_count > 0:
                self.logger.info(f"Generated {len(new_chunks)} adaptive chunks, skipped {skipped_count} already scanned")
            else:
                self.logger.info(f"Generated {len(new_chunks)} adaptive chunks")
            return new_chunks
            
        except Exception as e:
            self.logger.error(f"Error generating adaptive chunks: {e}")
            return []

class ProgressiveScanner:
    """Progressive scanner that starts immediately and optimizes as it goes"""
    
    def __init__(self, db_path, image_name='nas-scanner-hp:latest'):
        # Setup logging
        log_dir = os.path.dirname(db_path)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = os.path.join(log_dir, f'progressive_scan_{timestamp}.log')
        self.logger = setup_logging(log_file)
        
        self.db_path = db_path
        self.image_name = image_name
        self.analyzer = QuickDirectoryAnalyzer(self.logger)
        self.chunk_generator = ProgressiveChunkGenerator(self.logger, self.analyzer)
        
        # State tracking
        self.active_containers = {}
        self.completed_chunks = 0
        self.failed_chunks = 0
        self._shutdown_requested = False
        self.scan_start_time = None
        
        # Work queues
        self.pending_chunks = queue.PriorityQueue()
        self.chunk_generation_active = False
        
        # Resume capability
        self.database_exists = os.path.exists(db_path)
        self.scanned_chunks = set()  # Will be populated from database
        
        self.logger.info("=" * 60)
        self.logger.info("PROGRESSIVE SCANNER INITIALIZATION")
        self.logger.info(f"Database: {db_path}")
        self.logger.info(f"Database exists: {self.database_exists}")
        self.logger.info(f"Docker image: {image_name}")
        self.logger.info(f"Max containers: {MAX_CONTAINERS}")
        self.logger.info(f"Container resources: {CONTAINER_RESOURCES}")
        self.logger.info(f"Strategy: Start immediately, optimize progressively")
        self.logger.info("=" * 60)
    
    def create_scan_database(self):
        """Create database schema"""
        import sqlite3
        db_dir = os.path.dirname(self.db_path)
        os.makedirs(db_dir, exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
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
        conn.execute('''
            CREATE TABLE IF NOT EXISTS scanned_dirs (
                path TEXT PRIMARY KEY,
                mount_point TEXT,
                scan_time REAL
            ) WITHOUT ROWID
        ''')
        conn.commit()
        conn.close()
        self.logger.info("Database schema ready")
    
    def get_scanned_chunks(self, mount_name):
        """Get set of directories already scanned for a mount point"""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()
            cursor.execute('SELECT path FROM scanned_dirs WHERE mount_point = ?', (mount_name,))
            scanned_paths = {row[0] for row in cursor.fetchall()}
            conn.close()
            return scanned_paths
        except Exception as e:
            self.logger.warning(f"Could not fetch scanned chunks: {e}")
            return set()
    
    def mark_chunk_scanned(self, chunk_path, mount_name):
        """Mark a chunk as successfully scanned"""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO scanned_dirs (path, mount_point, scan_time) VALUES (?, ?, ?)',
                (chunk_path, mount_name, time.time())
            )
            conn.commit()
            conn.close()
            self.logger.info(f"✓ Marked chunk as scanned: {chunk_path}")
        except Exception as e:
            self.logger.error(f"Could not mark chunk as scanned {chunk_path}: {e}")
    
    def check_existing_data(self, mount_name):
        """Check for existing scan data and provide resume information"""
        if not self.database_exists:
            self.logger.info("🆕 New scan - no existing database found")
            return
        
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()
            
            # Get total files for this mount
            cursor.execute('SELECT COUNT(*) FROM files WHERE mount_point = ?', (mount_name,))
            existing_files = cursor.fetchone()[0]
            
            # Get scanned directories for this mount
            cursor.execute('SELECT COUNT(*) FROM scanned_dirs WHERE mount_point = ?', (mount_name,))
            scanned_dirs = cursor.fetchone()[0]
            
            # Get last scan time
            cursor.execute('SELECT MAX(scan_time) FROM files WHERE mount_point = ?', (mount_name,))
            last_scan_time = cursor.fetchone()[0]
            
            conn.close()
            
            if scanned_dirs > 0 or existing_files > 0:
                self.logger.info("🔄 RESUME MODE DETECTED")
                self.logger.info(f"   Existing files for {mount_name}: {existing_files:,}")
                self.logger.info(f"   Scanned directories: {scanned_dirs:,}")
                if last_scan_time:
                    last_scan_dt = datetime.fromtimestamp(last_scan_time)
                    self.logger.info(f"   Last scan activity: {last_scan_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                self.logger.info("   Will skip already completed chunks")
                
                # Populate scanned chunks for filtering
                self.scanned_chunks = self.get_scanned_chunks(mount_name)
                self.logger.info(f"   Loaded {len(self.scanned_chunks)} completed chunks to skip")
            else:
                self.logger.info("📊 Database exists but no previous data for this mount - starting fresh")
                
        except Exception as e:
            self.logger.warning(f"Could not check existing data: {e}")
            self.logger.info("Proceeding with fresh scan")
    
    def start_container(self, chunk):
        """Start a container for a chunk"""
        chunk_name = chunk['path'].replace('/', '_').replace(' ', '_')
        container_name = f"progressive-scan-{chunk_name[-40:]}"
        container_name = ''.join(c for c in container_name if c.isalnum() or c in '-_')
        
        # Pre-flight checks
        if not os.path.exists(chunk['path']):
            self.logger.error(f"Chunk path missing: {chunk['path']}")
            return None
        
        try:
            os.listdir(chunk['path'])
        except Exception as e:
            self.logger.error(f"Cannot access chunk: {chunk['path']} - {e}")
            return None
        
        db_dir = os.path.dirname(self.db_path)
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--rm',
            '-v', f"{chunk['path']}:{chunk['path']}:ro",
            '-v', f"{db_dir}:/data",
            '--cpus', CONTAINER_RESOURCES['cpus'],
            '--memory', CONTAINER_RESOURCES['memory'],
            '--ulimit', 'nofile=65536:65536',
            self.image_name,
            'python', 'nas_scanner_hp.py',
            chunk['path'],
            chunk['mount_name'],
            '--db', '/data/' + os.path.basename(self.db_path),
            '--workers', '8'  # Reduced workers per container
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
            container_id = result.stdout.strip()
            
            if container_id:
                self.active_containers[container_id] = {
                    'name': container_name,
                    'chunk': chunk,
                    'start_time': time.time()
                }
                self.logger.info(f"✓ Started container {container_name} for {chunk['path']} "
                               f"(~{chunk.get('estimated_files', 0):,} files)")
                return container_id
        
        except Exception as e:
            self.logger.error(f"Failed to start container for {chunk['path']}: {e}")
        
        return None
    
    def scan_mount_point(self, mount_path, mount_name):
        """Main progressive scanning method"""
        self.scan_start_time = time.time()
        
        self.logger.info("=" * 60)
        self.logger.info("PROGRESSIVE SCAN STARTING")
        self.logger.info(f"Mount: {mount_name} ({mount_path})")
        self.logger.info(f"Strategy: Start scanning immediately, no upfront analysis")
        self.logger.info(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info("=" * 60)
        
        # Quick validation
        if not os.path.exists(mount_path):
            raise Exception(f"Mount path does not exist: {mount_path}")
        
        try:
            os.listdir(mount_path)
        except Exception as e:
            raise Exception(f"Cannot access mount path: {e}")
        
        # Create database
        self.create_scan_database()
        
        # Check for existing data and resume capability
        self.check_existing_data(mount_name)
        
        # Generate initial chunks immediately - NO ANALYSIS PHASE
        self.logger.info("🚀 Generating initial chunks - NO WAITING!")
        initial_chunks = self.chunk_generator.generate_initial_chunks(mount_path, mount_name, self.scanned_chunks)
        
        # Queue initial chunks
        for chunk in initial_chunks:
            self.pending_chunks.put((chunk['priority'], chunk))
        
        self.logger.info(f"✅ {len(initial_chunks)} chunks ready - STARTING SCAN NOW!")
        
        # Start processing chunks immediately
        with ThreadPoolExecutor(max_workers=MAX_CONTAINERS + 2) as executor:
            # Submit chunk processors
            futures = []
            for i in range(MAX_CONTAINERS):
                future = executor.submit(self._chunk_processor_worker)
                futures.append(future)
            
            # Submit adaptive chunk generator (runs in background)
            adaptive_future = executor.submit(self._adaptive_chunk_generator, mount_path, mount_name)
            futures.append(adaptive_future)
            
            # Wait for completion
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Worker error: {e}")
        
        # Final statistics
        elapsed = time.time() - self.scan_start_time
        self.logger.info("=" * 60)
        self.logger.info("PROGRESSIVE SCAN COMPLETE")
        self.logger.info(f"Total time: {elapsed/60:.1f} minutes")
        self.logger.info(f"Completed chunks: {self.completed_chunks}")
        self.logger.info(f"Failed chunks: {self.failed_chunks}")
        self.logger.info("=" * 60)
    
    def _chunk_processor_worker(self):
        """Worker that processes chunks from the queue"""
        while not self._shutdown_requested:
            try:
                # Get next chunk (with timeout to allow shutdown)
                try:
                    priority, chunk = self.pending_chunks.get(timeout=10)
                except queue.Empty:
                    continue
                
                # Process this chunk
                container_id = self.start_container(chunk)
                if not container_id:
                    self.failed_chunks += 1
                    self.pending_chunks.task_done()
                    continue
                
                # Wait for completion
                self._wait_for_container(container_id)
                self.pending_chunks.task_done()
                
            except Exception as e:
                self.logger.error(f"Chunk processor error: {e}")
                break
    
    def _wait_for_container(self, container_id):
        """Wait for a specific container to complete"""
        while not self._shutdown_requested:
            try:
                # Check if container is still running
                result = subprocess.run(
                    ['docker', 'ps', '-q', '--filter', f'id={container_id}'],
                    capture_output=True, text=True
                )
                
                if not result.stdout.strip():
                    # Container finished
                    info = self.active_containers.get(container_id, {})
                    chunk = info.get('chunk', {})
                    
                    # Check exit code
                    exit_result = subprocess.run(
                        ['docker', 'inspect', container_id, '--format', '{{.State.ExitCode}}'],
                        capture_output=True, text=True
                    )
                    
                    exit_code = int(exit_result.stdout.strip()) if exit_result.stdout.strip() else 1
                    
                    if exit_code == 0:
                        self.completed_chunks += 1
                        chunk_path = chunk.get('path', 'unknown')
                        mount_name = chunk.get('mount_name', 'unknown')
                        self.logger.info(f"✓ [{self.completed_chunks}] Completed: {chunk_path}")
                        
                        # Mark chunk as scanned for resume capability
                        if chunk_path != 'unknown' and mount_name != 'unknown':
                            self.mark_chunk_scanned(chunk_path, mount_name)
                            # Add to current session's scanned set
                            self.scanned_chunks.add(chunk_path)
                    else:
                        self.failed_chunks += 1
                        self.logger.error(f"✗ [{self.failed_chunks}] Failed: {chunk.get('path', 'unknown')} (exit: {exit_code})")
                    
                    # Clean up
                    if container_id in self.active_containers:
                        del self.active_containers[container_id]
                    
                    break
                
                time.sleep(5)  # Check every 5 seconds
                
            except Exception as e:
                self.logger.error(f"Error monitoring container {container_id}: {e}")
                break
    
    def _adaptive_chunk_generator(self, mount_path, mount_name):
        """Background worker that generates additional chunks as needed"""
        self.logger.info("🔄 Adaptive chunk generator started")
        
        # Wait a bit for initial scanning to start
        time.sleep(60)
        
        while not self._shutdown_requested and (self.active_containers or not self.pending_chunks.empty()):
            try:
                # Generate more chunks if queue is getting low
                if self.pending_chunks.qsize() < 2:
                    self.logger.info("Queue low - generating more adaptive chunks...")
                    new_chunks = self.chunk_generator.generate_adaptive_chunks(mount_path, mount_name, self.scanned_chunks, max_chunks=5)
                    
                    for chunk in new_chunks:
                        self.pending_chunks.put((chunk['priority'], chunk))
                    
                    if new_chunks:
                        self.logger.info(f"Added {len(new_chunks)} adaptive chunks to queue")
                
                time.sleep(120)  # Check every 2 minutes
                
            except Exception as e:
                self.logger.error(f"Adaptive chunk generator error: {e}")
                break
        
        self.logger.info("Adaptive chunk generator finished")

def main():
    parser = argparse.ArgumentParser(description='Progressive Smart NAS Scanner - Start Immediately')
    parser.add_argument('mount_path', help='Path to mount point to scan')
    parser.add_argument('mount_name', help='Name of the mount point')
    parser.add_argument('--db', required=True, help='Database path')
    parser.add_argument('--max-containers', type=int, default=6, help='Maximum concurrent containers')
    parser.add_argument('--image', default='nas-scanner-hp:latest', help='Docker image to use')
    
    args = parser.parse_args()
    
    global MAX_CONTAINERS
    MAX_CONTAINERS = args.max_containers
    
    scanner = ProgressiveScanner(args.db, args.image)
    scanner.scan_mount_point(args.mount_path, args.mount_name)

if __name__ == '__main__':
    main() 
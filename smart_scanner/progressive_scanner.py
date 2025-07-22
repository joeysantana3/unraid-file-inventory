#!/usr/bin/env python3
"""
Progressive Smart NAS Scanner - Fixed Version with Bug Fixes and Improvements

Fixes:
- Database schema creation
- Race condition in scanned chunks tracking
- Container name collision prevention
- Efficient container status checking
- Better error handling and resource management
- Input sanitization
- Memory leak prevention
"""

import os
import sys
import time
import logging
import subprocess
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import sqlite3
import queue
import json
import uuid
import re
from datetime import datetime

MAX_CONTAINERS = 6
MAX_RETRIES = 3
BATCH_SIZE = 100
DB_TIMEOUT = 60  # Increased from 30

class ContainerManager:
    """Manages Docker container lifecycle and limits"""
    
    def __init__(self, max_containers=MAX_CONTAINERS, host_db_dir=None):
        self.max_containers = max_containers
        self.running_containers = {}
        self.container_lock = threading.Lock()
        self.semaphore = threading.Semaphore(max_containers)
        self.host_db_dir = host_db_dir
        self._check_system_resources()
        
    def _check_system_resources(self):
        """Check if system has enough resources for requested containers"""
        try:
            # Check CPU count
            cpu_count = os.cpu_count() or 1
            requested_cpus = self.max_containers * 8
            if requested_cpus > cpu_count:
                logging.warning(f"Requested {requested_cpus} CPUs but system has {cpu_count}")
                
            # Check available memory
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('MemAvailable:'):
                        available_kb = int(line.split()[1])
                        available_gb = available_kb / (1024 * 1024)
                        requested_gb = self.max_containers * 8
                        if requested_gb > available_gb * 0.8:  # Don't use more than 80%
                            logging.warning(f"Requested {requested_gb}GB RAM but only {available_gb:.1f}GB available")
                        break
        except:
            # If we can't check, proceed anyway but log it
            logging.info("Could not check system resources")
    
    def _sanitize_container_name(self, path):
        """Safely generate container name from path"""
        # First sanitize the path before using it
        safe_path = re.sub(r'[^a-zA-Z0-9/_-]', '_', path)
        # Use last 20 chars of path + unique ID
        path_suffix = safe_path[-20:] if len(safe_path) > 20 else safe_path
        unique_id = uuid.uuid4().hex[:8]
        return f"progressive-scan-{path_suffix}-{unique_id}"
        
    def start_container(self, chunk, db_path, image_name, logger):
        """Start a container with proper resource management"""
        self.semaphore.acquire()
        
        try:
            container_name = self._sanitize_container_name(chunk['path'])
            
            if not os.path.exists(chunk['path']):
                logger.error(f"Path does not exist: {chunk['path']}")
                return None
                
            if is_empty_directory(chunk['path']):
                logger.info(f"Skipping empty directory: {chunk['path']}")
                return None
            
            # Determine mount path for database
            if self.host_db_dir:
                db_mount_source = self.host_db_dir
                logger.debug(f"Using host database directory: {db_mount_source}")
            else:
                db_mount_source = os.path.dirname(db_path)
                logger.debug(f"Using container database directory: {db_mount_source}")
                
            cmd = [
                'docker', 'run', '-d', '--name', container_name, '--rm',
                '-v', f"{chunk['path']}:{chunk['path']}:ro",
                '-v', f"{db_mount_source}:/data",
                '--cpus', '8', '--memory', '8g',
                image_name, 'python', 'nas_scanner_hp.py',
                chunk['path'], chunk['mount_name'],
                '--db', f"/data/{os.path.basename(db_path)}",
                '--workers', '8'
            ]
            
            logger.info(f"Starting container: {container_name}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.error(f"Failed to start container: {result.stderr}")
                return None
                
            with self.container_lock:
                self.running_containers[container_name] = {
                    'chunk': chunk,
                    'start_time': time.time(),
                    'retries': 0
                }
                
            logger.info(f"Started container {container_name} for {chunk['path']}")
            return container_name
            
        except Exception as e:
            logger.error(f"Exception starting container: {e}")
            return None
        finally:
            self.semaphore.release()
            
    def get_all_container_statuses(self):
        """Efficiently get status of all containers at once"""
        try:
            cmd = ['docker', 'ps', '-a', '--filter', 'name=progressive-scan', 
                   '--format', '{{.Names}}|{{.Status}}|{{.State}}']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                return {}
                
            statuses = {}
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split('|')
                    if len(parts) >= 3:
                        name, status, state = parts[0], parts[1], parts[2]
                        statuses[name] = {'status': status, 'running': state == 'running'}
                        
            return statuses
        except Exception as e:
            logging.error(f"Error getting container statuses: {e}")
            return {}
            
    def wait_for_container(self, container_name, logger, timeout=3600):
        """Wait for a specific container to complete"""
        try:
            cmd = ['docker', 'wait', container_name]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            
            # Safely parse exit code
            exit_code = 1  # Default to failure
            if result.stdout.strip().isdigit():
                exit_code = int(result.stdout.strip())
            
            with self.container_lock:
                if container_name in self.running_containers:
                    chunk = self.running_containers[container_name]['chunk']
                    del self.running_containers[container_name]
                else:
                    chunk = None
                    
            if exit_code == 0:
                logger.info(f"Container {container_name} completed successfully")
                return True, chunk
            else:
                logger.error(f"Container {container_name} failed with exit code {exit_code}")
                return False, chunk
                
        except subprocess.TimeoutExpired:
            logger.error(f"Container {container_name} timed out")
            self.stop_container(container_name, logger)
            return False, None
        except Exception as e:
            logger.error(f"Error waiting for container {container_name}: {e}")
            return False, None
            
    def stop_container(self, container_name, logger):
        """Forcefully stop a container"""
        try:
            subprocess.run(['docker', 'stop', container_name], capture_output=True, timeout=30)
            logger.info(f"Stopped container {container_name}")
            
            with self.container_lock:
                if container_name in self.running_containers:
                    del self.running_containers[container_name]
                    
        except Exception as e:
            logger.error(f"Error stopping container {container_name}: {e}")
            
    def get_running_count(self):
        """Get count of currently running containers"""
        with self.container_lock:
            return len(self.running_containers)

class DatabaseManager:
    """Thread-safe database operations"""
    
    def __init__(self, db_path):
        self.db_path = db_path
        self.batch_lock = threading.Lock()
        self.completed_chunks_batch = []
        self._create_schema()
        
    def _create_schema(self):
        """Create database schema if it doesn't exist"""
        try:
            with sqlite3.connect(self.db_path, timeout=DB_TIMEOUT) as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS scanned_dirs 
                               (path TEXT PRIMARY KEY, 
                                mount_point TEXT, 
                                scan_time REAL,
                                files_count INTEGER DEFAULT 0,
                                total_size INTEGER DEFAULT 0)''')
                
                conn.execute('''CREATE TABLE IF NOT EXISTS files 
                               (path TEXT, 
                                mount_point TEXT, 
                                size INTEGER, 
                                mtime REAL,
                                scan_time REAL,
                                PRIMARY KEY (path, mount_point))''')
                
                conn.execute('''CREATE INDEX IF NOT EXISTS idx_mount_point 
                               ON files(mount_point)''')
                
                conn.execute('''CREATE INDEX IF NOT EXISTS idx_scan_time 
                               ON files(scan_time)''')
                
                conn.commit()
                logging.info("Database schema created/verified")
        except Exception as e:
            logging.error(f"Error creating database schema: {e}")
            raise
            
    def mark_chunk_scanned(self, chunk_path, mount_name, files_count=0, total_size=0):
        """Mark a chunk as scanned with metadata"""
        with self.batch_lock:
            self.completed_chunks_batch.append((chunk_path, mount_name, time.time(), files_count, total_size))
            
            if len(self.completed_chunks_batch) >= BATCH_SIZE:
                self._flush_completed_chunks()
                
    def _flush_completed_chunks(self):
        """Flush batch - must be called with lock held"""
        if not self.completed_chunks_batch:
            return
            
        try:
            with sqlite3.connect(self.db_path, timeout=DB_TIMEOUT) as conn:
                cursor = conn.cursor()
                cursor.executemany(
                    '''INSERT OR REPLACE INTO scanned_dirs 
                       (path, mount_point, scan_time, files_count, total_size) 
                       VALUES (?, ?, ?, ?, ?)''',
                    self.completed_chunks_batch
                )
                conn.commit()
                logging.info(f"Flushed {len(self.completed_chunks_batch)} completed chunks")
                self.completed_chunks_batch.clear()
        except Exception as e:
            logging.error(f"Error flushing chunks: {e}")
            
    def flush_remaining(self):
        """Flush any remaining batched chunks"""
        with self.batch_lock:
            self._flush_completed_chunks()
            
    def load_scanned_chunks(self, mount_name=None):
        """Load previously scanned chunks"""
        try:
            with sqlite3.connect(self.db_path, timeout=DB_TIMEOUT) as conn:
                cursor = conn.cursor()
                if mount_name:
                    cursor.execute('SELECT path FROM scanned_dirs WHERE mount_point = ?', (mount_name,))
                else:
                    cursor.execute('SELECT path FROM scanned_dirs')
                return {row[0] for row in cursor.fetchall()}
        except Exception as e:
            logging.error(f"Error loading scanned chunks: {e}")
            return set()
            
    def get_scan_stats(self, mount_name):
        """Get scanning statistics"""
        try:
            with sqlite3.connect(self.db_path, timeout=DB_TIMEOUT) as conn:
                cursor = conn.cursor()
                cursor.execute('''SELECT COUNT(*), SUM(files_count), SUM(total_size) 
                                 FROM scanned_dirs WHERE mount_point = ?''', (mount_name,))
                chunks, files, size = cursor.fetchone()
                return {
                    'chunks_scanned': chunks or 0,
                    'files_counted': files or 0,
                    'total_size': size or 0
                }
        except Exception as e:
            logging.error(f"Error getting stats: {e}")
            return {'chunks_scanned': 0, 'files_counted': 0, 'total_size': 0}

def detect_container_environment():
    """Detect if running in Docker and determine host paths"""
    if os.path.exists('/.dockerenv'):
        host_db_dir = os.environ.get('HOST_DB_DIR')
        if host_db_dir:
            return host_db_dir
        
        if os.path.exists('/data') and os.access('/data', os.W_OK):
            return '/mnt/user/appdata/nas-scanner'
    
    return None

def setup_logging(log_file_path=None):
    """Setup logging configuration"""
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    
    # Remove any existing handlers
    logger.handlers = []

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s', 
        '%Y-%m-%d %H:%M:%S'
    )
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file_path:
        os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
        file_handler = logging.FileHandler(log_file_path, mode='a')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger

def is_empty_directory(path):
    """Check if directory is empty or inaccessible"""
    try:
        # Use next() with default to avoid StopIteration
        return next(os.scandir(path), None) is None
    except (OSError, PermissionError):
        return True

def generate_initial_chunks(mount_path, mount_name, scanned_chunks, logger):
    """Generate initial chunks with consistent directory listing"""
    chunks = []
    
    try:
        # Single directory scan
        entries = list(os.scandir(mount_path))
        subdirs = [entry.path for entry in entries if entry.is_dir()]
        
        # Process in parallel but with controlled concurrency
        def check_dir(subdir):
            if subdir in scanned_chunks:
                logger.info(f"Skipping already scanned: {subdir}")
                return None
            if is_empty_directory(subdir):
                logger.info(f"Skipping empty directory: {subdir}")
                return None
            return {'path': subdir, 'mount_name': mount_name, 'priority': 1}
        
        with ThreadPoolExecutor(max_workers=min(8, len(subdirs))) as executor:
            futures = [executor.submit(check_dir, subdir) for subdir in subdirs]
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        chunks.append(result)
                except Exception as e:
                    logger.error(f"Error processing directory: {e}")
        
        logger.info(f"Generated {len(chunks)} initial chunks from {len(subdirs)} directories")
        return chunks, subdirs  # Return subdirs for pending_paths
        
    except (OSError, PermissionError) as e:
        logger.error(f"Cannot access mount path {mount_path}: {e}")
        return [], []

def generate_adaptive_chunks(pending_paths, mount_name, scanned_chunks, logger, max_chunks=5):
    """Generate adaptive chunks without race condition"""
    new_chunks = []
    
    # Use a copy to avoid modifying during iteration
    paths_to_check = list(pending_paths[:max_chunks * 2])  # Check more than needed
    
    for path in paths_to_check:
        if len(new_chunks) >= max_chunks:
            break
            
        if path not in scanned_chunks and not is_empty_directory(path):
            new_chunks.append({'path': path, 'mount_name': mount_name, 'priority': 1})
            # Don't add to scanned_chunks here - wait for actual completion
            pending_paths.remove(path)
            logger.info(f"Adaptive chunk created: {path}")
        else:
            pending_paths.remove(path)
            logger.debug(f"Skipped: {path} (empty or already scanned)")
    
    logger.info(f"Generated {len(new_chunks)} adaptive chunks")
    return new_chunks

def main():
    parser = argparse.ArgumentParser(description='Progressive NAS Scanner with Bug Fixes')
    parser.add_argument('mount_path', help='Path to mount point to scan')
    parser.add_argument('mount_name', help='Name identifier for the mount')
    parser.add_argument('--db', required=True, help='Path to database file')
    parser.add_argument('--image', default='nas-scanner-hp:latest', help='Docker image to use')
    parser.add_argument('--max-containers', type=int, default=MAX_CONTAINERS, help='Maximum concurrent containers')
    parser.add_argument('--log-file', help='Log file path')
    parser.add_argument('--host-db-dir', help='Host database directory path')
    args = parser.parse_args()

    # Setup logging
    log_path = args.log_file or os.path.join(os.path.dirname(args.db), 'progressive_scan.log')
    logger = setup_logging(log_path)
    
    # Detect environment
    detected_host_db_dir = detect_container_environment()
    host_db_dir = args.host_db_dir or detected_host_db_dir
    
    if host_db_dir:
        logger.info(f"Docker-in-Docker mode: Host DB directory: {host_db_dir}")
    
    logger.info(f"Starting progressive scan of {args.mount_path}")
    logger.info(f"Configuration: max_containers={args.max_containers}, db={args.db}")

    # Initialize managers
    container_manager = ContainerManager(args.max_containers, host_db_dir)
    db_manager = DatabaseManager(args.db)
    
    # Load previously scanned chunks for this mount
    scanned_chunks = db_manager.load_scanned_chunks(args.mount_name)
    logger.info(f"Loaded {len(scanned_chunks)} previously scanned chunks")
    
    # Show statistics
    stats = db_manager.get_scan_stats(args.mount_name)
    logger.info(f"Previous scan stats: {stats['chunks_scanned']} chunks, "
                f"{stats['files_counted']:,} files, "
                f"{stats['total_size'] / (1024**3):.2f} GB")

    try:
        # Generate initial chunks and get all subdirs
        chunks, all_subdirs = generate_initial_chunks(args.mount_path, args.mount_name, scanned_chunks, logger)
        
        # Initialize pending paths (excluding already processed)
        pending_paths = [d for d in all_subdirs if d not in scanned_chunks and d not in [c['path'] for c in chunks]]
        logger.info(f"Initial chunks: {len(chunks)}, Pending paths: {len(pending_paths)}")
        
        # Track containers and failures
        active_containers = {}
        failed_chunks = []  # List of (chunk, retry_count)
        completed_count = 0
        
        # Start initial containers
        for chunk in chunks:
            container_name = container_manager.start_container(chunk, args.db, args.image, logger)
            if container_name:
                active_containers[container_name] = chunk
            else:
                failed_chunks.append((chunk, 0))
        
        # Main processing loop
        last_status_time = time.time()
        last_status_check = time.time()
        
        while active_containers or pending_paths or failed_chunks:
            current_time = time.time()
            
            # Periodic status logging
            if current_time - last_status_time > 30:
                logger.info(f"Progress: {completed_count} completed, "
                           f"{len(active_containers)} active, "
                           f"{len(pending_paths)} pending, "
                           f"{len(failed_chunks)} failed")
                last_status_time = current_time
            
            # Check container statuses efficiently
            if current_time - last_status_check > 5:  # Check every 5 seconds
                statuses = container_manager.get_all_container_statuses()
                last_status_check = current_time
                
                completed_containers = []
                for container_name, chunk in list(active_containers.items()):
                    if container_name in statuses and not statuses[container_name]['running']:
                        # Container finished
                        success, _ = container_manager.wait_for_container(container_name, logger, timeout=1)
                        completed_containers.append(container_name)
                        
                        if success:
                            db_manager.mark_chunk_scanned(chunk['path'], chunk['mount_name'])
                            scanned_chunks.add(chunk['path'])  # Now safe to add
                            completed_count += 1
                            logger.info(f"Completed: {chunk['path']} ({completed_count} total)")
                        else:
                            if len(failed_chunks) < 100:  # Prevent unbounded growth
                                failed_chunks.append((chunk, 0))
                            logger.warning(f"Failed: {chunk['path']}")
                
                # Remove completed containers
                for container_name in completed_containers:
                    del active_containers[container_name]
            
            # Start new containers if capacity available
            capacity = args.max_containers - len(active_containers)
            
            if capacity > 0:
                # Priority 1: Retry failed chunks
                while failed_chunks and capacity > 0:
                    chunk, retries = failed_chunks.pop(0)
                    if retries < MAX_RETRIES:
                        wait_time = min(2 ** retries, 60)  # Cap at 60 seconds
                        if retries > 0:
                            logger.info(f"Retrying {chunk['path']} (attempt {retries + 1}/{MAX_RETRIES}) "
                                       f"after {wait_time}s")
                            time.sleep(wait_time)
                        
                        container_name = container_manager.start_container(chunk, args.db, args.image, logger)
                        if container_name:
                            active_containers[container_name] = chunk
                            capacity -= 1
                        else:
                            failed_chunks.append((chunk, retries + 1))
                    else:
                        logger.error(f"Giving up on {chunk['path']} after {MAX_RETRIES} attempts")
                
                # Priority 2: New adaptive chunks
                if pending_paths and capacity > 0:
                    adaptive_chunks = generate_adaptive_chunks(
                        pending_paths, args.mount_name, scanned_chunks, logger, 
                        max_chunks=capacity
                    )
                    
                    for chunk in adaptive_chunks:
                        container_name = container_manager.start_container(chunk, args.db, args.image, logger)
                        if container_name:
                            active_containers[container_name] = chunk
                        else:
                            failed_chunks.append((chunk, 0))
            
            # Brief pause to prevent CPU spinning
            time.sleep(1)
        
        logger.info(f"Scanning complete! Processed {completed_count} chunks")
        
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, cleaning up...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        # Cleanup
        logger.info("Performing cleanup...")
        
        # Stop all active containers
        for container_name in list(active_containers.keys()):
            container_manager.stop_container(container_name, logger)
        
        # Flush database
        db_manager.flush_remaining()
        
        # Final statistics
        final_stats = db_manager.get_scan_stats(args.mount_name)
        logger.info(f"Final stats: {final_stats['chunks_scanned']} chunks, "
                   f"{final_stats['files_counted']:,} files, "
                   f"{final_stats['total_size'] / (1024**3):.2f} GB")

if __name__ == '__main__':
    main()
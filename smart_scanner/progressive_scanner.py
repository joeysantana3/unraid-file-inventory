#!/usr/bin/env python3
"""
Progressive Smart NAS Scanner - Complete with Optimizations and Proper Container Management

Features:
- Graceful handling of empty directories
- Parallel initial chunk generation with proper container limits
- Container pool management and status monitoring
- Thread-safe database operations
- Robust error handling and retry logic
- Proper synchronization and resource management
- Docker-in-Docker compatibility for container environments
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

MAX_CONTAINERS = 6
MAX_RETRIES = 3
BATCH_SIZE = 100

class ContainerManager:
    """Manages Docker container lifecycle and limits"""
    
    def __init__(self, max_containers=MAX_CONTAINERS, host_db_dir=None):
        self.max_containers = max_containers
        self.running_containers = {}
        self.container_lock = threading.Lock()
        self.semaphore = threading.Semaphore(max_containers)
        self.host_db_dir = host_db_dir  # Host path for database directory
        
    def start_container(self, chunk, db_path, image_name, logger):
        """Start a container with proper resource management and Docker-in-Docker support"""
        self.semaphore.acquire()  # Block if max containers reached
        
        try:
            chunk_name = chunk['path'].replace('/', '_').replace(' ', '_')
            container_name = f"progressive-scan-{chunk_name[-40:]}-{int(time.time())}"
            container_name = ''.join(c for c in container_name if c.isalnum() or c in '-_')
            
            if not os.path.exists(chunk['path']) or is_empty_directory(chunk['path']):
                logger.error(f"Skipping empty or missing chunk: {chunk['path']}")
                return None
            
            # Handle Docker-in-Docker scenario
            # If we have a host_db_dir, use it for mounting; otherwise use container path
            if self.host_db_dir:
                db_mount_source = self.host_db_dir
                logger.debug(f"Using host database directory for mounting: {db_mount_source}")
            else:
                db_mount_source = os.path.dirname(db_path)
                logger.debug(f"Using container database directory for mounting: {db_mount_source}")
                
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
            
            logger.info(f"Starting container with command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.error(f"Failed to start container for {chunk['path']}: {result.stderr}")
                return None
                
            with self.container_lock:
                self.running_containers[container_name] = {
                    'chunk': chunk,
                    'start_time': time.time(),
                    'retries': 0
                }
                
            logger.info(f"Started container {container_name} for chunk {chunk['path']}")
            return container_name
            
        except Exception as e:
            logger.error(f"Exception starting container for {chunk['path']}: {e}")
            return None
        finally:
            self.semaphore.release()
            
    def wait_for_container(self, container_name, logger, timeout=3600):
        """Wait for a specific container to complete"""
        try:
            cmd = ['docker', 'wait', container_name]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            
            exit_code = int(result.stdout.strip()) if result.stdout.strip() else 1
            
            with self.container_lock:
                if container_name in self.running_containers:
                    chunk = self.running_containers[container_name]['chunk']
                    del self.running_containers[container_name]
                    
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
        
    def mark_chunk_scanned(self, chunk_path, mount_name):
        """Thread-safe batch marking of scanned chunks"""
        with self.batch_lock:
            self.completed_chunks_batch.append((chunk_path, mount_name))
            
            if len(self.completed_chunks_batch) >= BATCH_SIZE:
                self._flush_completed_chunks()
                
    def _flush_completed_chunks(self):
        """Internal method to flush batch - must be called with lock held"""
        if not self.completed_chunks_batch:
            return
            
        try:
            with sqlite3.connect(self.db_path, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.executemany(
                    'INSERT OR IGNORE INTO scanned_dirs (path, mount_point, scan_time) VALUES (?, ?, ?)',
                    [(path, mount, time.time()) for path, mount in self.completed_chunks_batch]
                )
                conn.commit()
                logging.info(f"Flushed {len(self.completed_chunks_batch)} completed chunks to database")
                self.completed_chunks_batch.clear()
        except Exception as e:
            logging.error(f"Error flushing chunks to database: {e}")
            
    def flush_remaining(self):
        """Flush any remaining batched chunks"""
        with self.batch_lock:
            self._flush_completed_chunks()
            
    def load_scanned_chunks(self):
        """Load previously scanned chunks from database"""
        try:
            with sqlite3.connect(self.db_path, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT path FROM scanned_dirs')
                return {row[0] for row in cursor.fetchall()}
        except Exception as e:
            logging.error(f"Error loading scanned chunks: {e}")
            return set()

def detect_container_environment():
    """Detect if we're running inside a Docker container and determine host paths"""
    # Check if we're in a container
    if os.path.exists('/.dockerenv'):
        # We're in a container, try to determine the host database directory
        # Look for environment variable or known mount patterns
        host_db_dir = os.environ.get('HOST_DB_DIR')
        if host_db_dir:
            return host_db_dir
        
        # Common pattern: if db is at /data/something.db, host is likely /mnt/user/appdata/nas-scanner
        # This matches the bash script setup
        if os.path.exists('/data') and os.access('/data', os.W_OK):
            return '/mnt/user/appdata/nas-scanner'
    
    return None

# Setup logging
def setup_logging(log_file_path=None):
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%Y-%m-%d %H:%M:%S')
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file_path:
        os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
        file_handler = logging.FileHandler(log_file_path, mode='a')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger

# Helper functions
def is_empty_directory(path):
    """Check if directory is empty"""
    try:
        return not any(os.scandir(path))
    except (OSError, PermissionError):
        return True

def generate_initial_chunks_parallel(mount_path, mount_name, scanned_chunks, logger):
    """Generate initial chunks in parallel with proper filtering"""
    try:
        subdirs = [entry.path for entry in os.scandir(mount_path) if entry.is_dir()]
    except (OSError, PermissionError) as e:
        logger.error(f"Cannot access mount path {mount_path}: {e}")
        return []

    def process_subdir(subdir):
        if subdir in scanned_chunks:
            logger.info(f"Skipping already scanned: {subdir}")
            return None
        if is_empty_directory(subdir):
            logger.info(f"Skipping empty directory: {subdir}")
            return None
        return {'path': subdir, 'mount_name': mount_name, 'priority': 1}

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(process_subdir, subdirs))

    chunks = [chunk for chunk in results if chunk]
    logger.info(f"Generated {len(chunks)} initial chunks from {len(subdirs)} subdirectories")
    return chunks

def generate_adaptive_chunks(pending_paths, mount_name, scanned_chunks, logger, max_chunks=5):
    """Generate adaptive chunks with better logic"""
    new_chunks = []

    while pending_paths and len(new_chunks) < max_chunks:
        try:
            path = pending_paths.pop(0)  # FIFO instead of LIFO
            if path not in scanned_chunks and not is_empty_directory(path):
                new_chunks.append({'path': path, 'mount_name': mount_name, 'priority': 1})
                scanned_chunks.add(path)
                logger.info(f"Adaptive chunk created: {path}")
            else:
                logger.debug(f"Skipped adaptive chunk (empty/already scanned): {path}")
        except IndexError:
            break

    logger.info(f"Generated {len(new_chunks)} adaptive chunks")
    return new_chunks

def retry_failed_chunk(chunk, retries, max_retries, container_manager, db_manager, image_name, logger):
    """Retry failed chunks with exponential backoff"""
    if retries >= max_retries:
        logger.error(f"Chunk {chunk['path']} failed {max_retries} times, giving up")
        return False
        
    wait_time = 2 ** retries
    logger.info(f"Retrying chunk {chunk['path']} in {wait_time} seconds (attempt {retries + 1}/{max_retries})")
    time.sleep(wait_time)
    
    container_name = container_manager.start_container(chunk, db_manager.db_path, image_name, logger)
    if container_name:
        success, _ = container_manager.wait_for_container(container_name, logger)
        if success:
            db_manager.mark_chunk_scanned(chunk['path'], chunk['mount_name'])
            return True
        else:
            return retry_failed_chunk(chunk, retries + 1, max_retries, container_manager, db_manager, image_name, logger)
    
    return False

# Main scanning logic
def main():
    parser = argparse.ArgumentParser(description='Complete Progressive NAS Scanner with Proper Management')
    parser.add_argument('mount_path', help='Path to mount point to scan')
    parser.add_argument('mount_name', help='Name identifier for the mount')
    parser.add_argument('--db', required=True, help='Path to database file')
    parser.add_argument('--image', default='nas-scanner-hp:latest', help='Docker image to use')
    parser.add_argument('--max-containers', type=int, default=MAX_CONTAINERS, help='Maximum concurrent containers')
    parser.add_argument('--log-file', help='Log file path')
    parser.add_argument('--host-db-dir', help='Host database directory path (for Docker-in-Docker scenarios)')
    args = parser.parse_args()

    logger = setup_logging(args.log_file)
    
    # Detect container environment
    detected_host_db_dir = detect_container_environment()
    host_db_dir = args.host_db_dir or detected_host_db_dir
    
    if host_db_dir:
        logger.info(f"Docker-in-Docker mode detected. Host database directory: {host_db_dir}")
    else:
        logger.info("Running in direct mode (not in container)")
    
    logger.info(f"Starting progressive scan of {args.mount_path} with max {args.max_containers} containers")

    # Initialize managers
    container_manager = ContainerManager(args.max_containers, host_db_dir)
    db_manager = DatabaseManager(args.db)
    
    # Load previously scanned chunks
    scanned_chunks = db_manager.load_scanned_chunks()
    logger.info(f"Loaded {len(scanned_chunks)} previously scanned chunks")

    try:
        # Generate initial directory list
        pending_paths = [entry.path for entry in os.scandir(args.mount_path) if entry.is_dir()]
        pending_paths = [path for path in pending_paths if path not in scanned_chunks]
        logger.info(f"Found {len(pending_paths)} directories to potentially scan")

        # Generate and process initial chunks
        chunks = generate_initial_chunks_parallel(args.mount_path, args.mount_name, scanned_chunks, logger)
        
        # Track active containers and failed chunks
        active_containers = {}
        failed_chunks = []
        
        # Process initial chunks
        for chunk in chunks:
            container_name = container_manager.start_container(chunk, args.db, args.image, logger)
            if container_name:
                active_containers[container_name] = chunk
            else:
                failed_chunks.append((chunk, 0))  # 0 retries so far
                
        # Continue with adaptive chunks while containers are running
        while pending_paths or active_containers or failed_chunks:
            # Start new containers for adaptive chunks if we have capacity
            if pending_paths and container_manager.get_running_count() < args.max_containers:
                adaptive_chunks = generate_adaptive_chunks(
                    pending_paths, args.mount_name, scanned_chunks, logger,
                    max_chunks=args.max_containers - container_manager.get_running_count()
                )
                
                for chunk in adaptive_chunks:
                    container_name = container_manager.start_container(chunk, args.db, args.image, logger)
                    if container_name:
                        active_containers[container_name] = chunk
                    else:
                        failed_chunks.append((chunk, 0))
            
            # Wait for containers to complete (non-blocking check)
            completed_containers = []
            for container_name in list(active_containers.keys()):
                # Quick status check
                try:
                    result = subprocess.run(['docker', 'ps', '-q', '-f', f'name={container_name}'], 
                                          capture_output=True, text=True, timeout=5)
                    if not result.stdout.strip():  # Container not running
                        success, chunk = container_manager.wait_for_container(container_name, logger, timeout=1)
                        completed_containers.append(container_name)
                        
                        if success and chunk:
                            db_manager.mark_chunk_scanned(chunk['path'], chunk['mount_name'])
                            logger.info(f"Successfully completed chunk: {chunk['path']}")
                        elif chunk:
                            failed_chunks.append((chunk, 0))
                            logger.warning(f"Chunk failed, will retry: {chunk['path']}")
                            
                except Exception as e:
                    logger.error(f"Error checking container {container_name}: {e}")
            
            # Clean up completed containers
            for container_name in completed_containers:
                if container_name in active_containers:
                    del active_containers[container_name]
            
            # Retry failed chunks if we have capacity
            if failed_chunks and container_manager.get_running_count() < args.max_containers:
                chunk, retries = failed_chunks.pop(0)
                if retries < MAX_RETRIES:
                    container_name = container_manager.start_container(chunk, args.db, args.image, logger)
                    if container_name:
                        active_containers[container_name] = chunk
                    else:
                        failed_chunks.append((chunk, retries + 1))
                else:
                    logger.error(f"Giving up on chunk after {MAX_RETRIES} retries: {chunk['path']}")
            
            # Brief pause to prevent busy waiting
            time.sleep(2)
            
            # Progress logging
            if len(active_containers) > 0:
                logger.info(f"Status: {len(active_containers)} active, {len(pending_paths)} pending, {len(failed_chunks)} failed")

    except KeyboardInterrupt:
        logger.info("Received interrupt signal, gracefully stopping...")
    except Exception as e:
        logger.error(f"Unexpected error during scanning: {e}")
    finally:
        # Clean up any remaining containers
        logger.info("Cleaning up remaining containers...")
        for container_name in active_containers:
            container_manager.stop_container(container_name, logger)
        
        # Flush remaining database updates
        db_manager.flush_remaining()
        logger.info("Scanning complete.")

if __name__ == '__main__':
    main()
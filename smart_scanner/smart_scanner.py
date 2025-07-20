#!/usr/bin/env python3
"""
Smart NAS Scanner - Intelligent Directory Chunking
Spawns containers per directory chunk based on size analysis
"""

import os
import sys
import json
import time
import logging
import subprocess
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Configuration
CHUNK_SIZE_GB = 100
CHUNK_SIZE_BYTES = CHUNK_SIZE_GB * 1024 * 1024 * 1024
MAX_CONTAINERS = 8
CONTAINER_RESOURCES = {
    'cpus': '12',
    'memory': '12g'
}

def setup_logging():
    """Setup logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

class DirectoryAnalyzer:
    """Analyzes directory sizes and creates optimal chunks"""
    
    def __init__(self, logger, analysis_timeout=1800):
        self.logger = logger
        self._size_cache = {}
        self._lock = threading.Lock()
        self.analysis_timeout = analysis_timeout
    
    def get_directory_size(self, path):
        """Get directory size with caching"""
        with self._lock:
            if path in self._size_cache:
                return self._size_cache[path]
        
        # For very large directories, try a faster estimation first
        try:
            # Quick file count check - if too many files, skip detailed analysis
            count_result = subprocess.run(
                ['find', path, '-type', 'f', '|', 'wc', '-l'], 
                shell=True,
                capture_output=True, 
                text=True, 
                timeout=60  # 1 minute timeout for file count
            )
            
            if count_result.returncode == 0:
                file_count = int(count_result.stdout.strip())
                # If more than 1M files, assume it's a large chunk and skip du
                if file_count > 1000000:
                    self.logger.info(f"Large directory detected ({file_count:,} files): {path} - treating as single chunk")
                    with self._lock:
                        self._size_cache[path] = CHUNK_SIZE_BYTES + 1
                    return CHUNK_SIZE_BYTES + 1
        except:
            pass  # Fall back to du if file count fails
        
        try:
            # Use du command for size calculation
            self.logger.info(f"Analyzing directory size: {path}")
            result = subprocess.run(
                ['du', '-sb', path], 
                capture_output=True, 
                text=True, 
                timeout=self.analysis_timeout  # Use configurable timeout
            )
            
            if result.returncode == 0:
                size = int(result.stdout.split()[0])
                with self._lock:
                    self._size_cache[path] = size
                return size
            else:
                self.logger.warning(f"Failed to get size for {path}: {result.stderr}")
                return 0
                
        except subprocess.TimeoutExpired:
            self.logger.warning(f"Timeout getting size for {path} after {self.analysis_timeout} seconds")
            # For very large directories that timeout, assume they're larger than chunk size
            # This will cause them to be processed as single chunks
            return CHUNK_SIZE_BYTES + 1
        except Exception as e:
            self.logger.error(f"Error getting size for {path}: {e}")
            return 0
    
    def find_optimal_chunks(self, root_path, mount_name):
        """Find optimal directory chunks for scanning"""
        chunks = []
        
        def analyze_directory(dir_path, depth=0):
            """Recursively analyze directory structure"""
            try:
                # Get size of current directory
                dir_size = self.get_directory_size(dir_path)
                
                self.logger.info(f"{'  ' * depth}Analyzing {dir_path}: {dir_size / 1024**3:.2f} GB")
                
                # If directory is small enough or we can't subdivide further, add as chunk
                if dir_size <= CHUNK_SIZE_BYTES:
                    chunks.append({
                        'path': dir_path,
                        'size_gb': dir_size / 1024**3,
                        'mount_name': mount_name,
                        'depth': depth
                    })
                    self.logger.info(f"{'  ' * depth}✓ Added chunk: {dir_path} ({dir_size / 1024**3:.2f} GB)")
                    return
                
                # Try to subdivide large directories
                subdirs = []
                try:
                    for item in os.listdir(dir_path):
                        item_path = os.path.join(dir_path, item)
                        if os.path.isdir(item_path) and not os.path.islink(item_path):
                            subdirs.append(item_path)
                except (PermissionError, OSError) as e:
                    self.logger.warning(f"Cannot list directory {dir_path}: {e}")
                    # Add as chunk anyway if we can't subdivide
                    chunks.append({
                        'path': dir_path,
                        'size_gb': dir_size / 1024**3,
                        'mount_name': mount_name,
                        'depth': depth,
                        'note': 'Cannot subdivide - permission denied'
                    })
                    return
                
                # If no subdirectories, add current directory as chunk
                if not subdirs:
                    chunks.append({
                        'path': dir_path,
                        'size_gb': dir_size / 1024**3,
                        'mount_name': mount_name,
                        'depth': depth,
                        'note': 'Leaf directory'
                    })
                    self.logger.info(f"{'  ' * depth}✓ Added leaf chunk: {dir_path} ({dir_size / 1024**3:.2f} GB)")
                    return
                
                # Recursively analyze subdirectories
                for subdir in subdirs:
                    analyze_directory(subdir, depth + 1)
                    
            except Exception as e:
                self.logger.error(f"Error analyzing {dir_path}: {e}")
                # Add as chunk anyway
                chunks.append({
                    'path': dir_path,
                    'size_gb': 0,
                    'mount_name': mount_name,
                    'depth': depth,
                    'note': f'Error: {str(e)}'
                })
        
        self.logger.info(f"Starting analysis of {root_path}")
        analyze_directory(root_path)
        
        # Sort chunks by size (largest first) for better load balancing
        chunks.sort(key=lambda x: x['size_gb'], reverse=True)
        
        return chunks

class SmartScanner:
    """Smart scanner that manages container spawning per chunk"""
    
    def __init__(self, db_path, image_name='nas-scanner-hp:latest', analysis_timeout=1800):
        self.logger = setup_logging()
        self.db_path = db_path
        self.image_name = image_name
        self.analyzer = DirectoryAnalyzer(self.logger, analysis_timeout)
        self.active_containers = {}
        self.completed_chunks = 0
        self.failed_chunks = 0
        
    def create_scan_database(self):
        """Create a new database with the same schema"""
        db_dir = os.path.dirname(self.db_path)
        os.makedirs(db_dir, exist_ok=True)
        
        # Directly create the database schema.  The previous implementation
        # attempted to invoke ``nas_scanner_hp.py`` with a non-existent
        # ``--init-only`` flag which resulted in an argument parsing error.
        # Creating the schema here avoids that issue and keeps the behaviour
        # self-contained.
        import sqlite3
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
        self.logger.info(f"Created database schema: {self.db_path}")
    
    def start_container(self, chunk):
        """Start a container for a specific chunk"""
        chunk_name = chunk['path'].replace('/', '_').replace(' ', '_')
        container_name = f"smart-scan-{chunk_name[-50:]}"  # Limit name length
        
        # Sanitize container name
        container_name = ''.join(c for c in container_name if c.isalnum() or c in '-_')
        
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--rm',
            '-v', f"{chunk['path']}:{chunk['path']}:ro",
            '-v', f"{os.path.dirname(self.db_path)}:/data",
            '--cpus', CONTAINER_RESOURCES['cpus'],
            '--memory', CONTAINER_RESOURCES['memory'],
            '--ulimit', 'nofile=65536:65536',
            self.image_name,
            'python', 'nas_scanner_hp.py',
            chunk['path'],
            chunk['mount_name'],
            '--db', '/data/' + os.path.basename(self.db_path),
            '--workers', '12'
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            container_id = result.stdout.strip()
            
            self.active_containers[container_id] = {
                'name': container_name,
                'chunk': chunk,
                'start_time': time.time()
            }
            
            self.logger.info(f"Started container {container_name} for {chunk['path']} ({chunk['size_gb']:.2f} GB)")
            return container_id
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to start container for {chunk['path']}: {e.stderr}")
            return None
    
    def monitor_containers(self):
        """Monitor running containers and handle completion"""
        while self.active_containers:
            completed = []
            
            for container_id, info in self.active_containers.items():
                try:
                    # Check if container is still running
                    result = subprocess.run(
                        ['docker', 'ps', '-q', '--filter', f'id={container_id}'],
                        capture_output=True, text=True
                    )
                    
                    if not result.stdout.strip():
                        # Container finished
                        completed.append(container_id)
                        
                        # Check exit code
                        exit_result = subprocess.run(
                            ['docker', 'inspect', container_id, '--format', '{{.State.ExitCode}}'],
                            capture_output=True, text=True
                        )
                        
                        exit_code = int(exit_result.stdout.strip()) if exit_result.stdout.strip() else 1
                        
                        if exit_code == 0:
                            self.completed_chunks += 1
                            self.logger.info(f"✓ Completed: {info['name']} ({info['chunk']['path']})")
                        else:
                            self.failed_chunks += 1
                            self.logger.error(f"✗ Failed: {info['name']} ({info['chunk']['path']}) - Exit code: {exit_code}")
                            
                            # Get container logs for debugging
                            log_result = subprocess.run(
                                ['docker', 'logs', '--tail', '10', container_id],
                                capture_output=True, text=True
                            )
                            if log_result.stdout or log_result.stderr:
                                self.logger.error(f"Container logs: {log_result.stdout}{log_result.stderr}")
                
                except Exception as e:
                    self.logger.error(f"Error monitoring container {container_id}: {e}")
                    completed.append(container_id)
                    self.failed_chunks += 1
            
            # Remove completed containers
            for container_id in completed:
                del self.active_containers[container_id]
            
            if self.active_containers:
                time.sleep(10)  # Check every 10 seconds
    
    def scan_mount_point(self, mount_path, mount_name):
        """Scan a mount point using smart chunking"""
        self.logger.info(f"Starting smart scan of {mount_path}")
        
        # Create database
        self.create_scan_database()
        
        # Analyze directory structure
        self.logger.info("Analyzing directory structure...")
        chunks = self.analyzer.find_optimal_chunks(mount_path, mount_name)
        
        self.logger.info(f"Found {len(chunks)} optimal chunks:")
        total_size = sum(chunk['size_gb'] for chunk in chunks)
        for i, chunk in enumerate(chunks):
            note = f" ({chunk['note']})" if 'note' in chunk else ""
            self.logger.info(f"  {i+1:2d}. {chunk['path']} - {chunk['size_gb']:.2f} GB{note}")
        
        self.logger.info(f"Total size: {total_size:.2f} GB across {len(chunks)} chunks")
        
        # Process chunks in batches
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=MAX_CONTAINERS) as executor:
            # Submit all chunks
            futures = []
            for chunk in chunks:
                future = executor.submit(self._process_chunk, chunk)
                futures.append(future)
            
            # Wait for completion
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Chunk processing error: {e}")
                    self.failed_chunks += 1
        
        # Final statistics
        elapsed = time.time() - start_time
        self.logger.info(f"\nScan Complete!")
        self.logger.info(f"Completed chunks: {self.completed_chunks}")
        self.logger.info(f"Failed chunks: {self.failed_chunks}")
        self.logger.info(f"Total time: {elapsed/60:.1f} minutes")
        
        # Show database statistics
        self._show_final_stats()
    
    def _process_chunk(self, chunk):
        """Process a single chunk"""
        container_id = self.start_container(chunk)
        if not container_id:
            raise Exception(f"Failed to start container for {chunk['path']}")
        
        # Wait for this specific container to complete
        while True:
            try:
                result = subprocess.run(
                    ['docker', 'ps', '-q', '--filter', f'id={container_id}'],
                    capture_output=True, text=True
                )
                
                if not result.stdout.strip():
                    # Container finished
                    exit_result = subprocess.run(
                        ['docker', 'inspect', container_id, '--format', '{{.State.ExitCode}}'],
                        capture_output=True, text=True
                    )
                    
                    exit_code = int(exit_result.stdout.strip()) if exit_result.stdout.strip() else 1
                    
                    if exit_code == 0:
                        self.completed_chunks += 1
                        self.logger.info(f"✓ Completed chunk: {chunk['path']} ({chunk['size_gb']:.2f} GB)")
                    else:
                        self.failed_chunks += 1
                        self.logger.error(f"✗ Failed chunk: {chunk['path']} - Exit code: {exit_code}")
                        raise Exception(f"Container failed with exit code {exit_code}")
                    
                    break
                
                time.sleep(5)  # Check every 5 seconds
                
            except Exception as e:
                self.logger.error(f"Error waiting for container {container_id}: {e}")
                raise
    
    def _show_final_stats(self):
        """Show final database statistics"""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get overall stats
            cursor.execute("SELECT COUNT(*), SUM(size) FROM files")
            total_files, total_bytes = cursor.fetchone()
            
            # Get per-mount stats
            cursor.execute("""
                SELECT mount_point, COUNT(*), SUM(size) 
                FROM files 
                GROUP BY mount_point
            """)
            mount_stats = cursor.fetchall()
            
            conn.close()
            
            self.logger.info(f"\nDatabase Statistics:")
            self.logger.info(f"Total files: {total_files:,}")
            self.logger.info(f"Total size: {total_bytes/1024**4:.2f} TB")
            
            for mount, files, size in mount_stats:
                self.logger.info(f"  {mount}: {files:,} files, {size/1024**3:.2f} GB")
                
        except Exception as e:
            self.logger.error(f"Error getting final stats: {e}")

def main():
    parser = argparse.ArgumentParser(description='Smart NAS Scanner with Intelligent Chunking')
    parser.add_argument('mount_path', help='Path to mount point to scan')
    parser.add_argument('mount_name', help='Name of the mount point')
    parser.add_argument('--db', required=True, help='Database path')
    parser.add_argument('--chunk-size', type=int, default=100, help='Chunk size in GB (default: 100)')
    parser.add_argument('--max-containers', type=int, default=8, help='Maximum concurrent containers')
    parser.add_argument('--image', default='nas-scanner-hp:latest', help='Docker image to use')
    parser.add_argument('--analysis-timeout', type=int, default=1800, help='Directory analysis timeout in seconds (default: 1800 = 30 minutes)')
    
    args = parser.parse_args()
    
    # Update global configuration
    global CHUNK_SIZE_GB, CHUNK_SIZE_BYTES, MAX_CONTAINERS
    CHUNK_SIZE_GB = args.chunk_size
    CHUNK_SIZE_BYTES = CHUNK_SIZE_GB * 1024 * 1024 * 1024
    MAX_CONTAINERS = args.max_containers
    
    # Create and run scanner
    scanner = SmartScanner(args.db, args.image, args.analysis_timeout)
    scanner.scan_mount_point(args.mount_path, args.mount_name)

if __name__ == '__main__':
    main()

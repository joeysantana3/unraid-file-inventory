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
import signal
from typing import Dict, List, Optional, Tuple

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
        self._progress_counter = 0
        self._analysis_start_time = None
    
    def get_directory_size(self, path, show_progress=True):
        """Get directory size with caching and progress logging"""
        with self._lock:
            if path in self._size_cache:
                if show_progress:
                    self.logger.info(f"Using cached size for: {path} ({self._size_cache[path] / 1024**3:.2f} GB)")
                return self._size_cache[path]
        
        # For very large directories, try a faster estimation first
        if show_progress:
            self.logger.info(f"[{self._get_progress_indicator()}] Running fast file count check for: {path}")
        try:
            # Quick file count check - if too many files, skip detailed analysis
            count_start = time.time()
            count_result = subprocess.run(
                ['sh', '-c', f'find "{path}" -type f | wc -l'], 
                capture_output=True, 
                text=True, 
                timeout=120  # 2 minute timeout for file count
            )
            count_elapsed = time.time() - count_start
            
            if count_result.returncode == 0:
                file_count = int(count_result.stdout.strip())
                if show_progress:
                    self.logger.info(f"File count result: {file_count:,} files in {path} (took {count_elapsed:.1f}s)")
                # If more than 50K files, assume it's a large chunk and skip du
                if file_count > 50000:  # Further lowered threshold for faster processing
                    if show_progress:
                        self.logger.info(f"Large directory detected ({file_count:,} files): {path} - treating as oversized chunk")
                    with self._lock:
                        self._size_cache[path] = CHUNK_SIZE_BYTES + 1
                    return CHUNK_SIZE_BYTES + 1
            else:
                self.logger.warning(f"File count failed for {path}: {count_result.stderr}")
        except Exception as e:
            self.logger.warning(f"File count check failed for {path}: {e}")
            # For root directories like /mnt/user/Archive, assume they're large
            if '/mnt/user/' in path and path.count('/') <= 3:
                self.logger.info(f"Root mount directory detected: {path} - treating as single chunk")
                with self._lock:
                    self._size_cache[path] = CHUNK_SIZE_BYTES + 1
                return CHUNK_SIZE_BYTES + 1
        
        try:
            # Use du command for size calculation with progress updates
            if show_progress:
                self.logger.info(f"[{self._get_progress_indicator()}] Starting du analysis for: {path} (timeout: {self.analysis_timeout//60}min)")
            
            du_start = time.time()
            # Try faster methods first before falling back to du
            # Method 1: Use find with size calculation (faster for many small files)
            try:
                find_result = subprocess.run(
                    ['find', path, '-type', 'f', '-printf', '%s\n'],
                    capture_output=True,
                    text=True,
                    timeout=min(300, self.analysis_timeout // 4)  # Try find for max 5min or 1/4 of timeout
                )
                
                if find_result.returncode == 0 and find_result.stdout.strip():
                    # Sum up file sizes from find output
                    sizes = [int(s) for s in find_result.stdout.strip().split('\n') if s.isdigit()]
                    if sizes:
                        total_size = sum(sizes)
                        find_elapsed = time.time() - du_start
                        if show_progress:
                            self.logger.info(f"[{self._get_progress_indicator()}] FAST: find method completed for {path} = {total_size / 1024**3:.2f} GB (took {find_elapsed:.1f}s, {len(sizes):,} files)")
                        with self._lock:
                            self._size_cache[path] = total_size
                        return total_size
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError):
                # Fall back to du method
                if show_progress:
                    self.logger.info(f"[{self._get_progress_indicator()}] find method failed/timed out, falling back to du for: {path}")
            
            # Method 2: Use du as fallback (slower but more reliable)
            result = subprocess.run(
                ['du', '-sb', '--max-depth=0', path], 
                capture_output=True, 
                text=True, 
                timeout=self.analysis_timeout  # Use configurable timeout
            )
            du_elapsed = time.time() - du_start
            
            if result.returncode == 0:
                size = int(result.stdout.split()[0])
                if show_progress:
                    self.logger.info(f"[{self._get_progress_indicator()}] Size analysis complete: {path} = {size / 1024**3:.2f} GB (took {du_elapsed:.1f}s)")
                with self._lock:
                    self._size_cache[path] = size
                return size
            else:
                self.logger.warning(f"Failed to get size for {path}: {result.stderr}")
                return 0
                
        except subprocess.TimeoutExpired:
            if show_progress:
                self.logger.warning(f"[{self._get_progress_indicator()}] TIMEOUT: Size analysis for {path} exceeded {self.analysis_timeout//60} minutes - treating as oversized chunk")
            # For very large directories that timeout, assume they're larger than chunk size
            # This will cause them to be processed as single chunks
            with self._lock:
                self._size_cache[path] = CHUNK_SIZE_BYTES + 1
            return CHUNK_SIZE_BYTES + 1
        except Exception as e:
            if show_progress:
                self.logger.error(f"[{self._get_progress_indicator()}] Error getting size for {path}: {e}")
            return 0
    
    def _get_progress_indicator(self):
        """Get a progress indicator for logging"""
        with self._lock:
            self._progress_counter += 1
            if self._analysis_start_time:
                elapsed = time.time() - self._analysis_start_time
                return f"{self._progress_counter:3d} | {elapsed//60:02.0f}:{elapsed%60:02.0f}"
            return f"{self._progress_counter:3d}"

    def list_quick_chunks(self, root_path, mount_name):
        """Return top-level directories as chunks without size analysis"""
        chunks = []
        try:
            for entry in os.scandir(root_path):
                if entry.is_dir(follow_symlinks=False):
                    chunks.append({
                        'path': entry.path,
                        'size_gb': 0,
                        'mount_name': mount_name,
                        'depth': 1,
                        'note': 'fast'
                    })
        except Exception as e:
            self.logger.error(f"Error listing directories in {root_path}: {e}")

        if not chunks:
            chunks.append({
                'path': root_path,
                'size_gb': 0,
                'mount_name': mount_name,
                'depth': 0,
                'note': 'fast-root'
            })

        return chunks
    
    def find_optimal_chunks(self, root_path, mount_name):
        """Find optimal directory chunks for scanning with parallel analysis"""
        chunks = []
        self._analysis_start_time = time.time()
        self._progress_counter = 0
        
        def analyze_directory(dir_path, depth=0):
            """Recursively analyze directory structure"""
            try:
                # For root mount points, use parallel analysis of subdirectories
                if depth == 0 and '/mnt/user/' in dir_path:
                    self.logger.info(f"Root mount point detected: {dir_path} - analyzing subdirectories for optimal chunking")
                    try:
                        subdirs = [d for d in os.listdir(dir_path) 
                                 if os.path.isdir(os.path.join(dir_path, d)) and not d.startswith('.')]
                        
                        if subdirs:
                            self.logger.info(f"Found {len(subdirs)} subdirectories in {dir_path} - starting parallel analysis")
                            
                            # Analyze subdirectories in parallel for faster processing
                            subdir_paths = [os.path.join(dir_path, subdir) for subdir in subdirs]
                            self._analyze_directories_parallel(subdir_paths, mount_name, chunks, depth + 1)
                            return
                        else:
                            self.logger.warning(f"No subdirectories found in {dir_path} - treating as single chunk")
                            # Fall through to normal analysis if no subdirectories
                    except (PermissionError, OSError) as e:
                        self.logger.warning(f"Cannot list root directory {dir_path}: {e} - treating as single chunk")
                        # Fall through to normal analysis
                
                # Get size of current directory (for non-root or fallback cases)
                dir_size = self.get_directory_size(dir_path, show_progress=True)
                
                self.logger.info(f"{'  ' * depth}[{self._get_progress_indicator()}] Analysis result: {dir_path} = {dir_size / 1024**3:.2f} GB")
                
                # If directory is small enough or we can't subdivide further, add as chunk
                if dir_size <= CHUNK_SIZE_BYTES:
                    chunk = {
                        'path': dir_path,
                        'size_gb': dir_size / 1024**3,
                        'mount_name': mount_name,
                        'depth': depth
                    }
                    chunks.append(chunk)
                    self.logger.info(f"{'  ' * depth}✓ [CHUNK {len(chunks)}] Added: {dir_path} ({dir_size / 1024**3:.2f} GB)")
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
                    chunk = {
                        'path': dir_path,
                        'size_gb': dir_size / 1024**3,
                        'mount_name': mount_name,
                        'depth': depth,
                        'note': 'Leaf directory'
                    }
                    chunks.append(chunk)
                    self.logger.info(f"{'  ' * depth}✓ [CHUNK {len(chunks)}] Added leaf: {dir_path} ({dir_size / 1024**3:.2f} GB)")
                    return
                
                # Recursively analyze subdirectories
                for subdir in subdirs:
                    analyze_directory(subdir, depth + 1)
                    
            except Exception as e:
                self.logger.error(f"Error analyzing {dir_path}: {e}")
                # Add as chunk anyway
                chunk = {
                    'path': dir_path,
                    'size_gb': 0,
                    'mount_name': mount_name,
                    'depth': depth,
                    'note': f'Error: {str(e)}'
                }
                chunks.append(chunk)
                self.logger.error(f"[CHUNK {len(chunks)}] Added error chunk: {dir_path}")
        
        self.logger.info(f"Starting comprehensive analysis of {root_path}")
        analyze_directory(root_path)
        
        total_analysis_time = time.time() - self._analysis_start_time
        self.logger.info(f"Analysis complete! Found {len(chunks)} chunks in {total_analysis_time//60:.0f}m {total_analysis_time%60:.0f}s")
        
        # Sort chunks by size (largest first) for better load balancing
        chunks.sort(key=lambda x: x['size_gb'], reverse=True)
        
        return chunks
    
    def _analyze_directories_parallel(self, dir_paths, mount_name, chunks, depth):
        """Analyze multiple directories in parallel for faster processing"""
        self.logger.info(f"Starting parallel analysis of {len(dir_paths)} directories...")
        
        def analyze_single(dir_path):
            """Analyze a single directory and return chunk info"""
            try:
                dir_size = self.get_directory_size(dir_path, show_progress=True)
                
                # If small enough, return as chunk
                if dir_size <= CHUNK_SIZE_BYTES:
                    return {
                        'path': dir_path,
                        'size_gb': dir_size / 1024**3,
                        'mount_name': mount_name,
                        'depth': depth,
                        'note': 'Parallel analysis'
                    }
                else:
                    # For large directories, we'll need recursive analysis later
                    # For now, just mark them for single-chunk processing
                    self.logger.info(f"Large directory found: {dir_path} ({dir_size / 1024**3:.2f} GB) - will process as single chunk")
                    return {
                        'path': dir_path,
                        'size_gb': dir_size / 1024**3,
                        'mount_name': mount_name,
                        'depth': depth,
                        'note': 'Large directory - single chunk'
                    }
            except Exception as e:
                self.logger.error(f"Error analyzing {dir_path}: {e}")
                return {
                    'path': dir_path,
                    'size_gb': 0,
                    'mount_name': mount_name,
                    'depth': depth,
                    'note': f'Analysis error: {str(e)}'
                }
        
        # Use ThreadPoolExecutor for parallel directory analysis
        with ThreadPoolExecutor(max_workers=min(8, len(dir_paths))) as executor:
            future_to_path = {executor.submit(analyze_single, path): path for path in dir_paths}
            
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    chunk = future.result()
                    if chunk:
                        chunks.append(chunk)
                        self.logger.info(f"✓ [CHUNK {len(chunks)}] Parallel result: {chunk['path']} ({chunk['size_gb']:.2f} GB)")
                except Exception as e:
                    self.logger.error(f"Failed to analyze {path}: {e}")
                    # Add a fallback chunk
                    chunks.append({
                        'path': path,
                        'size_gb': 0,
                        'mount_name': mount_name,
                        'depth': depth,
                        'note': f'Parallel analysis failed: {str(e)}'
                    })
        
        self.logger.info(f"Parallel analysis complete - processed {len(dir_paths)} directories")

class SmartScanner:
    """Smart scanner that manages container spawning per chunk"""

    def __init__(self, db_path, image_name='nas-scanner-hp:latest', analysis_timeout=1800, skip_analysis=False):
        self.logger = setup_logging()
        self.db_path = db_path
        self.image_name = image_name
        self.analyzer = DirectoryAnalyzer(self.logger, analysis_timeout)
        self.skip_analysis = skip_analysis
        self.active_containers = {}
        self.completed_chunks = 0
        self.failed_chunks = 0
        self._shutdown_requested = False
        
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
        # Set up signal handling for graceful shutdown
        self._setup_signal_handlers()
        
        self.logger.info(f"Starting smart scan of {mount_path}")
        self.logger.info(f"Analysis timeout: {self.analyzer.analysis_timeout//60} minutes per directory")
        self.logger.info(f"Max containers: {MAX_CONTAINERS}")
        
        # Validate mount path exists
        if not os.path.exists(mount_path):
            raise Exception(f"Mount path does not exist: {mount_path}")
        
        # Create database
        self.create_scan_database()
        
        # Analyze directory structure
        self.logger.info("Analyzing directory structure...")
        
        # Start analysis and begin processing chunks as they're discovered
        if self.skip_analysis:
            self.logger.info("Fast start enabled - skipping size analysis")
            chunks = self.analyzer.list_quick_chunks(mount_path, mount_name)
        else:
            chunks = self.analyzer.find_optimal_chunks(mount_path, mount_name)
        
        if not chunks:
            self.logger.warning("No chunks found - creating single fallback chunk")
            chunks = [{
                'path': mount_path,
                'size_gb': 0,
                'mount_name': mount_name,
                'depth': 0,
                'note': 'Fallback chunk - analysis failed'
            }]
        
        self.logger.info(f"\n=== CHUNK ANALYSIS COMPLETE ===")
        self.logger.info(f"Found {len(chunks)} optimal chunks:")
        total_size = sum(chunk['size_gb'] for chunk in chunks)
        
        for i, chunk in enumerate(chunks):
            note = f" [{chunk['note']}]" if 'note' in chunk else ""
            self.logger.info(f"  {i+1:2d}. {chunk['path']:<60} | {chunk['size_gb']:>8.2f} GB{note}")
        
        self.logger.info(f"\nTotal size: {total_size:.2f} GB across {len(chunks)} chunks")
        self.logger.info(f"Estimated scan time: {self._estimate_scan_time(chunks):.0f} minutes")
        self.logger.info("===================================\n")
        
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
        self.logger.info(f"\n=== SCAN COMPLETE ===")
        self.logger.info(f"Completed chunks: {self.completed_chunks}/{len(chunks)}")
        self.logger.info(f"Failed chunks: {self.failed_chunks}/{len(chunks)}")
        self.logger.info(f"Success rate: {(self.completed_chunks/(self.completed_chunks+self.failed_chunks)*100):.1f}%" if (self.completed_chunks + self.failed_chunks) > 0 else "N/A")
        self.logger.info(f"Total time: {elapsed/60:.1f} minutes")
        self.logger.info(f"Average time per chunk: {elapsed/len(chunks)/60:.1f} minutes")
        self.logger.info("======================\n")
        
        # Show database statistics
        self._show_final_stats()
    
    def _process_chunk(self, chunk):
        """Process a single chunk with detailed progress logging"""
        chunk_start = time.time()
        self.logger.info(f"[STARTING] Chunk processing: {chunk['path']} ({chunk['size_gb']:.2f} GB)")
        
        container_id = self.start_container(chunk)
        if not container_id:
            raise Exception(f"Failed to start container for {chunk['path']}")
        
        # Wait for this specific container to complete with periodic progress updates
        last_log_time = time.time()
        while True:
            try:
                result = subprocess.run(
                    ['docker', 'ps', '-q', '--filter', f'id={container_id}'],
                    capture_output=True, text=True
                )
                
                if not result.stdout.strip():
                    # Container finished
                    chunk_elapsed = time.time() - chunk_start
                    
                    exit_result = subprocess.run(
                        ['docker', 'inspect', container_id, '--format', '{{.State.ExitCode}}'],
                        capture_output=True, text=True
                    )
                    
                    exit_code = int(exit_result.stdout.strip()) if exit_result.stdout.strip() else 1
                    
                    if exit_code == 0:
                        self.completed_chunks += 1
                        self.logger.info(f"✓ [COMPLETED {self.completed_chunks}/{self.completed_chunks+self.failed_chunks}] {chunk['path']} ({chunk['size_gb']:.2f} GB) in {chunk_elapsed/60:.1f}min")
                    else:
                        self.failed_chunks += 1
                        self.logger.error(f"✗ [FAILED {self.failed_chunks}] {chunk['path']} - Exit code: {exit_code} after {chunk_elapsed/60:.1f}min")
                        
                        # Get container logs for debugging
                        try:
                            log_result = subprocess.run(
                                ['docker', 'logs', '--tail', '20', container_id],
                                capture_output=True, text=True, timeout=10
                            )
                            if log_result.stdout or log_result.stderr:
                                self.logger.error(f"Container logs:\n{log_result.stdout}{log_result.stderr}")
                        except:
                            pass
                        
                        raise Exception(f"Container failed with exit code {exit_code}")
                    
                    break
                
                # Periodic progress logging for long-running containers
                current_time = time.time()
                if current_time - last_log_time > 300:  # Every 5 minutes
                    elapsed = current_time - chunk_start
                    self.logger.info(f"[PROGRESS] {chunk['path']} still running... ({elapsed/60:.1f}min elapsed)")
                    last_log_time = current_time
                
                time.sleep(10)  # Check every 10 seconds
                
            except Exception as e:
                chunk_elapsed = time.time() - chunk_start
                self.logger.error(f"Error waiting for container {container_id} after {chunk_elapsed/60:.1f}min: {e}")
                
                # Check for shutdown request
                if self._shutdown_requested:
                    self.logger.info("Shutdown requested, stopping chunk processing...")
                    raise KeyboardInterrupt("Shutdown requested")
                
                raise
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            signal_name = signal.Signals(signum).name
            self.logger.info(f"\n{signal_name} received - initiating graceful shutdown...")
            self._shutdown_requested = True
            
            # Stop any running containers
            if self.active_containers:
                self.logger.info(f"Stopping {len(self.active_containers)} active containers...")
                for container_id, info in list(self.active_containers.items()):
                    try:
                        subprocess.run(['docker', 'stop', container_id], timeout=30)
                        self.logger.info(f"Stopped container: {info['name']}")
                    except Exception as e:
                        self.logger.error(f"Error stopping container {container_id}: {e}")
            
            # Show final stats before exiting
            self.logger.info(f"\nShutdown Summary:")
            self.logger.info(f"Completed chunks: {self.completed_chunks}")
            self.logger.info(f"Failed chunks: {self.failed_chunks}")
            self._show_final_stats()
            
            sys.exit(130)  # Standard exit code for Ctrl+C
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    def _estimate_scan_time(self, chunks):
        """Estimate total scan time based on chunk sizes"""
        # Rough estimate: 1 minute per GB for scanning + overhead
        total_gb = sum(chunk['size_gb'] for chunk in chunks)
        base_time = total_gb * 0.5  # 30 seconds per GB
        overhead = len(chunks) * 2  # 2 minutes overhead per chunk
        return base_time + overhead
    
    def _show_final_stats(self):
        """Show final database statistics"""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get overall stats
            cursor.execute("SELECT COUNT(*), SUM(size), COUNT(DISTINCT mount_point) FROM files")
            result = cursor.fetchone()
            total_files = result[0] or 0
            total_bytes = result[1] or 0
            total_mounts = result[2] or 0
            
            # Get per-file-type stats
            cursor.execute("""
                SELECT file_type, COUNT(*), SUM(size) 
                FROM files 
                GROUP BY file_type 
                ORDER BY SUM(size) DESC
            """)
            type_stats = cursor.fetchall()
            
            # Get per-mount stats
            cursor.execute("""
                SELECT mount_point, COUNT(*), SUM(size) 
                FROM files 
                GROUP BY mount_point
                ORDER BY SUM(size) DESC
            """)
            mount_stats = cursor.fetchall()
            
            conn.close()
            
            self.logger.info(f"=== DATABASE STATISTICS ===")
            self.logger.info(f"Total files scanned: {total_files:,}")
            self.logger.info(f"Total size scanned: {total_bytes/1024**3:.2f} GB ({total_bytes/1024**4:.2f} TB)")
            self.logger.info(f"Mount points: {total_mounts}")
            
            if type_stats:
                self.logger.info(f"\nBy file type:")
                for file_type, files, size in type_stats[:10]:  # Top 10
                    self.logger.info(f"  {file_type or 'unknown':<12}: {files:>8,} files, {size/1024**3:>8.2f} GB")
            
            if mount_stats:
                self.logger.info(f"\nBy mount point:")
                for mount, files, size in mount_stats:
                    self.logger.info(f"  {mount:<20}: {files:>8,} files, {size/1024**3:>8.2f} GB")
            
            self.logger.info("=============================")
                
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
    parser.add_argument('--fast-start', action='store_true', help='Skip size analysis and scan each top-level directory directly')
    
    args = parser.parse_args()
    
    # Update global configuration
    global CHUNK_SIZE_GB, CHUNK_SIZE_BYTES, MAX_CONTAINERS
    CHUNK_SIZE_GB = args.chunk_size
    CHUNK_SIZE_BYTES = CHUNK_SIZE_GB * 1024 * 1024 * 1024
    MAX_CONTAINERS = args.max_containers
    
    # Create and run scanner
    scanner = SmartScanner(args.db, args.image, args.analysis_timeout, skip_analysis=args.fast_start)
    scanner.scan_mount_point(args.mount_path, args.mount_name)

if __name__ == '__main__':
    main()

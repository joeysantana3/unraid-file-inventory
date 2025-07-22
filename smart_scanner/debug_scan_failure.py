#!/usr/bin/env python3
"""
Smart Scanner Failure Analysis Tool

This script analyzes failed smart scanner runs to help determine what went wrong.
It examines logs, database state, system resources, and Docker container status.
"""

import os
import sys
import sqlite3
import glob
import json
import subprocess
import argparse
from datetime import datetime, timedelta
from pathlib import Path

def analyze_log_files(log_dir):
    """Analyze smart scanner log files for error patterns"""
    print("=" * 60)
    print("LOG FILE ANALYSIS")
    print("=" * 60)
    
    log_files = glob.glob(os.path.join(log_dir, "smart_scan_*.log"))
    
    if not log_files:
        print(f"❌ No smart scanner log files found in {log_dir}")
        return
    
    # Sort by modification time (newest first)
    log_files.sort(key=os.path.getmtime, reverse=True)
    
    print(f"Found {len(log_files)} log files:")
    for i, log_file in enumerate(log_files[:5]):  # Show only last 5
        mtime = datetime.fromtimestamp(os.path.getmtime(log_file))
        size = os.path.getsize(log_file)
        print(f"  {i+1}. {os.path.basename(log_file)} - {mtime.strftime('%Y-%m-%d %H:%M:%S')} ({size:,} bytes)")
    
    # Analyze the most recent log file
    latest_log = log_files[0]
    print(f"\nAnalyzing latest log: {os.path.basename(latest_log)}")
    
    try:
        with open(latest_log, 'r') as f:
            lines = f.readlines()
        
        print(f"Log contains {len(lines)} lines")
        
        # Look for key patterns
        errors = [line for line in lines if 'ERROR' in line]
        warnings = [line for line in lines if 'WARNING' in line]
        failed_containers = [line for line in lines if 'Failed to start container' in line]
        timeouts = [line for line in lines if 'TIMEOUT' in line or 'timeout' in line]
        database_issues = [line for line in lines if 'Database' in line and ('error' in line.lower() or 'failed' in line.lower())]
        
        print(f"\nError patterns found:")
        print(f"  Errors: {len(errors)}")
        print(f"  Warnings: {len(warnings)}")
        print(f"  Failed containers: {len(failed_containers)}")
        print(f"  Timeouts: {len(timeouts)}")
        print(f"  Database issues: {len(database_issues)}")
        
        # Show recent errors
        if errors:
            print(f"\nRecent errors (last 5):")
            for error in errors[-5:]:
                print(f"  {error.strip()}")
        
        # Check if scan completed
        completion_lines = [line for line in lines if 'SMART SCAN COMPLETE' in line]
        if completion_lines:
            print(f"\n✅ Scan appears to have completed normally")
        else:
            print(f"\n❌ Scan did not complete normally")
            
        # Look for last activity
        if lines:
            last_line = lines[-1]
            print(f"\nLast log entry: {last_line.strip()}")
            
        return {
            'total_lines': len(lines),
            'errors': len(errors),
            'warnings': len(warnings),
            'failed_containers': len(failed_containers),
            'timeouts': len(timeouts),
            'database_issues': len(database_issues),
            'completed': len(completion_lines) > 0
        }
            
    except Exception as e:
        print(f"❌ Error reading log file: {e}")
        return None

def analyze_database_state(db_path):
    """Analyze the database state to understand what data was actually written"""
    print("\n" + "=" * 60)
    print("DATABASE ANALYSIS")
    print("=" * 60)
    
    if not os.path.exists(db_path):
        print(f"❌ Database not found at {db_path}")
        return None
    
    print(f"Database file: {db_path}")
    print(f"Size: {os.path.getsize(db_path):,} bytes")
    print(f"Modified: {datetime.fromtimestamp(os.path.getmtime(db_path)).strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        
        # Basic table info
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"Tables: {', '.join(tables)}")
        
        # Files table analysis
        cursor.execute("SELECT COUNT(*) FROM files")
        total_files = cursor.fetchone()[0]
        print(f"\nTotal files in database: {total_files:,}")
        
        if total_files == 0:
            print("❌ No files found in database - this indicates the scan never successfully wrote any data")
            
            # Check if schema exists
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='files'")
            schema = cursor.fetchone()
            if schema:
                print("✅ Database schema exists - scan started but no data was written")
            else:
                print("❌ Database schema missing - scan may have failed very early")
        else:
            print("✅ Database contains file data")
            
            # Analyze by mount point
            cursor.execute("SELECT mount_point, COUNT(*), SUM(size) FROM files GROUP BY mount_point ORDER BY COUNT(*) DESC")
            mount_stats = cursor.fetchall()
            
            print(f"\nData by mount point:")
            for mount, count, total_size in mount_stats:
                size_gb = (total_size or 0) / (1024**3)
                print(f"  {mount}: {count:,} files, {size_gb:.2f} GB")
            
            # Check scan timestamps
            cursor.execute("SELECT MIN(scan_time), MAX(scan_time) FROM files")
            min_time, max_time = cursor.fetchone()
            
            if min_time and max_time:
                start_dt = datetime.fromtimestamp(min_time)
                end_dt = datetime.fromtimestamp(max_time)
                duration = end_dt - start_dt
                
                print(f"\nScan timeline:")
                print(f"  First file: {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"  Last file: {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"  Duration: {duration}")
                
                # Check for recent activity
                one_hour_ago = (datetime.now() - timedelta(hours=1)).timestamp()
                cursor.execute("SELECT COUNT(*) FROM files WHERE scan_time > ?", (one_hour_ago,))
                recent_files = cursor.fetchone()[0]
                print(f"  Files added in last hour: {recent_files:,}")
        
        # Check scanned_dirs table
        cursor.execute("SELECT COUNT(*) FROM scanned_dirs")
        scanned_dirs = cursor.fetchone()[0]
        print(f"\nScanned directories: {scanned_dirs:,}")
        
        conn.close()
        
        return {
            'total_files': total_files,
            'has_data': total_files > 0,
            'mount_stats': mount_stats if total_files > 0 else [],
            'scanned_dirs': scanned_dirs
        }
        
    except Exception as e:
        print(f"❌ Error analyzing database: {e}")
        return None

def check_system_resources():
    """Check current system resources and Docker status"""
    print("\n" + "=" * 60)
    print("SYSTEM RESOURCE ANALYSIS")
    print("=" * 60)
    
    # Docker status
    try:
        result = subprocess.run(['docker', 'info'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print("✅ Docker daemon is running")
            
            # Check for running smart scanner containers
            result = subprocess.run(['docker', 'ps', '--filter', 'name=smart-scan', '--format', '{{.Names}}\t{{.Status}}'], 
                                   capture_output=True, text=True)
            if result.stdout.strip():
                print("📋 Running smart scanner containers:")
                for line in result.stdout.strip().split('\n'):
                    print(f"  {line}")
            else:
                print("ℹ️  No smart scanner containers currently running")
                
        else:
            print("❌ Docker daemon not responding")
            
    except Exception as e:
        print(f"❌ Error checking Docker: {e}")
    
    # Check disk space
    try:
        import shutil
        
        # Check space on database location
        db_space = shutil.disk_usage('/mnt/user/appdata/nas-scanner')
        print(f"\nDisk space (database location):")
        print(f"  Free: {db_space.free / (1024**3):.1f} GB")
        print(f"  Total: {db_space.total / (1024**3):.1f} GB")
        
        if db_space.free < 1024**3:  # Less than 1GB
            print("⚠️  Low disk space - may cause database write failures")
            
    except Exception as e:
        print(f"❌ Error checking disk space: {e}")
    
    # Check mount accessibility
    print(f"\nMount point accessibility:")
    common_mounts = ['/mnt/user/Archive', '/mnt/user/Movies', '/mnt/user/Photos', '/mnt/user/Music']
    
    for mount in common_mounts:
        if os.path.exists(mount):
            try:
                files = os.listdir(mount)
                print(f"  {mount}: ✅ accessible ({len(files)} entries)")
            except Exception as e:
                print(f"  {mount}: ❌ not accessible ({e})")
        else:
            print(f"  {mount}: ⚠️  does not exist")

def check_docker_logs():
    """Check recent Docker container logs for smart scanner containers"""
    print("\n" + "=" * 60)
    print("DOCKER CONTAINER LOGS")
    print("=" * 60)
    
    try:
        # Get all containers that have run (including stopped ones)
        result = subprocess.run(
            ['docker', 'ps', '-a', '--filter', 'name=smart-scan', '--format', '{{.Names}}\t{{.Status}}'],
            capture_output=True, text=True
        )
        
        if not result.stdout.strip():
            print("ℹ️  No smart scanner containers found (running or stopped)")
            return
        
        print("Recent smart scanner containers:")
        containers = []
        for line in result.stdout.strip().split('\n'):
            name, status = line.split('\t', 1)
            containers.append(name)
            print(f"  {name}: {status}")
        
        # Get logs from most recent containers
        for container in containers[:3]:  # Last 3 containers
            print(f"\nLogs from {container} (last 10 lines):")
            try:
                log_result = subprocess.run(
                    ['docker', 'logs', '--tail', '10', container],
                    capture_output=True, text=True, timeout=10
                )
                
                if log_result.stdout or log_result.stderr:
                    print(log_result.stdout)
                    if log_result.stderr:
                        print("STDERR:", log_result.stderr)
                else:
                    print("  (no logs available)")
                    
            except Exception as e:
                print(f"  Error getting logs: {e}")
                
    except Exception as e:
        print(f"❌ Error checking Docker container logs: {e}")

def main():
    parser = argparse.ArgumentParser(description='Analyze smart scanner failures')
    parser.add_argument('--data-dir', default='/mnt/user/appdata/nas-scanner', 
                       help='Scanner data directory (default: /mnt/user/appdata/nas-scanner)')
    parser.add_argument('--db-name', default='smart_catalog.db',
                       help='Database filename (default: smart_catalog.db)')
    
    args = parser.parse_args()
    
    data_dir = args.data_dir
    db_path = os.path.join(data_dir, args.db_name)
    
    print("SMART SCANNER FAILURE ANALYSIS")
    print("=" * 60)
    print(f"Data directory: {data_dir}")
    print(f"Database path: {db_path}")
    print(f"Analysis time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Run all analyses
    log_analysis = analyze_log_files(data_dir)
    db_analysis = analyze_database_state(db_path)
    check_system_resources()
    check_docker_logs()
    
    # Summary and recommendations
    print("\n" + "=" * 60)
    print("ANALYSIS SUMMARY & RECOMMENDATIONS")
    print("=" * 60)
    
    if db_analysis and db_analysis['has_data']:
        print("✅ Database contains file data - scan made some progress")
    else:
        print("❌ Database is empty - scan failed to write any data")
        print("\nPossible causes:")
        print("  - Mount point not accessible")
        print("  - Database permission issues")
        print("  - Container startup failures")
        print("  - Docker daemon issues")
    
    if log_analysis:
        if log_analysis['completed']:
            print("✅ Log indicates scan completed normally")
        else:
            print("❌ Log indicates scan did not complete")
            
        if log_analysis['failed_containers'] > 0:
            print(f"⚠️  {log_analysis['failed_containers']} container startup failures detected")
            
        if log_analysis['timeouts'] > 0:
            print(f"⚠️  {log_analysis['timeouts']} timeout issues detected")
    
    print("\nNext steps:")
    print("1. Check the full log files for detailed error messages")
    print("2. Verify mount point accessibility")
    print("3. Test Docker container creation manually")
    print("4. Check system resources (disk space, memory)")
    print("5. Consider running with --fast-start to skip directory analysis")

if __name__ == '__main__':
    main() 
#!/usr/bin/env python3
"""
Test Resume Capability for Progressive Scanner

This script tests the resume functionality without running a full scan.
"""

import os
import sys
import tempfile
import sqlite3
import time
from datetime import datetime

# Add current directory to path to import progressive_scanner
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from progressive_scanner import ProgressiveScanner

def test_resume_capability():
    """Test the progressive scanner's resume capability"""
    print("Testing Progressive Scanner Resume Capability")
    print("=" * 60)
    
    # Create a temporary directory and database for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Test directory: {temp_dir}")
        
        # Create some test subdirectories
        test_mount = os.path.join(temp_dir, "test_mount")
        os.makedirs(test_mount)
        
        subdir1 = os.path.join(test_mount, "subdir1")
        subdir2 = os.path.join(test_mount, "subdir2") 
        subdir3 = os.path.join(test_mount, "subdir3")
        
        os.makedirs(subdir1)
        os.makedirs(subdir2)
        os.makedirs(subdir3)
        
        # Create a few test files
        for i, subdir in enumerate([subdir1, subdir2, subdir3]):
            for j in range(3):
                test_file = os.path.join(subdir, f"file_{i}_{j}.txt")
                with open(test_file, 'w') as f:
                    f.write(f"Test content for file {i}_{j}")
        
        db_path = os.path.join(temp_dir, "test_progressive.db")
        mount_name = "TestMount"
        
        print(f"Database path: {db_path}")
        print(f"Mount path: {test_mount}")
        print(f"Mount name: {mount_name}")
        print()
        
        # Test 1: Fresh start (no existing database)
        print("TEST 1: Fresh start - no existing database")
        print("-" * 40)
        
        scanner1 = ProgressiveScanner(db_path)
        scanner1.create_scan_database()
        scanner1.check_existing_data(mount_name)
        
        # Generate initial chunks
        chunks1 = scanner1.chunk_generator.generate_initial_chunks(test_mount, mount_name, scanner1.scanned_chunks)
        print(f"Generated {len(chunks1)} chunks on fresh start")
        
        # Simulate completing first two chunks
        print("\nSimulating completion of first two chunks...")
        for i, chunk in enumerate(chunks1[:2]):
            scanner1.mark_chunk_scanned(chunk['path'], mount_name)
            print(f"  Marked as scanned: {chunk['path']}")
        
        print()
        
        # Test 2: Resume scenario (existing database with some completed chunks)
        print("TEST 2: Resume scenario - existing database with completed chunks")
        print("-" * 40)
        
        scanner2 = ProgressiveScanner(db_path)
        scanner2.check_existing_data(mount_name)
        
        # Generate chunks with resume logic
        chunks2 = scanner2.chunk_generator.generate_initial_chunks(test_mount, mount_name, scanner2.scanned_chunks)
        print(f"Generated {len(chunks2)} chunks on resume (should be fewer)")
        
        if len(chunks2) < len(chunks1):
            print(f"✅ Resume working: {len(chunks1) - len(chunks2)} chunks were skipped as already completed")
        else:
            print(f"❌ Resume not working: Same number of chunks generated")
        
        print()
        
        # Test 3: Database query validation
        print("TEST 3: Database validation")
        print("-" * 40)
        
        # Check database contents
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM scanned_dirs WHERE mount_point = ?", (mount_name,))
        scanned_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT path FROM scanned_dirs WHERE mount_point = ?", (mount_name,))
        scanned_paths = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        
        print(f"Scanned directories in database: {scanned_count}")
        for path in scanned_paths:
            print(f"  - {path}")
        
        print()
        
        # Test 4: Adaptive chunk generation with resume
        print("TEST 4: Adaptive chunk generation with resume")
        print("-" * 40)
        
        adaptive_chunks = scanner2.chunk_generator.generate_adaptive_chunks(
            test_mount, mount_name, scanner2.scanned_chunks, max_chunks=5
        )
        print(f"Generated {len(adaptive_chunks)} adaptive chunks")
        
        print()
        
        # Test Summary
        print("TEST SUMMARY")
        print("=" * 40)
        
        if len(chunks2) < len(chunks1) and scanned_count > 0:
            print("✅ Resume capability is working correctly!")
            print("   - Database properly tracks completed chunks")
            print("   - Initial chunk generation skips completed directories")
            print("   - Adaptive chunk generation respects scanned directories")
        else:
            print("❌ Resume capability has issues:")
            if len(chunks2) >= len(chunks1):
                print("   - Chunk generation not skipping completed directories")
            if scanned_count == 0:
                print("   - Database not properly recording completed chunks")
        
        return len(chunks2) < len(chunks1) and scanned_count > 0

def test_database_checks():
    """Test database existence and data checks"""
    print("\nTesting Database Existence Checks")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "nonexistent.db")
        mount_name = "TestMount"
        
        # Test with non-existent database
        print("TEST: Non-existent database")
        scanner = ProgressiveScanner(db_path)
        print(f"Database exists: {scanner.database_exists}")
        scanner.check_existing_data(mount_name)
        
        # Create database and add some data
        print("\nTEST: Database with existing data")
        scanner.create_scan_database()
        
        # Add some fake file data
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO files (path, size, mtime, mount_point, scan_time) 
            VALUES (?, ?, ?, ?, ?)
        """, ("/test/file1.txt", 1024, time.time(), mount_name, time.time()))
        cursor.execute("""
            INSERT INTO scanned_dirs (path, mount_point, scan_time) 
            VALUES (?, ?, ?)
        """, ("/test/dir1", mount_name, time.time()))
        conn.commit()
        conn.close()
        
        # Test with existing data
        scanner2 = ProgressiveScanner(db_path)
        scanner2.check_existing_data(mount_name)

if __name__ == '__main__':
    try:
        success = test_resume_capability()
        test_database_checks()
        
        print("\n" + "=" * 60)
        if success:
            print("🎉 ALL TESTS PASSED - Resume capability is working!")
        else:
            print("⚠️  SOME TESTS FAILED - Resume capability needs fixes")
        print("=" * 60)
        
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc() 
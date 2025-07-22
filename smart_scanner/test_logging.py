#!/usr/bin/env python3
"""
Test script for smart scanner logging improvements

This script tests the new logging functionality without running a full scan.
"""

import os
import sys
import tempfile
import sqlite3
import time
from datetime import datetime

# Add current directory to path to import smart_scanner
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smart_scanner import setup_logging, SmartScanner

def test_logging():
    """Test the enhanced logging functionality"""
    print("Testing Smart Scanner Logging Improvements")
    print("=" * 50)
    
    # Create temporary directory for test
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Using temporary directory: {temp_dir}")
        
        # Test log file creation
        log_file = os.path.join(temp_dir, "test_scan.log")
        logger = setup_logging(log_file)
        
        print("✅ Logger created successfully")
        
        # Test logging to both console and file
        logger.info("Test info message")
        logger.warning("Test warning message")
        logger.error("Test error message")
        
        # Check if log file was created and contains messages
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                log_content = f.read()
            
            if "Test info message" in log_content:
                print("✅ File logging working")
            else:
                print("❌ File logging not working")
        else:
            print("❌ Log file not created")
        
        # Test SmartScanner initialization with logging
        db_path = os.path.join(temp_dir, "test.db")
        
        try:
            scanner = SmartScanner(db_path, skip_analysis=True)
            print("✅ SmartScanner initialization with logging successful")
            
            # Test database creation
            if os.path.exists(db_path):
                print("✅ Database created successfully")
                
                # Test database schema
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]
                conn.close()
                
                expected_tables = ['files', 'scan_stats', 'scanned_dirs']
                if all(table in tables for table in expected_tables):
                    print("✅ Database schema created correctly")
                else:
                    print(f"❌ Database schema incomplete. Found: {tables}")
            else:
                print("❌ Database not created")
                
        except Exception as e:
            print(f"❌ SmartScanner initialization failed: {e}")
        
        # Test _check_database_activity method
        try:
            activity = scanner._check_database_activity({'mount_name': 'test'})
            print("✅ Database activity check working")
        except Exception as e:
            print(f"❌ Database activity check failed: {e}")
        
        print("\nTest completed!")
        print(f"Log file contents:")
        print("-" * 30)
        try:
            with open(log_file, 'r') as f:
                print(f.read())
        except Exception as e:
            print(f"Could not read log file: {e}")

if __name__ == '__main__':
    test_logging() 
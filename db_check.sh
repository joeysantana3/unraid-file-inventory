#!/bin/bash
find . -type f -name "*.db" | while read -r dbfile; do
  echo "Found: $dbfile"
  echo "Checking $dbfile"
  sqlite3 $dbfile "SELECT COUNT(*) FROM files;"
  sqlite3 $dbfile .schema
  sqlite3 $dbfile .tables
done
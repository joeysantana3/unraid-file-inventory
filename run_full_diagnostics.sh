#!/bin/bash

echo "=== DOCKER CONTAINER DIAGNOSTICS (HOST) ==="
echo "Time: $(date)"
echo ""

echo "1. CONTAINER STATUS:"
docker ps --filter "name=nas-hp-" --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"
echo ""

echo "2. CONTAINER CPU/MEMORY (last 10 seconds):"
docker stats --no-stream $(docker ps --filter "name=nas-hp-" -q) 2>/dev/null
echo ""

echo "3. RECENT CONTAINER LOGS (last 3 lines each):"
docker ps --filter "name=nas-hp-" --format "{{.Names}}" | while read container; do
    echo "--- $container ---"
    docker logs --tail 3 "$container" 2>&1 | sed 's/^/  /'
done
echo ""

echo "4. MOUNT POINT QUICK CHECK:"
for mount in /mnt/user/Movies /mnt/user/Music /mnt/user/Photos; do
    if [ -d "$mount" ]; then
        echo -n "$(basename $mount): "
        if timeout 3 ls "$mount" >/dev/null 2>&1; then
            echo "✅ accessible"
        else
            echo "❌ slow/inaccessible"
        fi
    fi
done
echo ""

echo "=== SYSTEM DIAGNOSTICS (CONTAINER) ==="
docker exec -it nas_diag /usr/local/bin/diagnose_now.sh
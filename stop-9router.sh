#!/bin/bash
# 9router Stop Script

echo "Stopping 9router..."

# Find and kill the process on port 20128
PID=$(lsof -ti :20128)

if [ -z "$PID" ]; then
    echo "9router is not running"
    exit 0
fi

kill $PID 2>/dev/null
sleep 2

# Check if still running
if lsof -i :20128 > /dev/null 2>&1; then
    echo "Force killing..."
    kill -9 $PID 2>/dev/null
fi

echo "9router stopped"

#!/bin/bash
# 9router Production Startup Script

cd /Users/irobotx/.openclaw/workspace/9router

# Check if already running
if lsof -i :20128 > /dev/null 2>&1; then
    echo "9router is already running on port 20128"
    exit 0
fi

# Start in production mode
PORT=20128 nohup npm start > /tmp/9router-prod.log 2>&1 &
echo "9router started in production mode (PID: $!)"
echo "Access at: http://localhost:20128"
echo "Logs: /tmp/9router-prod.log"

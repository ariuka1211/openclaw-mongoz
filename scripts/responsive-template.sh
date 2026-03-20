#!/bin/bash
# Template for responsive long-running scripts
# Polls every 30s to check for interruptions

cleanup() {
    echo "🔴 Process interrupted"
    exit 1
}
trap cleanup SIGTERM SIGINT

count=0
while [ $count -lt 1000 ]; do
    # Do work here
    echo "Working... $count"
    count=$((count + 1))
    
    # Poll every 30 seconds
    sleep 30
done
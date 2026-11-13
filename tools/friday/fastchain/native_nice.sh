#!/bin/bash
# E8 A/B helper (measurement only): keep this user's native processes (dotnet on a
# candidate-* artifact) at nice $1; every 60 s log the nice histogram to $2.
n=$1; log=$2; i=0
while true; do
  ps -o pid=,ni=,args= -u "$USER" | awk -v n="$n" '$2!=n && /dotnet/ && /candidate-/ {print $1}' | xargs -r renice -n "$n" -p >/dev/null 2>&1
  i=$((i + 1))
  if [ $((i % 60)) = 0 ]; then
    echo "$(date +%s) $(ps -o ni=,args= -u "$USER" | awk '/dotnet/ && /candidate-/ {print "ni" $1}' | sort | uniq -c | tr '\n' ' ')" >> "$log"
  fi
  sleep 1
done

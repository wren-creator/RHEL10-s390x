#!/usr/bin/env bash
# studio.sh — start/stop the RHEL 10 Image Mode Studio web server.
#
#   ./studio.sh start      # launch in the background, print the URL
#   ./studio.sh stop       # stop it
#   ./studio.sh restart    # stop then start
#   ./studio.sh status     # is it running?
#   ./studio.sh logs       # follow the log (Ctrl-C to stop following)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$HERE/bootc-builder-server.py"
PIDFILE="$HERE/.studio.pid"
LOGFILE="$HERE/studio.log"
PORT=8080

PY="$(command -v python3 || command -v python || true)"

is_running() {
  [ -f "$PIDFILE" ] || return 1
  local pid; pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

show_url() {
  # The server logs its LAN URL on startup; surface it plus localhost.
  local lan; lan="$(grep -oE 'http://[0-9.]+:[0-9]+' "$LOGFILE" 2>/dev/null | tail -1 || true)"
  echo "  → http://localhost:$PORT"
  [ -n "$lan" ] && echo "  → $lan   (from other machines on your network)"
}

start() {
  local pid existing_pid

  # Clean up a stale PID file (process no longer alive).
  if [[ -f "$PIDFILE" ]]; then
    pid=$(<"$PIDFILE")
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Removing stale PID file."
      rm -f "$PIDFILE"
    fi
  fi

  # Already running under a different/no PID file?
  existing_pid=$(pgrep -f "bootc-builder-server.py" | head -n1)
  if [[ -n "$existing_pid" ]]; then
    echo "Image Mode Studio already running (pid $existing_pid)."
    show_url
    return 0
  fi

  # Something else already holds the port?
  existing_pid=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null)
  if [[ -n "$existing_pid" ]]; then
    echo "✗ Cannot start Image Mode Studio."
    echo
    echo "Port $PORT is already in use:"
    ps -fp "$existing_pid"
    echo
    echo "Stop the existing process or run on a different port."
    return 1
  fi

  [ -n "$PY" ] || { echo "✗ python3 (or python) not found on PATH."; return 1; }
  [ -f "$APP" ] || { echo "✗ $APP not found."; return 1; }

  echo "Starting Image Mode Studio..."
  nohup "$PY" "$APP" >"$LOGFILE" 2>&1 &
  pid=$!
  echo "$pid" >"$PIDFILE"

  # Wait up to 10s for the server to actually bind the port (not just launch).
  for _ in {1..10}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      break   # process died
    fi
    if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "✓ Started (pid $pid)."
      show_url
      echo "  logs: ./studio.sh logs"
      return 0
    fi
    sleep 1
  done

  echo "✗ Failed to start — last log lines:"
  echo
  tail -n 20 "$LOGFILE" 2>/dev/null || true
  rm -f "$PIDFILE"
  return 1
}

stop() {
  local pid
  local pids=()

  echo "Stopping Image Mode Studio..."

  # 1. PID file process (only if it still matches our server).
  if [[ -f "$PIDFILE" ]]; then
    pid=$(<"$PIDFILE")
    if [[ "$pid" =~ ^[0-9]+$ ]] && ps -p "$pid" -o args= 2>/dev/null | grep -q "bootc-builder-server.py"; then
      pids+=("$pid")
    else
      echo "Removing stale PID file."
    fi
  fi

  # 2. Any other matching server processes (covers duplicate/manual starts).
  while read -r pid; do
    [[ -n "$pid" ]] && pids+=("$pid")
  done < <(pgrep -f "bootc-builder-server.py" | sort -u)

  # 3. Nothing to stop.
  if [[ ${#pids[@]} -eq 0 ]]; then
    rm -f "$PIDFILE"
    echo "Not running."
    return 0
  fi

  mapfile -t pids < <(printf '%s\n' "${pids[@]}" | sort -u)
  echo "Found process(es): ${pids[*]}"

  # 4. Graceful shutdown.
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || sudo kill "$pid" 2>/dev/null || true
  done

  # 5. Wait up to 6 seconds.
  for _ in {1..20}; do
    local running=0
    for pid in "${pids[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        running=1
        break
      fi
    done
    (( running == 0 )) && break
    sleep 0.3
  done

  # 6. Force kill survivors.
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      echo "Force killing $pid"
      kill -9 "$pid" 2>/dev/null || sudo kill -9 "$pid" 2>/dev/null || true
    fi
  done

  # 7. Verify the port is actually free.
  sleep 1
  if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "WARNING: Port $PORT is still in use:"
    lsof -iTCP:"$PORT" -sTCP:LISTEN
    return 1
  fi

  rm -f "$PIDFILE"
  echo "✓ Stopped."
}

status() {
  if is_running; then
    echo "running (pid $(cat "$PIDFILE"))"
    show_url
  else
    echo "stopped"
  fi
}

case "${1:-}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  logs)    tail -f "$LOGFILE" ;;
  *) echo "Usage: $0 {start|stop|restart|status|logs}"; exit 2 ;;
esac

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
  if is_running; then
    echo "Image Mode Studio already running (pid $(cat "$PIDFILE"))."
    show_url
    return 0
  fi
  [ -n "$PY" ] || { echo "✗ python3 (or python) not found on PATH."; exit 1; }
  [ -f "$APP" ] || { echo "✗ $APP not found."; exit 1; }
  echo "Starting Image Mode Studio..."
  nohup "$PY" "$APP" >"$LOGFILE" 2>&1 &
  echo $! >"$PIDFILE"
  sleep 1
  if is_running; then
    echo "✓ Started (pid $(cat "$PIDFILE"))."
    show_url
    echo "  logs: ./studio.sh logs"
  else
    echo "✗ Failed to start — last log lines:"
    tail -n 20 "$LOGFILE" 2>/dev/null || true
    rm -f "$PIDFILE"
    exit 1
  fi
}

stop() {
  if ! is_running; then
    echo "Not running."
    rm -f "$PIDFILE"
    return 0
  fi
  local pid; pid="$(cat "$PIDFILE")"
  echo "Stopping (pid $pid)..."
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do is_running || break; sleep 0.3; done
  if is_running; then kill -9 "$pid" 2>/dev/null || true; fi
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

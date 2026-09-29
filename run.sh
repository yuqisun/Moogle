#!/usr/bin/env bash
# Moogle launcher for Linux/macOS
# Starts Streamlit chat (8501) + static file server with Range support (8502)
#
# Usage:
#   ./run.sh                  # bind 127.0.0.1 (local only)
#   ./run.sh --host 0.0.0.0   # bind all interfaces (remote access)
#
# Prerequisites:
#   python3 -m venv .venv
#   .venv/bin/pip install -r requirements.txt
#   cp .env.example .env   # then edit .env with your LLM_API_KEY etc.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# --- Detect Python / venv ---
if [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
    STREAMLIT="$ROOT/.venv/bin/streamlit"
elif command -v python3 &>/dev/null; then
    PY="$(command -v python3)"
    STREAMLIT="$(command -v streamlit 2>/dev/null || echo "")"
else
    echo "ERROR: No Python found. Install Python 3.8+ and create a venv:" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

if [ ! -x "${STREAMLIT:-}" ] && [ -z "${STREAMLIT:-}" ]; then
    # Try the venv path as fallback
    STREAMLIT="$ROOT/.venv/bin/streamlit"
fi
if [ ! -x "${STREAMLIT:-}" ]; then
    echo "ERROR: streamlit not found. Install it:" >&2
    echo "  $PY -m pip install -r requirements.txt" >&2
    exit 1
fi

# --- Read config from .env ---
STATIC_PORT=8502
STREAMLIT_PORT=8501
BIND_HOST="127.0.0.1"

if [ -f "$ROOT/.env" ]; then
    while IFS='=' read -r key value; do
        # Skip comments and blank lines
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${key// }" ]] && continue
        key="${key%%#*}"           # strip inline comments
        key="$(echo "$key" | xargs)"   # trim whitespace
        value="$(echo "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | sed 's/^["'"'"']//;s/["'"'"']$//')"
        case "$key" in
            STATIC_PORT)    STATIC_PORT="$value" ;;
            STREAMLIT_PORT) STREAMLIT_PORT="$value" ;;
        esac
    done < "$ROOT/.env"
fi

# --- Parse CLI args ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            BIND_HOST="$2"
            shift 2
            ;;
        --host=*)
            BIND_HOST="${1#*=}"
            shift
            ;;
        --streamlit-port)
            STREAMLIT_PORT="$2"
            shift 2
            ;;
        --static-port)
            STATIC_PORT="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--host ADDR] [--streamlit-port N] [--static-port N]"
            echo ""
            echo "Options:"
            echo "  --host ADDR          Bind address (default: 127.0.0.1, use 0.0.0.0 for remote)"
            echo "  --streamlit-port N   Streamlit port (default: 8501)"
            echo "  --static-port N      Static file server port (default: 8502)"
            echo "  -h, --help           Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1 (use --help for usage)" >&2
            exit 1
            ;;
    esac
done

# --- Check if ports are already in use ---
for port in "$STREAMLIT_PORT" "$STATIC_PORT"; do
    if command -v ss &>/dev/null; then
        if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
            pid=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP 'pid=\K[0-9]+' | head -1)
            proc=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
            echo "ERROR: Port $port is already in use by PID $pid ($proc)." >&2
            echo "       Stop the existing process or close the other Moogle instance first." >&2
            exit 1
        fi
    elif command -v lsof &>/dev/null; then
        if lsof -i ":$port" -sTCP:LISTEN &>/dev/null; then
            pid=$(lsof -ti ":$port" -sTCP:LISTEN 2>/dev/null | head -1)
            proc=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
            echo "ERROR: Port $port is already in use by PID $pid ($proc)." >&2
            echo "       Stop the existing process or close the other Moogle instance first." >&2
            exit 1
        fi
    fi
done

# --- Cleanup on exit ---
STATIC_PID=""
cleanup() {
    if [ -n "$STATIC_PID" ] && kill -0 "$STATIC_PID" 2>/dev/null; then
        kill "$STATIC_PID" 2>/dev/null
        wait "$STATIC_PID" 2>/dev/null
        echo "Stopped static server (PID $STATIC_PID)."
    fi
}
trap cleanup EXIT INT TERM

# --- Start static file server (background) ---
echo "Starting static file server on ${BIND_HOST}:${STATIC_PORT} (Range requests enabled) ..."
"$PY" "$ROOT/static_server.py" "$STATIC_PORT" --bind "$BIND_HOST" --directory "$ROOT/static" &
STATIC_PID=$!

# Wait briefly and verify it started
sleep 1
if ! kill -0 "$STATIC_PID" 2>/dev/null; then
    echo "ERROR: Static file server failed to start." >&2
    exit 1
fi

# --- Start Streamlit (foreground) ---
echo "Starting Streamlit on ${BIND_HOST}:${STREAMLIT_PORT} ..."
echo ""
if [ "$BIND_HOST" = "0.0.0.0" ]; then
    echo "  Local:   http://127.0.0.1:${STREAMLIT_PORT}"
    echo "  Network: http://<server-ip>:${STREAMLIT_PORT}"
else
    echo "  URL: http://${BIND_HOST}:${STREAMLIT_PORT}"
fi
echo ""

exec "$STREAMLIT" run streamlit_app.py \
    --server.port "$STREAMLIT_PORT" \
    --server.address "$BIND_HOST" \
    --server.headless true \
    --browser.gatherUsageStats false

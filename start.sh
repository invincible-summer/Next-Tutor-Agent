#!/usr/bin/env bash

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# This deployment runs with direct network access. Do not inherit a stale
# HTTP proxy settings into LLM/Embedding/OCR
# clients or the Next.js process.
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY 2>/dev/null || true
echo "[start.sh] direct network: proxy environment disabled"

# --- Activate conda if available (WSL2 + Miniconda) ---
if command -v conda &>/dev/null; then
    eval "$(conda shell.bash hook)" 2>/dev/null
    conda activate edu_agent 2>/dev/null || true
fi
# Prefer conda python; fall back to system python3
PYTHON_BIN="python"
if ! command -v "$PYTHON_BIN" &>/dev/null; then
    PYTHON_BIN="python3"
fi

read_nonsecret_env() {
    local name="$1" value=""
    if [ -f "$ROOT/.env" ]; then
        value="$(grep -E "^${name}=" "$ROOT/.env" | tail -1 | cut -d= -f2-)"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
    fi
    printf '%s' "$value"
}

set_runtime_default() {
    local name="$1" default="$2" configured=""
    if [ -n "${!name:-}" ]; then return; fi
    configured="$(read_nonsecret_env "$name")"
    export "$name=${configured:-$default}"
}

# start.sh starts the latest complete Agent runtime by default. Explicit shell
# or non-secret .env values still win and can roll back one layer independently.
set_runtime_default SUPERVISOR_MODE v2
set_runtime_default SKILL_RUNTIME_MODE gated
set_runtime_default LLM_RUNTIME_MODE adapter
set_runtime_default TOOL_CONTEXT_PROJECTION_MODE on
set_runtime_default TOOL_MESSAGE_MODE native
set_runtime_default REASONING_SUMMARY_LEVEL adaptive
# Frontend run mode: prod (default) builds once + `next start` — minified
# bundles, Link prefetching, no on-demand compile (dev-mode page loads are
# seconds-slow, especially under WSL2). FRONTEND_MODE=dev keeps the classic
# hot-reload dev server for active development. The `dev` subcommand is an
# explicit override (wins over shell/.env) and must be resolved before the
# runtime echo so the log line reflects the actual mode.
if [ "${1:-all}" = "dev" ]; then FRONTEND_MODE=dev; fi
set_runtime_default FRONTEND_MODE prod
echo "[start.sh] runtime: supervisor=$SUPERVISOR_MODE skill=$SKILL_RUNTIME_MODE llm=$LLM_RUNTIME_MODE tool_context=$TOOL_CONTEXT_PROJECTION_MODE tool_messages=$TOOL_MESSAGE_MODE reasoning_summary=$REASONING_SUMMARY_LEVEL frontend=$FRONTEND_MODE"

BACK_PID=""; FRONT_PID=""; VOICE_PID=""; BACK_PORT=""; FRONT_PORT=""; VOICE_PORT=""

cleanup() {
    # Ctrl+C / TERM: kill direct children, then free the ports as a fallback.
    # A plain kill often strands the real servers (uvicorn workers, the
    # next-server process behind `npx next dev`), which then keep holding
    # :8000/:3000 and force the next run onto fallback ports.
    kill $BACK_PID $FRONT_PID $VOICE_PID 2>/dev/null || true
    sleep 1
    [ -n "$BACK_PORT" ] && fuser -k "$BACK_PORT/tcp" 2>/dev/null || true
    [ -n "$FRONT_PORT" ] && fuser -k "$FRONT_PORT/tcp" 2>/dev/null || true
    [ -n "$VOICE_PORT" ] && fuser -k "$VOICE_PORT/tcp" 2>/dev/null || true
}
# EXIT included so a set -e abort mid-start (e.g. a failed production build in
# `all` mode) also tears down the already-started backend instead of orphaning
# it on the port. Idempotent after the INT/TERM handler.
trap cleanup INT TERM EXIT

pick_port() {
    local preferred="$1"; shift
    local candidates=("$preferred" "$@")
    for port in "${candidates[@]}"; do
        if "$PYTHON_BIN" -c "
import socket, sys
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try: s.bind(('0.0.0.0', $port)); s.close(); sys.exit(0)
except OSError: sys.exit(1)" 2>/dev/null; then
            echo "$port"; return 0
        fi
    done
    echo "$preferred"
}

configure_local_cors() {
    # An explicit shell/.env CORS_ORIGINS wins; otherwise allow the selected
    # local frontend port. Read only this non-secret setting from .env rather
    # than sourcing the file (which could expose API keys to child processes).
    if [ -n "${CORS_ORIGINS:-}" ]; then
        return
    fi
    local configured=""
    if [ -f "$ROOT/.env" ]; then
        configured="$(grep -E '^CORS_ORIGINS=' "$ROOT/.env" | tail -1 | cut -d= -f2-)"
        configured="${configured%\"}"; configured="${configured#\"}"
        configured="${configured%\'}"; configured="${configured#\'}"
    fi
    if [ -n "$configured" ]; then
        export CORS_ORIGINS="$configured"
    else
        export CORS_ORIGINS="http://localhost:$FRONT_PORT,http://127.0.0.1:$FRONT_PORT,http://0.0.0.0:$FRONT_PORT"
    fi
}

prepare_ports() {
    BACK_PORT="$(pick_port 8123 8000 8124)"
    FRONT_PORT="$(pick_port 3001 3000 3030)"
    configure_local_cors
    echo "[start.sh] CORS_ORIGINS=$CORS_ORIGINS"
}

start_backend() {
    local port
    port="${BACK_PORT:-$(pick_port 8123 8000 8124)}"
    echo "[start.sh] backend on :$port"
    cd "$ROOT/backend"
    BACKEND_PORT="$port" "$PYTHON_BIN" -m uvicorn app.main:app --host 0.0.0.0 --port "$port" --proxy-headers &
    BACK_PID=$!
    BACK_PORT="$port"
    echo "$port" > /tmp/edu_backend_port
    echo "$BACK_PID" > /tmp/edu_backend_pid
}

start_voice_sidecar() {
    # P10 语音：VOICE_TTS_PROVIDER=melo 时拉起 MeloTTS sidecar（自带 venv，
    # 见 deploy/install_voice.sh）。未安装/未启用时仅提示，主服务照常运行
    # （fail-open：后端把语音请求报告为 tts 不可用，聊天完全不受影响）。
    set_runtime_default VOICE_TTS_PROVIDER off
    if [ "$VOICE_TTS_PROVIDER" != "melo" ]; then
        return 0
    fi
    if [ ! -x "$ROOT/backend/voice_sidecar/.venv/bin/python" ]; then
        echo "[start.sh] VOICE_TTS_PROVIDER=melo 但 sidecar venv 缺失（bash deploy/install_voice.sh）；语音 TTS 将不可用"
        return 0
    fi
    VOICE_PORT="$(pick_port 8130 8131 8132)"
    # 显式 VOICE_TTS_BASE_URL（shell/.env）优先，否则指向本次选中的端口。
    set_runtime_default VOICE_TTS_BASE_URL "http://127.0.0.1:$VOICE_PORT"
    echo "[start.sh] voice sidecar on :$VOICE_PORT (MeloTTS-Chinese, CPU)"
    (
        cd "$ROOT/backend/voice_sidecar"
        HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
        HF_HOME="$ROOT/backend/models/voice/hf" \
        OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
        ./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "$VOICE_PORT" &
        echo $! > /tmp/edu_voice_pid
    )
    VOICE_PID="$(cat /tmp/edu_voice_pid 2>/dev/null || true)"
    echo "$VOICE_PORT" > /tmp/edu_voice_port
    # 首次加载模型约需 20-60s；不阻塞启动，健康检查在后台确认。
    (
        for _ in $(seq 1 90); do
            curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:$VOICE_PORT/health" 2>/dev/null && {
                echo "[start.sh] voice sidecar ready"; exit 0; }
            sleep 1
        done
        echo "[start.sh] voice sidecar 未在 90s 内就绪（首次加载较慢属正常，稍后自动可用）"
    ) &
}

# Is a production (re)build required? NEXT_PUBLIC_ vars are inlined at build
# time, so a backend port change forces a rebuild; the baked port is recorded
# in .next/edu-build-port (inside .next so the dev-mode wipe invalidates it).
frontend_build_needed() {
    local bport="$1"
    [ ! -f .next/BUILD_ID ] && return 0
    [ "${REBUILD:-0}" = "1" ] && return 0
    # NEXT_PUBLIC_* 在构建期内联，rewrites 也烙进 manifest：未经 start.sh 环境
    # 构建的包（手动 next build）会把同源回退代理固化到 127.0.0.1:8000，端口
    # 回退到 8123 时全部 /api 请求 ECONNREFUSED。edu-build-port 只有
    # build_frontend 会写——缺失或比 BUILD_ID 旧（外部构建覆盖了我们烙的包）
    # 都必须重建。
    [ ! -f .next/edu-build-port ] && return 0
    [ "$(cat .next/edu-build-port)" != "$bport" ] && return 0
    [ .next/edu-build-port -ot .next/BUILD_ID ] && return 0
    # Source newer than the last build -> stale bundle.
    [ -n "$(find src public next.config.ts package.json -newer .next/BUILD_ID -print -quit 2>/dev/null)" ] && return 0
    return 1
}

build_frontend() {
    local bport="$1"
    echo "[start.sh] building frontend (next build --webpack, backend :$bport baked in; first build ~1-2 min)"
    # NEXT_PUBLIC_* 内联给客户端直连；BACKEND_URL 供 rewrites() 构建期求值——
    # 缺了会把同源回退代理固化到默认 8000，端口回退时 SSR/相对路径请求全断。
    if command -v pnpm &>/dev/null; then
        NEXT_PUBLIC_BACKEND_URL="http://localhost:$bport" \
        BACKEND_URL="http://localhost:$bport" \
        pnpm exec next build --webpack
    else
        NEXT_PUBLIC_BACKEND_URL="http://localhost:$bport" \
        BACKEND_URL="http://localhost:$bport" \
        npx next build --webpack
    fi
    echo "$bport" > .next/edu-build-port
}

start_frontend_prod() {
    local port="$1" bport="$2"
    if frontend_build_needed "$bport"; then
        build_frontend "$bport"
    else
        echo "[start.sh] reusing production build in frontend/.next (REBUILD=1 to force)"
    fi
    echo "[start.sh] frontend on :$port (prod next start, backend :$bport)"
    if command -v pnpm &>/dev/null; then
        BACKEND_URL="http://localhost:$bport" \
        pnpm exec next start -p "$port" -H 0.0.0.0 &
    else
        BACKEND_URL="http://localhost:$bport" \
        npx next start -p "$port" -H 0.0.0.0 &
    fi
    FRONT_PID=$!
}

start_frontend() {
    local bport; bport="$(cat /tmp/edu_backend_port 2>/dev/null || echo 8000)"
    local port
    port="${FRONT_PORT:-$(pick_port 3001 3000 3030)}"
    cd "$ROOT/frontend"
    if [ "$FRONTEND_MODE" = "prod" ]; then
        start_frontend_prod "$port" "$bport"
    else
        # next build and next dev share frontend/.next; a production build leaves
        # BUILD_ID behind and poisons the dev server (stale/mixed chunks -> weird
        # runtime errors). Wipe it automatically before starting dev.
        if [ -f "$ROOT/frontend/.next/BUILD_ID" ]; then
            echo "[start.sh] found production build in frontend/.next (BUILD_ID); cleaning for dev"
            rm -rf "$ROOT/frontend/.next"
        fi
        echo "[start.sh] frontend on :$port (dev, backend :$bport)"
        if command -v pnpm &>/dev/null; then
            BACKEND_URL="http://localhost:$bport" \
            NEXT_PUBLIC_BACKEND_URL="http://localhost:$bport" \
            pnpm exec next dev -p "$port" -H 0.0.0.0 &
        else
            BACKEND_URL="http://localhost:$bport" \
            NEXT_PUBLIC_BACKEND_URL="http://localhost:$bport" \
            npx next dev -p "$port" -H 0.0.0.0 &
        fi
        FRONT_PID=$!
    fi
    FRONT_PORT="$port"
    echo "$port" > /tmp/edu_frontend_port
    echo "$FRONT_PID" > /tmp/edu_frontend_pid
}

open_browser() {
    # Open the app in the desktop browser once the dev server actually
    # answers (next dev needs a few seconds to compile). AUTO_OPEN=0 disables.
    # Runs in the background so it never blocks startup.
    [ "${AUTO_OPEN:-1}" = "0" ] && return 0
    local url="http://localhost:$FRONT_PORT"
    for _ in $(seq 1 60); do
        curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null && break
        sleep 1
    done
    echo "[start.sh] opening $url"
    if [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qi microsoft /proc/version 2>/dev/null; then
        # WSL2: explorer.exe hands the URL to the Windows default browser
        # (xdg-open usually exists here but has no desktop to open).
        if command -v explorer.exe &>/dev/null; then
            explorer.exe "$url" &>/dev/null || true
        elif command -v powershell.exe &>/dev/null; then
            powershell.exe /c start "$url" &>/dev/null || true
        fi
    elif command -v wslview &>/dev/null; then
        wslview "$url" &>/dev/null
    else
        xdg-open "$url" &>/dev/null || true
    fi
}

stop_all() {
    local name pid_file port_file pid port cwd
    for name in backend frontend voice; do
        pid_file="/tmp/edu_${name}_pid"; port_file="/tmp/edu_${name}_port"
        pid="$(cat "$pid_file" 2>/dev/null || true)"
        port="$(cat "$port_file" 2>/dev/null || true)"
        if [ -n "$pid" ] && [ -d "/proc/$pid" ]; then
            cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
            if [[ "$cwd" == "$ROOT"* ]]; then
                kill "$pid" 2>/dev/null || true
                sleep 1
                [ -n "$port" ] && fuser -k "$port/tcp" 2>/dev/null || true
            fi
        fi
    done
    # Compatibility cleanup for processes started by older start.sh versions
    # that did not write PID files. Match BOTH cwd under this repository and a
    # known server command, so Paper_Agent and other projects are untouched.
    local proc cmd
    for proc in /proc/[0-9]*; do
        pid="${proc##*/}"
        [ "$pid" = "$$" ] && continue
        cwd="$(readlink -f "$proc/cwd" 2>/dev/null || true)"
        [[ "$cwd" == "$ROOT"* ]] || continue
        cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
        case "$cmd" in
            python\ -m\ uvicorn*|*/python\ -m\ uvicorn*|\
            node\ *next/dist/bin/next\ dev*|node\ *pnpm*\ exec\ next\ dev*|\
            sh\ -c\ next\ dev*|next-server*)
                kill "$pid" 2>/dev/null || true ;;
        esac
    done
    sleep 1
    : > /tmp/edu_backend_pid; : > /tmp/edu_frontend_pid; : > /tmp/edu_voice_pid
}

case "${1:-all}" in
    backend) start_voice_sidecar; start_backend ;;
    frontend) start_frontend; open_browser & ;;
    # Explicit dev-mode entry point (same as FRONTEND_MODE=dev, overrides .env):
    # hot-reload dev server, auto-wipes any production build in .next.
    dev) FRONTEND_MODE=dev; stop_all; prepare_ports; start_voice_sidecar; start_backend; sleep 2; start_frontend; open_browser & ;;
    all) stop_all; prepare_ports; start_voice_sidecar; start_backend; sleep 2; start_frontend; open_browser & ;;
    stop) stop_all ;;
    *) echo "Usage: $0 [all|backend|frontend|dev|stop]"; echo "  默认（all）生产模式：一次构建 + next start，源码/端口变化自动重建"; echo "  dev 子命令显式覆盖为热重载开发模式（等价 FRONTEND_MODE=dev）"; echo "  REBUILD=1 $0 ...           # 强制重建前端生产包"; exit 1 ;;
esac
wait

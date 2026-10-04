#!/usr/bin/env bash

set -eE
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Non-interactive launchers (IDE tasks, remote commands, agent runners) can be
# interrupted or reaped independently of the services. Run their supervisor in
# its own session, with closed input and durable logs, before installing traps.
RUNTIME_DIR="$ROOT/.runtime"
if [ "${1:-all}" = "daemon" ] \
    || { [ "${EDU_DETACHED:-0}" != "1" ] && [ "${ATTACHED:-0}" != "1" ] \
         && [ ! -t 0 ] && [ "${1:-all}" != "stop" ]; }; then
    mode="${1:-all}"
    if [ "$mode" = "daemon" ]; then mode="${2:-all}"; fi
    case "$mode" in all|dev|backend|frontend) ;; *) echo "Usage: $0 daemon [all|dev|backend|frontend]"; exit 1 ;; esac
    mkdir -p "$RUNTIME_DIR"
    chmod 700 "$RUNTIME_DIR"
    (
        umask 077
        EDU_DETACHED=1 nohup setsid "$ROOT/start.sh" "$mode" \
            >> "$RUNTIME_DIR/launcher.log" 2>&1 < /dev/null &
    )
    echo "[start.sh] 后台启动中；日志：$RUNTIME_DIR/launcher.log；停止：./start.sh stop"
    exit 0
fi

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

# 运行时状态文件（pid/port）写 /tmp 固定路径：拒绝穿透符号链接（本地低权
# 用户预置 symlink 让启动者覆盖任意可写文件的攻击面），并收紧到 0600。
write_runtime_file() {
    local file="$1" content="$2"
    if [ -L "$file" ]; then
        echo "[start.sh] refusing to write through symlink: $file" >&2
        exit 1
    fi
    printf '%s\n' "$content" > "$file"
    chmod 600 "$file" 2>/dev/null || true
}

# start.sh starts the latest complete Agent runtime by default. Explicit shell
# or non-secret .env values still win and can roll back one layer independently.
set_runtime_default SUPERVISOR_MODE v2
set_runtime_default SKILL_RUNTIME_MODE gated
set_runtime_default LLM_RUNTIME_MODE adapter
set_runtime_default TOOL_CONTEXT_PROJECTION_MODE on
set_runtime_default TOOL_MESSAGE_MODE native
# 服务绑定地址：默认只绑回环（WSL2 下 Windows 侧 localhost 仍可达）；
# 需要局域网设备直连时在 shell/.env 显式设置 0.0.0.0。生产走 nginx/TLS 反代。
set_runtime_default API_HOST 127.0.0.1
set_runtime_default FRONTEND_HOST 127.0.0.1
# Frontend run mode: prod (default) builds once + `next start` — minified
# bundles, Link prefetching, no on-demand compile (dev-mode page loads are
# seconds-slow, especially under WSL2). FRONTEND_MODE=dev keeps the classic
# hot-reload dev server for active development. The `dev` subcommand is an
# explicit override (wins over shell/.env) and must be resolved before the
# runtime echo so the log line reflects the actual mode.
if [ "${1:-all}" = "dev" ]; then FRONTEND_MODE=dev; fi
set_runtime_default FRONTEND_MODE prod
echo "[start.sh] runtime: supervisor=$SUPERVISOR_MODE skill=$SKILL_RUNTIME_MODE llm=$LLM_RUNTIME_MODE tool_context=$TOOL_CONTEXT_PROJECTION_MODE tool_messages=$TOOL_MESSAGE_MODE frontend=$FRONTEND_MODE"

BACK_PID=""; FRONT_PID=""; VOICE_PID=""; BACK_PORT=""; FRONT_PORT=""; VOICE_PORT=""
BACK_REF=""; FRONT_REF=""; VOICE_REF=""
EXIT_REASON="normal"

record_service() {
    local name="$1" pid="$2" reference
    reference="$("$PYTHON_BIN" "$ROOT/deploy/self-hosted/process_cleanup.py" --identity "$pid")"
    [ -n "$reference" ] || { echo "[start.sh] $name exited before registration"; return 1; }
    mkdir -p "$RUNTIME_DIR"
    chmod 700 "$RUNTIME_DIR"
    (umask 077; printf '%s\n' "$reference" > "$RUNTIME_DIR/$name.pid")
    case "$name" in backend) BACK_REF="$reference" ;; frontend) FRONT_REF="$reference" ;; voice) VOICE_REF="$reference" ;; esac
}

cleanup() {
    local exit_code=$?
    # One cleanup per launcher. A previous launcher must never kill a replacement
    # server simply because it has reused the same port.
    trap - EXIT
    # Repeated signals from a terminal, IDE, or process manager must not abort teardown.
    trap '' INT TERM HUP
    echo "[start.sh] launcher exit: reason=${EXIT_REASON:-normal} status=$exit_code; cleaning owned services"
    "$PYTHON_BIN" "$ROOT/deploy/self-hosted/process_cleanup.py" "$ROOT" \
        "${BACK_REF:-$BACK_PID}" "${FRONT_REF:-$FRONT_PID}" "${VOICE_REF:-$VOICE_PID}" || true
}
trap cleanup EXIT
trap 'EXIT_REASON=SIGINT; exit 130' INT
trap 'EXIT_REASON=SIGTERM; exit 143' TERM
trap 'EXIT_REASON=SIGHUP; exit 129' HUP
trap 'EXIT_REASON="command_failed_line_$LINENO"' ERR

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
    echo "[start.sh] backend on $API_HOST:$port"
    cd "$ROOT/services/api"
    BACKEND_PORT="$port" "$PYTHON_BIN" -m uvicorn app.main:app --host "$API_HOST" --port "$port" --proxy-headers &
    BACK_PID=$!
    record_service backend "$BACK_PID"
    BACK_PORT="$port"
    write_runtime_file /tmp/edu_backend_port "$port"
    write_runtime_file /tmp/edu_backend_pid "$BACK_PID"
}

start_voice_sidecar() {
    # 语音 sidecar（MeloTTS-Chinese，本地 CPU；电话 P10 + 课堂回退共用）。
    # 启动判定（任一成立即启动）：
    #   1) 旧电话 VOICE_TTS_PROVIDER=melo；
    #   2) 旧电话 provider=auto（默认本地）；
    #   3) 课堂启用且本地回退启用，且默认策略 local/auto 或允许本地回退。
    # 未安装 venv/模型时仅提示（fail-open）：语音按不可用降级为文字路径，
    # 聊天/课堂不受影响，也绝不临时安装大型模型。
    set_runtime_default VOICE_TTS_PROVIDER off
    set_runtime_default CLASSROOM_ENABLED 0
    set_runtime_default CLASSROOM_TTS_POLICY auto
    set_runtime_default CLASSROOM_TTS_LOCAL_FALLBACK 1
    # 课堂本地回退开关：未显式设置时继承旧电话是否为 melo/auto
    if [ -z "${CLASSROOM_LOCAL_TTS_ENABLED:-}" ]; then
        local configured_local
        configured_local="$(read_nonsecret_env CLASSROOM_LOCAL_TTS_ENABLED)"
        if [ -n "$configured_local" ]; then
            export CLASSROOM_LOCAL_TTS_ENABLED="$configured_local"
        elif [ "$VOICE_TTS_PROVIDER" = "melo" ] || [ "$VOICE_TTS_PROVIDER" = "auto" ]; then
            export CLASSROOM_LOCAL_TTS_ENABLED=1
        else
            export CLASSROOM_LOCAL_TTS_ENABLED=0
        fi
    fi
    local want=0
    if [ "$VOICE_TTS_PROVIDER" = "melo" ]; then
        want=1
    fi
    if [ "$VOICE_TTS_PROVIDER" = "auto" ]; then
        want=1
    fi
    if [ "$CLASSROOM_ENABLED" = "1" ] \
        && [ "$CLASSROOM_LOCAL_TTS_ENABLED" = "1" ]; then
        case "$CLASSROOM_TTS_POLICY" in
            local|auto) want=1 ;;
            cloud) [ "$CLASSROOM_TTS_LOCAL_FALLBACK" = "1" ] && want=1 ;;
        esac
    fi
    if [ "$want" != "1" ]; then
        return 0
    fi
    if [ ! -x "$ROOT/services/voice/.venv/bin/python" ]; then
        echo "[start.sh] 语音 sidecar 判定需要启动，但 venv 缺失（bash deploy/self-hosted/install_voice.sh）；语音 TTS 将不可用（文字课堂照常）"
        return 0
    fi
    VOICE_PORT="$(pick_port 8130 8131 8132)"
    # 显式 VOICE_TTS_BASE_URL（shell/.env）优先，否则指向本次选中的端口。
    set_runtime_default VOICE_TTS_BASE_URL "http://127.0.0.1:$VOICE_PORT"
    echo "[start.sh] voice sidecar on :$VOICE_PORT (MeloTTS-Chinese, CPU)"
    (
        cd "$ROOT/services/voice"
        exec env HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
        HF_HOME="$ROOT/services/voice/models/hf" \
        OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
        ./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "$VOICE_PORT"
    ) &
    VOICE_PID=$!
    record_service voice "$VOICE_PID"
    write_runtime_file /tmp/edu_voice_pid "$VOICE_PID"
    write_runtime_file /tmp/edu_voice_port "$VOICE_PORT"
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
    # Source newer than the last build -> stale bundle. Shared workspace
    # packages and the root lockfile are build inputs too: a change anywhere
    # in packages/* must invalidate the apps/web bundle.
    [ -n "$(find src public next.config.ts package.json ../../packages ../../pnpm-lock.yaml ../../tsconfig.base.json -newer .next/BUILD_ID -print -quit 2>/dev/null)" ] && return 0
    return 1
}

build_classroom_assets() {
    # 课堂渲染资产包（§9.4）：tsc frame-runtime + 固定 KaTeX 打成
    # services/api 可读包。必须在 next build 前显式执行（npm prebuild hook
    # 不会被 next build 触发）；缺包时课堂 capability 明确 renderer_unavailable。
    # 路径用 $ROOT 锚定：调用时 cwd 已在 apps/web/，相对路径会双双落空。
    local classroom_manifest="$ROOT/services/api/app/classroom/static/generated/manifest.json"
    if [ ! -f "$classroom_manifest" ] \
        || [ "$ROOT/apps/web/src/lib/classroom/frame-runtime.ts" -nt "$classroom_manifest" ] \
        || [ "$ROOT/apps/web/scripts/build-classroom-assets.mjs" -nt "$classroom_manifest" ] \
        || [ "$ROOT/pnpm-lock.yaml" -nt "$classroom_manifest" ]; then
        echo "[start.sh] building classroom renderer assets"
        (cd "$ROOT/apps/web" && pnpm run build:classroom) || {
            echo "[start.sh] classroom assets build failed; classroom renderer will be unavailable"
        }
    fi
}

build_frontend() {
    local bport="$1"
    echo "[start.sh] building frontend (next build --webpack, backend :$bport baked in; first build ~1-2 min)"
    build_classroom_assets
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
        echo "[start.sh] reusing production build in apps/web/.next (REBUILD=1 to force)"
    fi
    echo "[start.sh] frontend on :$port (prod next start, backend :$bport)"
    BACKEND_URL="http://localhost:$bport" \
    node ./node_modules/next/dist/bin/next start -p "$port" -H "$FRONTEND_HOST" &
    FRONT_PID=$!
}

start_frontend() {
    local bport; bport="$(cat /tmp/edu_backend_port 2>/dev/null || echo 8000)"
    local port
    port="${FRONT_PORT:-$(pick_port 3001 3000 3030)}"
    cd "$ROOT/apps/web"
    build_classroom_assets
    if [ "$FRONTEND_MODE" = "prod" ]; then
        start_frontend_prod "$port" "$bport"
    else
        # next build and next dev share apps/web/.next; a production build leaves
        # BUILD_ID behind and poisons the dev server (stale/mixed chunks -> weird
        # runtime errors). Wipe it automatically before starting dev.
        if [ -f "$ROOT/apps/web/.next/BUILD_ID" ]; then
            echo "[start.sh] found production build in apps/web/.next (BUILD_ID); cleaning for dev"
            rm -rf "$ROOT/apps/web/.next"
        fi
        echo "[start.sh] frontend on :$port (dev, backend :$bport)"
        BACKEND_URL="http://localhost:$bport" \
        NEXT_PUBLIC_BACKEND_URL="http://localhost:$bport" \
        node ./node_modules/next/dist/bin/next dev -p "$port" -H "$FRONTEND_HOST" &
        FRONT_PID=$!
    fi
    FRONT_PORT="$port"
    record_service frontend "$FRONT_PID"
    write_runtime_file /tmp/edu_frontend_port "$port"
    write_runtime_file /tmp/edu_frontend_pid "$FRONT_PID"
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
    local name pid_file pid cwd reference
    for name in backend frontend voice; do
        pid_file="$RUNTIME_DIR/$name.pid"
        reference="$(cat "$pid_file" 2>/dev/null || true)"
        if [ -z "$reference" ]; then
            # Compatibility with old launchers: consult only their PID file,
            # never scan every Next/uvicorn process in the repository.
            reference="$(cat "/tmp/edu_${name}_pid" 2>/dev/null || true)"
            pid="${reference%%:*}"
            if [[ "$pid" =~ ^[0-9]+$ ]] && [ -d "/proc/$pid" ]; then
                local command_line
                command_line="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
                case "$name:$command_line" in
                    backend:*"-m uvicorn app.main:app"*|voice:*"-m uvicorn app:app"*|frontend:next-server*|frontend:*"next/dist/bin/next "*) ;;
                    *) continue ;;
                esac
            fi
        fi
        pid="${reference%%:*}"
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        if [ -n "$pid" ] && [ -d "/proc/$pid" ]; then
            cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
            if [[ "$cwd" == "$ROOT" || "$cwd" == "$ROOT/"* ]]; then
                "$PYTHON_BIN" "$ROOT/deploy/self-hosted/process_cleanup.py" "$ROOT" "$reference"
            fi
        fi
        rm -f "$pid_file"
    done
    sleep 1
    write_runtime_file /tmp/edu_backend_pid ""
    write_runtime_file /tmp/edu_frontend_pid ""
    write_runtime_file /tmp/edu_voice_pid ""
}

case "${1:-all}" in
    backend) start_voice_sidecar; start_backend ;;
    frontend) start_frontend; open_browser & ;;
    # Explicit dev-mode entry point (same as FRONTEND_MODE=dev, overrides .env):
    # hot-reload dev server, auto-wipes any production build in .next.
    dev) FRONTEND_MODE=dev; stop_all; prepare_ports; start_voice_sidecar; start_backend; sleep 2; start_frontend; open_browser & ;;
    all) stop_all; prepare_ports; start_voice_sidecar; start_backend; sleep 2; start_frontend; open_browser & ;;
    stop) stop_all ;;
    *) echo "Usage: $0 [all|backend|frontend|dev|stop|daemon [all|dev|backend|frontend]]"; echo "  默认（all）生产模式：一次构建 + next start，源码/端口变化自动重建"; echo "  非交互环境自动后台运行；ATTACHED=1 保持前台；daemon 显式后台运行"; echo "  dev 子命令显式覆盖为热重载开发模式（等价 FRONTEND_MODE=dev）"; echo "  REBUILD=1 $0 ...           # 强制重建前端生产包"; exit 1 ;;
esac

# Wait only for owned servers, not browser/readiness helper jobs. Log the actual
# child exit; one failed service never implicitly stops the other live services.
service_pids=()
for pid in "$BACK_PID" "$FRONT_PID" "$VOICE_PID"; do
    [ -z "$pid" ] || service_pids+=("$pid")
done
while [ "${#service_pids[@]}" -gt 0 ]; do
    finished_pid=""; service_status=0
    wait -n -p finished_pid "${service_pids[@]}" || service_status=$?
    if [ -z "$finished_pid" ]; then break; fi
    echo "[start.sh] service exited: pid=$finished_pid status=$service_status"
    remaining_pids=()
    for pid in "${service_pids[@]}"; do
        [ "$pid" = "$finished_pid" ] || remaining_pids+=("$pid")
    done
    service_pids=("${remaining_pids[@]}")
done

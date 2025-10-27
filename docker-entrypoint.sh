#!/bin/bash
set -e

# Warp2Api Docker 启动脚本
# 支持启动单个服务或双服务

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: $1${NC}"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $1${NC}"
}

# 修复日志目录权限
# 确保 warp 用户可以写入挂载的卷
if [ -d "/app/logs" ]; then
   log "正在修复 /app/logs 目录权限..."
   chown -R warp:warp /app/logs
fi

# 等待服务就绪
wait_for_service() {
    local url=$1
    local service_name=$2
    local max_attempts=30
    local attempt=1

    log "等待 $service_name 服务启动..."

    while [ $attempt -le $max_attempts ]; do
        if curl -s -f "$url" > /dev/null 2>&1; then
            log "$service_name 服务已就绪"
            return 0
        fi

        echo -n "."
        sleep 2
        attempt=$((attempt + 1))
    done

    error "$service_name 服务启动超时"
    return 1
}

# 启动 Protobuf 桥接服务器
start_bridge_server() {
    log "启动 Protobuf 桥接服务器 (端口 8001)..."
    cd /app
    exec uv run python server.py
}

# 启动 API 服务器
start_api_server() {
    log "启动多格式 API 服务器 (端口 8010)..."

    # 等待桥接服务器就绪
    if ! wait_for_service "http://localhost:8001/healthz" "Protobuf桥接"; then
        error "桥接服务器未就绪，无法启动API服务器"
        exit 1
    fi

    cd /app
    exec uv run python openai_compat.py
}

# 启动双服务器
start_both_servers() {
    log "启动 Warp2Api 双服务器模式"

    # 后台启动桥接服务器
    log "后台启动 Protobuf 桥接服务器..."
    uv run python server.py &
    BRIDGE_PID=$!

    # 等待桥接服务器就绪
    if ! wait_for_service "http://localhost:8001/healthz" "Protobuf桥接"; then
        error "桥接服务器启动失败"
        kill $BRIDGE_PID 2>/dev/null || true
        exit 1
    fi

    # 启动API服务器 (前台)
    log "启动多格式 API 服务器..."
    uv run python openai_compat.py &
    API_PID=$!

    # 等待API服务器就绪
    if ! wait_for_service "http://localhost:8010/healthz" "API"; then
        error "API服务器启动失败"
        kill $BRIDGE_PID $API_PID 2>/dev/null || true
        exit 1
    fi

    log "🎉 Warp2Api 服务已启动完成!"
    log "📡 Protobuf 桥接服务器: http://localhost:8001"
    log "🌐 多格式 API 服务器: http://localhost:8010"
    log "📚 支持的端点:"
    log "   - OpenAI Chat Completions: POST /v1/chat/completions"
    log "   - Anthropic Messages: POST /v1/messages"
    log "   - 模型列表: GET /v1/models"

    # 等待任一进程退出
    wait $API_PID $BRIDGE_PID
}

# 显示帮助信息
show_help() {
    echo "Warp2Api Docker 启动脚本"
    echo ""
    echo "用法: $0 [COMMAND]"
    echo ""
    echo "命令:"
    echo "  both        启动双服务器 (默认)"
    echo "  bridge      仅启动 Protobuf 桥接服务器"
    echo "  api         仅启动多格式 API 服务器"
    echo "  help        显示此帮助信息"
    echo ""
    echo "环境变量:"
    echo "  WARP_JWT              Warp JWT Token (可选)"
    echo "  WARP_REFRESH_TOKEN    Warp 刷新 Token (可选)"
    echo "  HOST                  服务器主机 (默认: 0.0.0.0)"
    echo "  PORT                  API 服务器端口 (默认: 8010)"
    echo "  BRIDGE_BASE_URL       桥接服务器 URL (默认: http://localhost:8001)"
    echo "  CLOUDFLARE_API_TOKEN  Cloudflare API Token (可选)"
    echo "  CLOUDFLARE_ACCOUNT_ID Cloudflare 账户 ID (可选)"
}

# 信号处理
cleanup() {
    log "收到停止信号，正在关闭服务..."
    kill $BRIDGE_PID $API_PID 2>/dev/null || true
    exit 0
}

trap cleanup SIGTERM SIGINT

# 主逻辑
case "${1:-both}" in
    "bridge")
        start_bridge_server
        ;;
    "api")
        start_api_server
        ;;
    "both")
        start_both_servers
        ;;
    "help"|"-h"|"--help")
        show_help
        ;;
    *)
        error "未知命令: $1"
        show_help
        exit 1
        ;;
esac

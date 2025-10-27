#!/bin/bash
# Warp2Api Docker 快速启动脚本

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[Warp2Api] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[Warp2Api] WARNING: $1${NC}"
}

error() {
    echo -e "${RED}[Warp2Api] ERROR: $1${NC}"
}

info() {
    echo -e "${BLUE}[Warp2Api] $1${NC}"
}

# 检查 Docker 和 Docker Compose
check_requirements() {
    if ! command -v docker &> /dev/null; then
        error "Docker 未安装，请先安装 Docker"
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null; then
        error "Docker Compose 未安装，请先安装 Docker Compose"
        exit 1
    fi

    log "✅ Docker 环境检查通过"
}

# 显示帮助信息
show_help() {
    echo "Warp2Api Docker 快速启动脚本"
    echo ""
    echo "用法: $0 [COMMAND] [OPTIONS]"
    echo ""
    echo "命令:"
    echo "  start       启动服务 (默认)"
    echo "  stop        停止服务"
    echo "  restart     重启服务"
    echo "  logs        查看日志"
    echo "  status      查看状态"
    echo "  build       重新构建镜像"
    echo "  clean       清理资源"
    echo "  dev         启动开发模式"
    echo "  separate    启动分离服务模式"
    echo "  help        显示帮助信息"
    echo ""
    echo "选项:"
    echo "  -d, --detach    后台运行"
    echo "  -f, --follow    跟踪日志输出"
    echo "  --no-cache      构建时不使用缓存"
    echo ""
    echo "示例:"
    echo "  $0 start -d              # 后台启动服务"
    echo "  $0 logs -f               # 跟踪日志"
    echo "  $0 build --no-cache      # 无缓存重新构建"
    echo "  $0 dev                   # 启动开发模式"
}

# 启动服务
start_service() {
    local detach=""
    if [[ "$1" == "-d" || "$1" == "--detach" ]]; then
        detach="-d"
    fi

    log "启动 Warp2Api 服务..."
    docker-compose up $detach

    if [[ -n "$detach" ]]; then
        log "服务已在后台启动"
        info "📡 Protobuf 桥接服务器: http://localhost:8000"
        info "🌐 多格式 API 服务器: http://localhost:8010"
        info "📚 API 文档: http://localhost:8010/docs"
        info ""
        info "使用以下命令查看状态和日志:"
        info "  $0 status    # 查看服务状态"
        info "  $0 logs -f   # 查看实时日志"
    fi
}

# 停止服务
stop_service() {
    log "停止 Warp2Api 服务..."
    docker-compose down
    log "✅ 服务已停止"
}

# 重启服务
restart_service() {
    log "重启 Warp2Api 服务..."
    docker-compose restart
    log "✅ 服务已重启"
}

# 查看日志
show_logs() {
    local follow=""
    if [[ "$1" == "-f" || "$1" == "--follow" ]]; then
        follow="-f"
    fi

    log "查看服务日志..."
    docker-compose logs $follow
}

# 查看状态
show_status() {
    log "Warp2Api 服务状态:"
    docker-compose ps

    echo ""
    info "健康检查:"

    if curl -s -f http://localhost:8000/healthz > /dev/null 2>&1; then
        log "✅ Protobuf 桥接服务器 (8000) - 正常"
    else
        warn "❌ Protobuf 桥接服务器 (8000) - 异常"
    fi

    if curl -s -f http://localhost:8010/healthz > /dev/null 2>&1; then
        log "✅ 多格式 API 服务器 (8010) - 正常"
    else
        warn "❌ 多格式 API 服务器 (8010) - 异常"
    fi
}

# 构建镜像
build_image() {
    local no_cache=""
    if [[ "$1" == "--no-cache" ]]; then
        no_cache="--no-cache"
    fi

    log "构建 Warp2Api 镜像..."
    docker-compose build $no_cache
    log "✅ 镜像构建完成"
}

# 清理资源
clean_resources() {
    log "清理 Docker 资源..."

    # 停止服务
    docker-compose down

    # 清理镜像
    docker image prune -f

    # 清理容器
    docker container prune -f

    # 清理网络
    docker network prune -f

    log "✅ 资源清理完成"
}

# 开发模式
dev_mode() {
    log "启动开发模式..."
    docker-compose --profile dev up -d
    log "✅ 开发模式已启动"
    info "🔧 开发服务器: http://localhost:8000, http://localhost:8010"
    info "🐛 调试端口: http://localhost:8080"
}

# 分离服务模式
separate_mode() {
    log "启动分离服务模式..."
    docker-compose --profile separate up -d
    log "✅ 分离服务模式已启动"
    info "🔗 桥接服务器: http://localhost:8000"
    info "🌐 API 服务器: http://localhost:8010"
}

# 主逻辑
main() {
    check_requirements

    case "${1:-start}" in
        "start")
            start_service "$2"
            ;;
        "stop")
            stop_service
            ;;
        "restart")
            restart_service
            ;;
        "logs")
            show_logs "$2"
            ;;
        "status")
            show_status
            ;;
        "build")
            build_image "$2"
            ;;
        "clean")
            clean_resources
            ;;
        "dev")
            dev_mode
            ;;
        "separate")
            separate_mode
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
}

# 运行主函数
main "$@"
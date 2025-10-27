# Warp2Api Dockerfile
# 支持双API格式的Warp AI桥接服务

# 基础镜像
FROM python:3.13-slim as base

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv (更快的Python包管理器)
RUN pip install uv

# 依赖安装阶段
FROM base as dependencies

WORKDIR /app

# 复制依赖文件
COPY pyproject.toml uv.lock .

# 安装Python依赖
RUN uv sync
#--frozen --no-dev

# 生产环境阶段
FROM base as production

WORKDIR /app

# 从依赖阶段复制虚拟环境
COPY --from=dependencies /app/.venv /app/.venv

# 复制应用代码
COPY server.py openai_compat.py ./
COPY protobuf2openai/ ./protobuf2openai/
COPY warp2protobuf/ ./warp2protobuf/
COPY warp_*.py ./
COPY cloudflare-worker.js ./
COPY proto/ ./proto/

# 复制启动脚本
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# 创建非root用户
RUN useradd --create-home --shell /bin/bash --uid 1000 warp && \
    mkdir -p logs && \
    chown -R warp:warp /app

USER warp

# 暴露端口
EXPOSE 8000 8010

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/healthz && curl -f http://localhost:8010/healthz || exit 1

# 默认启动命令
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["both"]

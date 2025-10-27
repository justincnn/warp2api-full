# Docker 部署指南

本文档详细说明如何使用 Docker 部署 Warp2Api 服务。

## 🐳 容器化概览

Warp2Api 提供了完整的 Docker 容器化解决方案，支持：
- **单容器双服务模式** (推荐)
- **分离服务模式** (高可用)
- **开发模式** (调试和开发)

## 📋 系统要求

### 基础要求
- Docker Engine 20.10+
- Docker Compose 2.0+
- 至少 1GB 可用内存
- 至少 2GB 可用磁盘空间

### 推荐配置
- 2 CPU 核心
- 2GB 内存
- 5GB 可用磁盘空间

## 🚀 快速开始

### 1. 克隆项目
```bash
git clone <repository-url>
cd Warp2Api
```

### 2. 使用 Docker Compose (推荐)
```bash
# 启动服务 (单容器双服务模式)
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

### 3. 直接使用 Docker
```bash
# 构建镜像
docker build -t warp2api .

# 运行容器
docker run -d \
  --name warp2api \
  -p 8000:8000 \
  -p 8010:8010 \
  warp2api
```

## 🔧 部署模式

### 模式 1: 单容器双服务 (默认)

**特点**:
- 一个容器运行两个服务
- 资源占用最小
- 部署简单
- 适合大多数场景

**启动命令**:
```bash
docker-compose up -d warp2api
```

**访问地址**:
- Protobuf 桥接服务器: `http://localhost:8000`
- 多格式 API 服务器: `http://localhost:8010`

### 模式 2: 分离服务模式

**特点**:
- 两个独立容器
- 更好的可扩展性
- 独立的健康检查
- 适合生产环境

**启动命令**:
```bash
docker-compose --profile separate up -d
```

**服务说明**:
- `warp2api-bridge`: Protobuf 桥接服务器
- `warp2api-api`: 多格式 API 服务器

### 模式 3: 开发模式

**特点**:
- 挂载源码目录
- 包含开发工具
- 支持热重载
- 适合开发调试

**启动命令**:
```bash
docker-compose --profile dev up -d
```

## ⚙️ 环境变量配置

### 基础配置
```bash
# 服务器配置
HOST=0.0.0.0                    # 服务器主机地址
PORT=8010                       # API 服务器端口
BRIDGE_BASE_URL=http://localhost:8000  # 桥接服务器 URL

# 调试配置
DEBUG=0                         # 调试模式 (0/1)
PYTHONPATH=/app                 # Python 路径
```

### Warp 认证配置 (可选)
```bash
# 如果不设置，程序会自动获取匿名 token
WARP_JWT=your_jwt_token
WARP_REFRESH_TOKEN=your_refresh_token
```

### Cloudflare Token 池配置 (可选)
```bash
# 需要 Cloudflare 账户才能使用 Token 池功能
CLOUDFLARE_API_TOKEN=your_api_token
CLOUDFLARE_ACCOUNT_ID=your_account_id
```

### 配置方法

#### 方法 1: 环境变量文件
```bash
# 创建 .env 文件
cp .env.example .env

# 编辑配置
vim .env

# 启动服务
docker-compose up -d
```

#### 方法 2: 直接在 docker-compose.yml 中配置
```yaml
services:
  warp2api:
    environment:
      - WARP_JWT=your_jwt_token
      - CLOUDFLARE_API_TOKEN=your_api_token
```

#### 方法 3: 运行时传递
```bash
docker run -d \
  -e WARP_JWT=your_jwt_token \
  -e CLOUDFLARE_API_TOKEN=your_api_token \
  -p 8000:8000 -p 8010:8010 \
  warp2api
```

## 📊 监控和日志

### 健康检查
```bash
# 检查容器状态
docker-compose ps

# 检查健康状态
docker inspect warp2api | grep Health -A 10

# 手动健康检查
curl http://localhost:8000/healthz
curl http://localhost:8010/healthz
```

### 日志管理
```bash
# 查看实时日志
docker-compose logs -f

# 查看特定服务日志
docker-compose logs -f warp2api

# 查看最近 100 行日志
docker-compose logs --tail=100 warp2api

# 日志持久化
# 日志会自动保存到 ./logs 目录
ls -la logs/
```

### 性能监控
```bash
# 查看资源使用
docker stats warp2api

# 查看容器详细信息
docker inspect warp2api
```

## 🔒 安全配置

### 网络安全
```yaml
# 限制网络访问
services:
  warp2api:
    ports:
      - "127.0.0.1:8000:8000"  # 仅本地访问
      - "127.0.0.1:8010:8010"
```

### 用户权限
- 容器内使用非 root 用户 (uid: 1000)
- 最小权限原则
- 只读文件系统 (可选)

### 资源限制
```yaml
services:
  warp2api:
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
```

## 🚀 生产环境部署

### 1. 反向代理配置 (Nginx)
```nginx
upstream warp2api {
    server localhost:8010;
}

server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://warp2api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 2. HTTPS 配置
```bash
# 使用 Let's Encrypt
certbot --nginx -d your-domain.com
```

### 3. 自动重启
```yaml
services:
  warp2api:
    restart: unless-stopped
    # 或者使用 always
```

### 4. 备份和恢复
```bash
# 备份配置
tar -czf warp2api-backup.tar.gz docker-compose.yml .env logs/

# 恢复
tar -xzf warp2api-backup.tar.gz
docker-compose up -d
```

## 🛠️ 故障排除

### 常见问题

#### 1. 容器启动失败
```bash
# 查看详细错误
docker-compose logs warp2api

# 检查端口占用
netstat -tulpn | grep :8000
netstat -tulpn | grep :8010

# 重新构建镜像
docker-compose build --no-cache
```

#### 2. 服务无法访问
```bash
# 检查防火墙
sudo ufw status
sudo ufw allow 8000
sudo ufw allow 8010

# 检查容器网络
docker network ls
docker network inspect warp2api-network
```

#### 3. 认证问题
```bash
# 检查环境变量
docker exec warp2api env | grep WARP

# 查看认证日志
docker-compose logs warp2api | grep -i auth
```

#### 4. 内存不足
```bash
# 检查内存使用
docker stats warp2api

# 增加内存限制
# 在 docker-compose.yml 中调整 memory 限制
```

### 调试模式
```bash
# 进入容器调试
docker exec -it warp2api bash

# 查看进程
docker exec warp2api ps aux

# 查看网络连接
docker exec warp2api netstat -tulpn
```

## 📈 性能优化

### 1. 镜像优化
- 使用多阶段构建减小镜像大小
- 利用 Docker 层缓存
- 最小化依赖

### 2. 运行时优化
```yaml
services:
  warp2api:
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONDONTWRITEBYTECODE=1
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
```

### 3. 资源调优
```yaml
services:
  warp2api:
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
    sysctls:
      - net.core.somaxconn=1024
```

## 🔄 更新和维护

### 更新流程
```bash
# 1. 拉取最新代码
git pull origin master

# 2. 重新构建镜像
docker-compose build

# 3. 重启服务
docker-compose down
docker-compose up -d

# 4. 验证服务
curl http://localhost:8000/healthz
curl http://localhost:8010/healthz
```

### 定期维护
```bash
# 清理未使用的镜像
docker image prune -f

# 清理未使用的容器
docker container prune -f

# 清理未使用的网络
docker network prune -f

# 查看磁盘使用
docker system df
```

## 📚 相关文档

- [项目 README](../README.md)
- [Token 池实现](TOKEN_POOL_IMPLEMENTATION.md)
- [测试指南](TESTING_GUIDE.md)
- [Cloudflare 设置](../CLOUDFLARE_SETUP.md)

---

*此文档提供了 Warp2Api 的完整 Docker 部署指南，涵盖了从开发到生产的各种场景。*
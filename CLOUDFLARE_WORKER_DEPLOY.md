# Cloudflare Worker 部署指南

使用 Cloudflare Worker 绕过 Warp API 的 IP 限制，获取匿名访问令牌。

## 🚀 快速部署

### 方法 1: Cloudflare Dashboard（推荐）

1. **登录 Cloudflare Dashboard**
   - 访问 [dash.cloudflare.com](https://dash.cloudflare.com)
   - 登录你的账号

2. **创建 Worker**
   - 点击左侧菜单 "Workers & Pages"
   - 点击 "Create application"
   - 选择 "Create Worker"
   - 输入 Worker 名称，如 `warp-token-service`

3. **部署代码**
   - 将 `cloudflare-worker.js` 的内容复制到编辑器中
   - 点击 "Save and Deploy"

4. **获取 Worker URL**
   - 部署成功后会显示 Worker URL
   - 格式类似：`https://warp-token-service.your-subdomain.workers.dev`

### 方法 2: Wrangler CLI

```bash
# 安装 Wrangler CLI
npm install -g wrangler

# 登录 Cloudflare
wrangler login

# 创建 wrangler.toml 配置文件
cat > wrangler.toml << EOF
name = "warp-token-service"
main = "cloudflare-worker.js"
compatibility_date = "2024-01-01"
EOF

# 部署 Worker
wrangler deploy
```

## 📡 API 端点

部署成功后，你的 Worker 将提供以下端点：

### 1. 主页面
```
GET https://your-worker.workers.dev/
```
显示使用说明和 API 文档

### 2. 获取完整访问令牌（推荐）
```bash
curl https://your-worker.workers.dev/token
```

**响应示例：**
```json
{
  "success": true,
  "accessToken": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refreshToken": "1//0GWqE9q-9Q9CgYIARAAGA0SNwF-L9Ir...",
  "idToken": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjFlOWdkazcifQ...",
  "userData": {
    "anonymousUserType": "NATIVE_CLIENT_ANONYMOUS_USER_FEATURE_GATED",
    "expiresAt": "2024-12-31T23:59:59Z",
    "firebaseUid": "anonymous_user_123"
  },
  "timestamp": "2024-01-15T10:30:00.000Z"
}
```

### 3. 仅创建匿名用户
```bash
curl https://your-worker.workers.dev/create
```

### 4. 健康检查
```bash
curl https://your-worker.workers.dev/health
```

## 🔧 集成到你的项目

### Python 集成示例

```python
import httpx
import asyncio

async def get_warp_token_from_worker(worker_url: str) -> str:
    """从 Cloudflare Worker 获取 Warp 访问令牌"""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{worker_url}/token")

        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                return data["accessToken"]
            else:
                raise Exception(f"Worker 返回错误: {data.get('error')}")
        else:
            raise Exception(f"Worker 请求失败: {response.status_code}")

# 使用示例
async def main():
    worker_url = "https://your-worker.workers.dev"
    try:
        token = await get_warp_token_from_worker(worker_url)
        print(f"获得访问令牌: {token[:50]}...")

        # 将令牌保存到环境变量或 .env 文件
        import os
        os.environ["WARP_JWT"] = token

    except Exception as e:
        print(f"获取令牌失败: {e}")

# 运行
asyncio.run(main())
```

### 修改现有的 auth.py

你可以修改 `warp2protobuf/core/auth.py` 中的 `acquire_anonymous_access_token` 函数：

```python
async def acquire_anonymous_access_token() -> str:
    """优先使用 Cloudflare Worker 获取匿名访问令牌"""

    # 尝试使用 Cloudflare Worker
    worker_url = os.getenv("WARP_WORKER_URL")
    if worker_url:
        try:
            logger.info("尝试通过 Cloudflare Worker 获取令牌...")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{worker_url}/token")

                if response.status_code == 200:
                    data = response.json()
                    if data.get("success"):
                        access_token = data["accessToken"]
                        update_env_file(access_token)

                        # 同时保存 refresh token
                        if "refreshToken" in data:
                            update_env_refresh_token(data["refreshToken"])

                        logger.info("通过 Worker 成功获取令牌")
                        return access_token

        except Exception as e:
            logger.warning(f"Worker 获取令牌失败，回退到直接请求: {e}")

    # 回退到原始方法
    logger.info("使用原始方法获取匿名访问令牌...")
    # ... 原始的实现代码
```

## 🌍 优势

1. **绕过 IP 限制** - 使用 Cloudflare 的全球 IP 池
2. **高可用性** - Cloudflare 的全球边缘网络
3. **零成本** - 免费套餐每天 100,000 次请求
4. **低延迟** - 边缘计算，就近响应
5. **随机化** - 每次请求使用不同的浏览器特征

## ⚙️ 环境变量配置

在你的项目中添加环境变量：

```bash
# .env 文件
WARP_WORKER_URL=https://your-worker.workers.dev
```

## 🔍 监控和调试

### 查看 Worker 日志
1. 在 Cloudflare Dashboard 中进入你的 Worker
2. 点击 "Logs" 标签页
3. 点击 "Begin log stream" 查看实时日志

### 测试 Worker
```bash
# 测试健康状态
curl https://your-worker.workers.dev/health

# 测试令牌获取
curl https://your-worker.workers.dev/token

# 查看响应头
curl -I https://your-worker.workers.dev/token
```

## 🚨 注意事项

1. **请求频率** - 虽然使用了不同 IP，但仍建议控制请求频率
2. **令牌缓存** - 建议在本地缓存令牌，避免频繁请求
3. **错误处理** - Worker 可能偶尔失败，建议实现重试机制
4. **安全性** - Worker URL 是公开的，但不包含敏感信息

## 📈 扩展功能

你可以进一步扩展 Worker 功能：

1. **添加认证** - 使用 API Key 保护端点
2. **令牌缓存** - 在 Worker 中缓存令牌，减少上游请求
3. **请求统计** - 记录请求次数和成功率
4. **多地域部署** - 部署到不同地域的 Worker

## 🎉 完成

现在你就可以通过 Cloudflare Worker 稳定地获取 Warp 匿名访问令牌了！这种方法有效绕过了 IP 限制，提供了更稳定的服务。
# Warp2Api

基于 Python 的桥接服务，为 Warp AI 服务提供 **OpenAI Chat Completions API** 和 **Anthropic Messages API** 双重兼容性，通过利用 Warp 的 protobuf 基础架构，实现与两种主流 AI API 格式的无缝集成。

## 🚀 特性

- **双 API 兼容性**: 同时支持 OpenAI Chat Completions API 和 Anthropic Messages API 格式
- **Warp 集成**: 使用 protobuf 通信与 Warp AI 服务无缝桥接
- **双服务器架构**:
  - 用于 Warp 通信的 Protobuf 编解码服务器
  - 用于客户端应用程序的多格式 API 服务器
- **自动格式转换**: OpenAI ↔ Anthropic 格式自动双向转换
- **智能 Token 管理**:
  - **Token 池**: 维护 2-3 个预备 token，实现无缝切换
  - **自动刷新**: 后台异步刷新过期 token
  - **429 错误处理**: 遇到速率限制自动切换备用 token，不中断对话
  - **Cloudflare Worker 集成**: 利用 CF 分布式 IP 池突破 IP 限制
- **JWT 认证**: Warp 服务的自动令牌管理和刷新
- **双格式流式支持**: 兼容 OpenAI 和 Anthropic 的 SSE 流式响应格式
- **WebSocket 监控**: 内置监控和调试功能
- **消息重排序**: 针对 Anthropic 风格对话的智能消息处理
- **工具调用增强**: 支持 OpenAI Function Calling 和 Anthropic Tool Use
  - **工具调用结果处理**: 增强的工具调用结果解码和传递
  - **Base64URL 解码**: 自动解码工具调用payload和result数据
  - **流式工具结果**: 支持工具执行结果的流式传输

## 📋 系统要求

- Python 3.13+
- Warp AI 服务访问权限（需要 JWT 令牌）

## 🛠️ 安装

### 方式一: Docker 部署 (推荐)

1. **克隆仓库:**
   ```bash
   git clone <repository-url>
   cd Warp2Api
   ```

2. **使用 Docker Compose 启动:**
   ```bash
   # 快速启动 (后台运行)
   ./docker-start.sh start -d

   # 或直接使用 docker-compose
   docker-compose up -d
   ```

3. **验证服务:**
   ```bash
   # 检查服务状态
   ./docker-start.sh status

   # 查看日志
   ./docker-start.sh logs -f
   ```

### 方式二: 本地开发安装

1. **克隆仓库:**
   ```bash
   git clone <repository-url>
   cd Warp2Api
   ```

2. **使用 uv 安装依赖 (推荐):**
   ```bash
   uv sync
   ```

   或使用 pip:
   ```bash
   pip install -e .
   ```

3. **配置匿名JWT TOKEN:**
   这一步可以什么都不做，程序会自行请求匿名JWT TOKEN

   当然你也可以创建一个包含您的 Warp 凭证的 `.env` 文件，用自己的订阅额度，不过这并不推荐:
   ```env
   WARP_JWT=your_jwt_token_here
   WARP_REFRESH_TOKEN=your_refresh_token_here
   ```

## 🎯 使用方法

### 快速开始

1. **启动 Protobuf 桥接服务器:**
   ```bash
   python server.py
   ```
   默认地址: `http://localhost:8000`

2. **启动多格式 API 服务器:**
   ```bash
   python openai_compat.py
   ```
   默认地址: `http://localhost:8010`
   支持端点: `/v1/chat/completions` 和 `/v1/messages`

### 使用 API

两个服务器都运行后，您可以使用任何 OpenAI 或 Anthropic 兼容的客户端:

#### OpenAI 格式示例

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8010/v1",
    api_key="dummy"  # 不是必需的，但某些客户端需要
)

response = client.chat.completions.create(
    model="claude-3-sonnet",  # 模型名称会被传递
    messages=[
        {"role": "user", "content": "你好，你好吗？"}
    ],
    stream=True
)

for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

#### Anthropic 格式示例

```python
import httpx
import json

response = httpx.post(
    "http://localhost:8010/v1/messages",
    json={
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "你好，你好吗？"}
        ],
        "stream": True
    },
    headers={"Content-Type": "application/json"}
)

for line in response.iter_lines():
    if line.startswith("data: "):
        data = json.loads(line[6:])  # 去掉 "data: " 前缀
        if data["type"] == "content_block_delta":
            print(data["delta"]["text"], end="")
```

### 可用端点

#### Protobuf 桥接服务器 (`http://localhost:8000`)
- `GET /healthz` - 健康检查
- `POST /encode` - 将 JSON 编码为 protobuf
- `POST /decode` - 将 protobuf 解码为 JSON
- `WebSocket /ws` - 实时监控

#### 多格式 API 服务器 (`http://localhost:8010`)
- `GET /` - 服务状态
- `GET /healthz` - 健康检查
- `GET /v1/models` - 模型列表
- `POST /v1/chat/completions` - OpenAI Chat Completions 兼容端点
- `POST /v1/messages` - Anthropic Messages 兼容端点

## 🏗️ 架构

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│    客户端应用     │───▶│  多格式 API    │───▶│   Protobuf      │
│OpenAI/Anthropic│    │     服务器      │    │    桥接服务器    │
└─────────────────┘    │  (端口 8010)    │    │  (端口 8000)    │
                       │ OpenAI↔Anthropic│    └─────────────────┘
                       └─────────────────┘           │
                                                       ▼
                                              ┌─────────────────┐
                                              │    Warp AI      │
                                              │      服务       │
                                              └─────────────────┘
```

### 核心组件

- **`protobuf2openai/`**: 多格式 API 兼容层
  - OpenAI 和 Anthropic 消息格式转换
  - 双格式流式响应处理
  - 自动格式识别和转换
  - 错误映射和验证

- **`warp2protobuf/`**: Warp protobuf 通信层
  - JWT 认证管理
  - Protobuf 编解码
  - WebSocket 监控
  - 请求路由和验证

## 🔧 配置

### 环境变量

| 变量 | 描述 | 默认值 |
|------|------|--------|
| `WARP_JWT` | Warp 认证 JWT 令牌 | 自动获取匿名 token |
| `WARP_REFRESH_TOKEN` | JWT 刷新令牌 | 自动获取 |
| `HOST` | 服务器主机地址 | `127.0.0.1` |
| `PORT` | 多格式 API 服务器端口 | `8010` |
| `BRIDGE_BASE_URL` | Protobuf 桥接服务器 URL | `http://localhost:8000` |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API 令牌（可选） | 无 |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 账户 ID（可选） | 无 |

### Token 池配置

当配置了 Cloudflare 凭证时，系统会自动启用 Token 池功能：
- 维护 2-3 个有效 token
- 自动在后台刷新 token
- 遇到 429 错误自动切换
- 利用 Cloudflare Worker 分布式 IP 池

### 项目脚本

在 `pyproject.toml` 中定义:

```bash
# 启动 protobuf 桥接服务器
warp-server

# 启动多格式 API 服务器 (新名称)
warp-api
```

**注意**: 脚本名称已从 `warp-test` 更新为 `warp-api`，更准确地反映其功能。

## 🔐 认证

服务会自动处理 Warp 认证:

1. **JWT 管理**: 自动令牌验证和刷新
2. **匿名访问**: 在需要时回退到匿名令牌
3. **令牌持久化**: 安全的令牌存储和重用

## 🧪 开发

### 开发工具

项目提供了各种开发和调试工具：

#### Warp 响应解析工具

使用 `parse_warp_response.py` 解析保存的 Warp SSE 响应数据：

```bash
# 解析默认文件 warp-res/1.txt
python parse_warp_response.py

# 或修改脚本中的 file_path 指向不同文件
```

**功能特点**:
- 解析 SSE 流式响应数据
- 提取对话 ID 和任务 ID  
- 重建完整响应内容
- 统计事件类型和数量
- **Base64URL 解码**: 自动解码 `serialized_result` 和 `payload` 字段
- 支持工具调用结果解析

### 项目结构

```
Warp2Api/
├── protobuf2openai/          # 多格式 API 兼容层
│   ├── app.py               # FastAPI 应用程序
│   ├── router.py            # API 路由 (/v1/chat/completions + /v1/messages)
│   ├── models.py            # Pydantic 模型 (双格式支持)
│   ├── anthropic_converter.py # OpenAI↔Anthropic 格式转换器
│   ├── anthropic_sse_transform.py # Anthropic SSE 流式响应处理
│   ├── bridge.py            # 桥接初始化
│   └── sse_transform.py     # OpenAI SSE 流式响应处理
├── warp2protobuf/           # Warp protobuf 层
│   ├── api/                 # API 路由
│   ├── core/                # 核心功能
│   │   ├── auth.py          # 认证和 Token 池集成
│   │   ├── protobuf_utils.py # Protobuf 工具
│   │   └── logging.py       # 日志设置
│   ├── config/              # 配置
│   └── warp/                # Warp 特定代码
├── server.py                # Protobuf 桥接服务器
├── openai_compat.py         # 多格式 API 服务器
├── warp_token_manager.py    # Cloudflare Worker 部署管理
├── warp_token_pool.py       # Token 池管理系统
├── warp_request_handler.py  # 请求拦截和自动重试
├── cloudflare-worker.js     # Cloudflare Worker 脚本
├── parse_warp_response.py   # Warp 响应解析工具 🆕
├── warp-res/                # Warp 响应数据目录 🆕
├── test_token_pool.py       # Token 池测试
├── test_anthropic_endpoint.py # Anthropic 端点测试
├── test_anthropic_streaming.py # Anthropic 流式测试
├── docs/                    # 文档目录
└── pyproject.toml           # 项目配置
```

### 依赖项

主要依赖项包括:
- **FastAPI**: 现代、快速的 Web 框架
- **Uvicorn**: ASGI 服务器实现
- **HTTPx**: 支持 HTTP/2 的异步 HTTP 客户端
- **Protobuf**: Protocol buffer 支持
- **WebSockets**: WebSocket 通信
- **OpenAI**: 用于类型兼容性

## 🐛 故障排除

### 常见问题

1. **JWT 令牌过期**
   - 服务会自动刷新令牌
   - 检查日志中的认证错误
   - 验证 `WARP_REFRESH_TOKEN` 是否有效

2. **桥接服务器未就绪**
   - 确保首先运行 protobuf 桥接服务器
   - 检查 `BRIDGE_BASE_URL` 配置
   - 验证端口可用性

3. **连接错误**
   - 检查到 Warp 服务的网络连接
   - 验证防火墙设置
   - 如适用，检查代理配置

### 日志记录

两个服务器都提供详细的日志记录:
- 认证状态和令牌刷新
- 请求/响应处理
- 错误详情和堆栈跟踪
- 性能指标

## 📄 许可证

该项目配置为内部使用。请与项目维护者联系了解许可条款。

## 🤝 贡献

1. Fork 仓库
2. 创建功能分支
3. 进行更改
4. 如适用，添加测试
5. 提交 pull request

## 📚 文档

### 详细文档
- **[Warp 技术文档](WARP.md)** - Warp2Api 项目技术详细说明和开发指南 🆕
- **[更新日志](CHANGELOG.md)** - 项目版本更新和功能变更记录
- **[故障排查指南](docs/TROUBLESHOOTING.md)** - 常见问题解决方案和调试技巧 🆕
- **[测试指南](docs/TESTING_GUIDE.md)** - 测试脚本使用说明和最佳实践
- **[Token 池实现](docs/TOKEN_POOL_IMPLEMENTATION.md)** - Token 池管理系统详细文档
- **[Function Call 转换](docs/function-call-tool-use-conversion.md)** - 工具调用转换机制说明

### 部署文档
- **[Docker 部署指南](docs/DOCKER_DEPLOYMENT.md)** - 完整的容器化部署方案
- **[Cloudflare 设置](CLOUDFLARE_SETUP.md)** - Cloudflare Worker 配置指南
- **[Cloudflare 部署](CLOUDFLARE_WORKER_DEPLOY.md)** - Worker 部署详细步骤

## 📞 支持

如有问题和疑问:
1. 查看故障排除部分
2. 查看服务器日志获取错误详情
3. 创建包含重现步骤的 issue
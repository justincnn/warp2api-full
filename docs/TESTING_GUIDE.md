# 测试指南

本文档说明了项目中各个测试脚本的用途和使用方法。

## 测试脚本概览

### 核心功能测试

#### `test_anthropic_endpoint.py`
**用途**: 测试 Anthropic Messages API 端点的基本功能
**功能**:
- 测试 `/v1/messages` 端点
- 验证 Anthropic 格式的请求和响应
- 检查消息格式转换

**使用方法**:
```bash
python test_anthropic_endpoint.py
```

#### `test_anthropic_streaming.py`
**用途**: 测试 Anthropic Messages API 的流式响应
**功能**:
- 测试 SSE 流式响应
- 验证 Anthropic 格式的流式数据
- 检查事件类型和数据格式

**使用方法**:
```bash
python test_anthropic_streaming.py
```

#### `test_token_pool.py`
**用途**: 测试 Token 池管理系统
**功能**:
- 验证 Token 池的创建和管理
- 测试 429 错误处理和自动切换
- 检查 Cloudflare Worker 集成
- 验证并发请求处理

**使用方法**:
```bash
# 需要配置 Cloudflare 凭证
export CLOUDFLARE_API_TOKEN="your_token"
export CLOUDFLARE_ACCOUNT_ID="your_account_id"
python test_token_pool.py
```

### 系统集成测试

#### `test_anonymous_user.py`
**用途**: 测试匿名用户创建和认证功能
**功能**:
- 测试随机化浏览器特征头生成
- 验证匿名用户创建流程
- 测试 JWT Token 获取和刷新
- 检查认证系统的各个组件

**使用方法**:
```bash
python test_anonymous_user.py
```

#### `test_worker_system.py`
**用途**: 测试 Cloudflare Worker 系统
**功能**:
- 测试 Worker 部署和管理
- 验证 Worker 脚本功能
- 检查分布式 IP 获取
- 测试 Worker 清理机制

**使用方法**:
```bash
# 需要配置 Cloudflare 凭证
export CLOUDFLARE_API_TOKEN="your_token"
export CLOUDFLARE_ACCOUNT_ID="your_account_id"
python test_worker_system.py
```

#### `test_detailed_tool_issue.py`
**用途**: 详细测试工具调用相关问题
**功能**:
- 测试 OpenAI Function Calling 转换
- 验证工具调用参数处理
- 检查工具调用结果处理
- 测试复杂的工具调用场景

**使用方法**:
```bash
python test_detailed_tool_issue.py
```

## 测试环境要求

### 基本要求
- Python 3.13+
- 项目依赖已安装 (`uv sync` 或 `pip install -e .`)
- 两个服务器正在运行:
  - Protobuf 桥接服务器 (`python server.py`)
  - 多格式 API 服务器 (`python openai_compat.py`)

### 可选配置
- **Cloudflare 凭证** (用于 Token 池测试):
  ```bash
  export CLOUDFLARE_API_TOKEN="your_api_token"
  export CLOUDFLARE_ACCOUNT_ID="your_account_id"
  ```

## 测试执行顺序

### 1. 基础功能验证
```bash
# 1. 测试匿名用户创建
python test_anonymous_user.py

# 2. 测试 Anthropic 端点
python test_anthropic_endpoint.py

# 3. 测试流式响应
python test_anthropic_streaming.py
```

### 2. 高级功能测试
```bash
# 4. 测试工具调用（如果需要）
python test_detailed_tool_issue.py

# 5. 测试 Worker 系统（需要 CF 凭证）
python test_worker_system.py

# 6. 测试 Token 池（需要 CF 凭证）
python test_token_pool.py
```

## 测试结果解读

### 成功指标
- ✅ 所有测试通过
- 🎉 功能正常工作
- 📊 性能指标正常

### 失败处理
- ❌ 测试失败 - 检查错误信息
- ⚠️ 警告 - 功能可用但有问题
- 🔧 需要修复 - 查看具体错误

### 常见问题

#### 连接错误
- 确保两个服务器都在运行
- 检查端口是否被占用 (8000, 8010)
- 验证网络连接

#### 认证错误
- 检查 JWT Token 是否有效
- 验证匿名用户创建是否成功
- 查看认证相关日志

#### Cloudflare 错误
- 验证 API Token 权限
- 检查 Account ID 是否正确
- 确认 Workers 配额未超限

## 持续集成

### 自动化测试
建议在以下情况运行测试:
- 代码提交前
- 部署到生产环境前
- 定期健康检查

### 测试覆盖
当前测试覆盖:
- ✅ 基本 API 功能
- ✅ 格式转换
- ✅ 流式响应
- ✅ 认证系统
- ✅ Token 池管理
- ✅ 工具调用
- ✅ Worker 系统

## 贡献指南

### 添加新测试
1. 在根目录创建 `test_*.py` 文件
2. 遵循现有测试的结构和风格
3. 添加适当的错误处理和日志
4. 更新此文档

### 测试最佳实践
- 使用描述性的测试名称
- 包含成功和失败场景
- 添加适当的断言和验证
- 提供清晰的错误信息
- 清理测试资源

---

*此测试指南帮助开发者和用户理解和使用项目的测试套件，确保系统的稳定性和可靠性。*
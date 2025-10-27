# Warp2Api 更新日志

## [最新版本] - 2025-09-24

### 🐛 Bug 修复和稳定性改进

#### 关键问题修复
- **修复空 content 导致的 `<nil>` 错误**: 解决展开工具调用时 content=None 导致的请求失败
- **Git diff 单文件约束**: 添加明确指导，避免多文件同时 diff 导致的执行问题
- **工具调用序列清理**: 自动清理不完整的 tool_use/tool_result 序列
- **响应分段优化**: 实现智能文本分段，解决超长响应中断问题

#### 性能优化
- **超时时间调整**: API 客户端超时从 60 秒提升到 120 秒，适应长任务处理
- **流式响应改进**: Anthropic SSE 添加流完成状态跟踪，OpenAI SSE 处理上下文重置事件

#### 代码质量提升
- **工具限制模块化**: 提取 RESTRICTED_TOOLS 为共享常量，创建统一的格式化函数
- **增强日志跟踪**: Anthropic 转换器添加详细的工具调用跟踪日志
- **友好提示改进**: 上下文重置时提供任务延续提示

### 📚 文档更新
- **新增故障排查指南**: `docs/TROUBLESHOOTING.md` - 常见问题解决方案和调试技巧
- **更新 README**: 添加故障排查文档链接
- **清理测试文件**: 移除临时测试脚本和调试文件

## [前一版本] - 2025-09-19

### 🎉 重大功能更新

#### 双 API 格式支持
- **新增 Anthropic Messages API 兼容性**: 在原有 OpenAI Chat Completions API 基础上，新增完整的 Anthropic Messages API 支持
- **双向格式转换**: 实现 OpenAI ↔ Anthropic 格式的自动双向转换
- **统一端点**: 同一服务器同时支持 `/v1/chat/completions` 和 `/v1/messages` 端点
- **流式响应兼容**: 两种 API 格式都支持标准的 SSE 流式响应

#### Token 池管理系统
- **智能 Token 池**: 维护 2-3 个预备 token，实现无缝切换
- **Cloudflare Worker 集成**: 利用 CF 分布式 IP 池突破 IP 限制
- **自动 429 处理**: 遇到速率限制自动切换备用 token，不中断对话
- **后台刷新**: 异步后台任务自动刷新过期 token

### 🔧 技术改进

#### 架构优化
- **双服务器架构**: Protobuf 桥接服务器 + 多格式 API 服务器
- **格式转换层**: 新增 `anthropic_converter.py` 和 `anthropic_sse_transform.py`
- **消息重排序**: 优化消息处理逻辑，适配 Anthropic 风格对话
- **工具调用增强**: 完善 OpenAI Function Calling 和 Anthropic Tool Use 支持

#### 性能提升
- **超时优化**: 将请求超时从 60 秒增加到 180 秒
- **并发处理**: 优化 Token 池的并发获取和刷新
- **错误恢复**: 多层重试机制和降级策略

### 📁 新增文件

#### 核心功能
- `protobuf2openai/anthropic_converter.py` - Anthropic ↔ OpenAI 格式转换器
- `protobuf2openai/anthropic_sse_transform.py` - Anthropic 格式流式响应处理器
- `warp_token_pool.py` - Token 池管理系统
- `warp_token_manager.py` - Cloudflare Worker 部署管理
- `warp_request_handler.py` - 请求拦截和自动重试
- `cloudflare-worker.js` - Cloudflare Worker 脚本

#### 测试和文档
- `test_anthropic_endpoint.py` - Anthropic 端点测试
- `test_anthropic_streaming.py` - Anthropic 流式测试
- `test_token_pool.py` - Token 池测试
- `docs/TOKEN_POOL_IMPLEMENTATION.md` - Token 池实现文档

### 🔄 文件修改

#### 主要更新
- `protobuf2openai/router.py` - 新增 `/v1/messages` 路由支持
- `protobuf2openai/models.py` - 添加 Anthropic API 数据模型
- `protobuf2openai/packets.py` - 优化消息转换和工具调用处理
- `warp2protobuf/core/auth.py` - 集成 Token 池管理
- `README.md` - 更新文档，反映双 API 支持

#### 配置更新
- `pyproject.toml` - 更新项目依赖和脚本配置
- `.env.example` - 新增 Cloudflare 配置示例

### 🛠️ 工具和脚本

#### 开发工具
- `fix_timeout_and_todo.py` - 超时和 Todo 处理问题修复脚本
- `test_worker_system.py` - Worker 系统测试
- `test_detailed_tool_issue.py` - 详细工具问题测试

### 🔐 安全增强

#### 认证改进
- **匿名 Token 自动获取**: 无需预配置，程序自动获取匿名访问令牌
- **JWT 自动刷新**: 智能 Token 生命周期管理
- **工具调用限制**: 添加安全约束，禁止调用危险的内部工具

### 📊 性能指标

#### Token 池统计
- 总请求数跟踪
- 成功切换次数统计
- 429 错误命中率监控
- 平均 Token 年龄分析

### 🐛 问题修复

#### 已解决问题
- **超时问题**: 修复长时间请求的超时问题
- **工具调用**: 完善工具调用的参数处理和错误处理
- **流式响应**: 修复流式响应中的数据格式问题
- **消息重排序**: 解决 Anthropic 风格消息的处理问题

### 🔮 未来规划

#### 短期目标
- 完善监控面板和统计功能
- 优化 Token 池的智能预测能力
- 增强错误处理和日志记录

#### 长期目标
- 支持更多 AI 服务提供商
- 实现分布式 Token 池
- 添加 Web UI 管理界面

---

## 开发者注意事项

### 启动顺序
1. 先启动 Protobuf 桥接服务器: `python server.py`
2. 再启动多格式 API 服务器: `python openai_compat.py`
3. 等待"热身"完成后开始使用

### 环境配置
- 基本使用无需配置，程序自动获取匿名 token
- 可选配置 Cloudflare 凭证启用 Token 池功能
- 详见 `.env.example` 配置示例

### 测试验证
- 使用 `test_anthropic_endpoint.py` 测试 Anthropic API
- 使用 `test_token_pool.py` 验证 Token 池功能
- 使用 `quick_test.py` 快速验证基本功能

---

*此更新日志记录了项目的重大改进和功能增强，为用户和开发者提供了完整的变更追踪。*
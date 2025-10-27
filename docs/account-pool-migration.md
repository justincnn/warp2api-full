# Warp2Api 账号池改造计划（邮箱注册 → 账号池发放）

最后更新：2025-10-14（移植完成版）

## 背景与目标

当前 Warp2Api 的令牌获取存在匿名/Worker/本地刷新等多路径：
- 匿名用户创建 + Firebase 交换 + proxy refresh（匿名额度）
- Cloudflare Worker 单/多账号轮换
- 本地 `.env` 中的 WARP_JWT / WARP_REFRESH_TOKEN 刷新

目标：
- 全面改为“邮箱注册 + 账号池维护 + 账号池HTTP服务发放/回收”。
- Warp2Api 不再直接创建匿名账号或走 Worker，统一向账号池服务申请租约（lease）获取 access_token。
- 令牌刷新、回收、封禁处理由账号池统一治理。

## 总体方案

客户端请求 → Warp2Api 向账号池HTTP服务（pool_service）请求账号凭据（租约）→
将 access_token 注入 Protobuf 主服务请求 → 编码/转发至 Warp 官方接口 →
请求结束或失败时按需回收（release）。

在 Warp2Api 内部：
- 检测 `POOL_SERVICE_BASE_URL` 存在时：
  - 所有获取/刷新 token 的操作均委托给账号池HTTP服务。
- 不存在时（开发/回退模式）：
  - 可选择保留原有匿名/Worker 路径（默认建议关闭，仅开发时启用）。

## 账号池HTTP服务接口（以 warp-register 为准）

> 参考 ./warp-register/pool_service.py 与 README.md。

- Base URL（默认本地开发）：http://localhost:8019
- 当前未看到服务间鉴权头配置（x-internal-key 等未实现）；如需加密通道/鉴权，可后续扩展。
- 并发安全：服务内使用内存锁与会话（session_id）机制，分配时会将账户标记为 locked，释放时解锁。

1) 分配账户（Allocate/Lease）
- POST /api/accounts/allocate
- 请求 JSON：
```json
{ "count": 1, "session_duration": 1800 }
```
- 成功响应 JSON：
```json
{
  "success": true,
  "session_id": "uuid",
  "accounts": [
    {
      "email": "...",
      "local_id": "...",
      "id_token": "...",
      "refresh_token": "...",
      "client_id": "...",
      "outlook_refresh_token": "...",
      "proxy_info": "...",
      "user_agent": "...",
      "email_password": "...",
      "last_used": "...",
      "created_at": "..."
    }
  ],
  "expires_at": 173xxx
}
```
- 失败：503（No available accounts）或 500。

2) 释放会话（Release）
- POST /api/accounts/release
- 请求 JSON：`{ "session_id": "uuid" }`
- 响应：`{ "success": true, "message": "Session released" }` 或错误信息。

3) 标记封禁（Mark Blocked）
- POST /api/accounts/mark_blocked
- 请求 JSON：`{ "jwt_token": "..." (可选), "email": "..." (可选) }`
- 用途：上报疑似封禁账号，池会将其状态改为 blocked，并从缓存与锁列表移除。

4) 状态与健康
- GET /api/status → 返回池内统计（active/locked/available/active_sessions 等）
- GET /api/health → 基本健康检查（status=healthy）

令牌获取说明：
- 分配返回的 account 包含 refresh_token/id_token；客户端需调用 Warp 官方 refresh 接口换取 access_token。
- warp-register 已在模块 warp2protobuf/core/pool_auth.py 中提供了参考实现（_get_access_token_from_account），支持代理重试与 id_token 兜底。

错误/异常建议（客户端侧策略）：
- 503/超时：指数退避重试
- 分配到无 refresh_token 且无 id_token：立即释放会话并重试一次分配
- 访问令牌刷新失败：尝试 id_token 兜底，否则释放会话并重试

## 配置变更

新增环境变量：
- `POOL_SERVICE_BASE_URL`（必需以启用账号池模式）
- `POOL_SERVICE_API_KEY`（建议）
- `POOL_MIN_TTL_SECONDS`（可选，默认 900）

在账号池模式下：
- 不再写入 `.env` 的 `WARP_JWT`/`WARP_REFRESH_TOKEN`（仅内存使用）。

## 代码改造清单

说明：以下引用采用 file_path:line_number 便于导航。

1) 鉴权核心改造
- 替换 `get_valid_jwt` 为优先从账号池租约获取：
  - warp2protobuf/core/auth.py:150-201
- 刷新逻辑适配账号池：
  - warp2protobuf/core/auth.py:50-90（原 refresh_jwt_token）
- 废弃匿名获取触发（账号池模式下不走）：
  - warp2protobuf/core/auth.py:312-427（acquire_anonymous_access_token）

2) 服务启动流程
- 启用账号池时，跳过 Cloudflare Worker/本地多账号池初始化路径：
  - server.py:463-557
- JWT 检查阶段：账号池模式下不再调用匿名 token 申请：
  - server.py:561-579

3) OpenAI 兼容层
- 路由无需改，但确保所有请求前的鉴权依赖新的 `get_valid_jwt`：
  - openai_compat.py（通过 protobuf2openai.app 内部间接调用）

4) 可选：抽象池客户端
- 初期为减少文件改动，先在 `auth.py` 内部实现最小 HTTP 客户端；后续复杂再抽出。

## 失败与回退策略

- 账号池不可用（超时/5xx）：重试 3 次（指数退避），仍失败：
  - 生产：启动失败/请求失败（推荐）
  - 开发：可配置软回退到旧路径（默认关闭）
- 租约无效/立即过期：立即 `release(reason=error)` 并重试一次新的 lease。
- 在线请求过期：优先 `refresh`；失败则 `release` + 重新 `lease`。

## 测试计划

- 替换/新增：
  - 成功租约→请求可用
  - 立即过期→重试新的租约
  - 刷新成功/失败分支
  - release 正常与 error 分支
  - 流式（SSE）路径不中断（见 `protobuf2openai/anthropic_sse_transform.py` 与 `protobuf2openai/sse_transform.py`）
- 现有用例参考：
  - test_anthropic_streaming.py（存在于项目中，需确保不回归）

## 迁移步骤

1) 部署/就绪 warp-register（账号池）
2) 在 Warp2Api 设置 `POOL_SERVICE_BASE_URL`/`POOL_SERVICE_API_KEY`
3) 启动 server.py 与 openai_compat.py，检查健康（账号池 healthz + 本服务 /healthz）
4) 跑回归测试与联调
5) 逐步上线，观察池容量与错误率

## 回滚方案

- 取消 `POOL_SERVICE_BASE_URL` 环境变量 → 回到原有路径（仅在开发环境建议）。

## 风险与缓解

- 池容量不足/限流：增加 backoff 与熔断，池侧扩容
- 租约泄漏：确保所有失败路径均尝试 release（带 reason）
- token 泄漏：不落盘 .env，仅内存使用；日志脱敏

## 实现细节更新（2025-10-14）

- 循环式 429 上报与重试（账号池-only）
  - 请求失败返回 429（限流）且为首轮尝试时：
    1) 调用 Pool Service: POST /api/accounts/mark_blocked，上报当前 jwt/email
    2) 释放当前会话：POST /api/accounts/release
    3) 重新从账号池 acquire 新会话并重试一次
  - 不再使用匿名/本地/Cloudflare Token 池回退。
- 会话释放点
  - 每次请求完成（成功/失败）均释放本轮会话；异常情况下在错误分支立即释放。
- SSE 端点改造（方案 A）
  - /api/warp/send_stream_sse 复用 api_client 流式函数，不再在端点内发起 httpx 或自行获取 JWT。

## 进度追踪

- [x] 调研现有令牌获取与刷新流程（已完成）
- [x] 在 Warp2Api 中实现 pool_service 客户端（可配置、重试与超时）
- [x] 重构 auth 模块（仅账号池，无回退；禁用匿名/Worker）
- [x] server.py 与 protobuf_routes.py 接入新鉴权流程（账号池不可用→启动失败；SSE 端复用 api_client）
- [x] 新增配置项与健康检查，更新 README/CLAUDE.md（已完成）
- [x] 更新 docker-compose.yml 配置（已完成）
- [ ] 更新测试（含流式、刷新、错误处理）
- [ ] 编写迁移与回滚指南

## 迁移完成总结（2025-10-14）

### 核心架构改造已完成
- ✅ **鉴权系统**：完全重构为账号池-only 模式，移除所有回退逻辑
- ✅ **账号池客户端**：实现 pool_auth.py 模块，支持 acquire/release/mark_blocked 操作
- ✅ **429 处理**：实现循环重试机制（mark_blocked → release → acquire → retry once）
- ✅ **会话管理**：每次请求都有完整的会话生命周期（acquire → use → release）
- ✅ **SSE 流式**：重构 protobuf_routes.py 复用 api_client 统一逻辑
- ✅ **服务启动**：添加账号池健康检查，失败时阻止启动
- ✅ **配置更新**：docker-compose.yml 移除 Cloudflare 变量，添加账号池配置

### 当前系统状态
**架构流程**：
```
客户端请求 → Warp2Api (8010/8001) → 账号池服务 (8019) → 获取租约 → Warp官方API
```

**启动顺序**：
1. 账号池服务：`cd warp-register && python pool_service.py` (端口 8019)
2. Protobuf 桥接：`python server.py` (端口 8001)
3. API 服务：`python openai_compat.py` (端口 8010)

**关键配置**：
```bash
POOL_SERVICE_BASE_URL=http://localhost:8019  # 必需
POOL_SERVICE_API_KEY=your-key-here         # 可选
POOL_MIN_TTL_SECONDS=900                   # 可选
```

### 已移除的旧功能
- ❌ Cloudflare Worker 令牌池
- ❌ 本地 .env 令牌刷新回退
- ❌ 匿名令牌创建流程
- ❌ MultiAccountTokenService 多账号管理
- ❌ 所有旧的回退策略

### 待完成的收尾工作
- [ ] 更新测试用例（覆盖新的 429 处理和会话流程）
- [ ] 更新 README.md 使用说明
- [ ] 生产部署验证

## 待确认事项（需要你的输入）

- 账号池实际接口路径/字段是否与本文一致？若不同请提供确定版。
- 服务间鉴权方式（固定 header key 是否可行）。
- 是否保证同一账号同一时间只会被一个租约持有（并发安全）。
- 封禁/失效账号的处置策略（是否自动从池移除）。

## 更新日志

- **v1.0 (2025-10-14)** - 账号池改造完成
  - 完成核心架构迁移：账号池-only 模式
  - 实现 pool_auth.py 账号池客户端
  - 重构鉴权系统，移除所有回退逻辑
  - 添加 429 循环重试机制
  - 更新配置文件和健康检查
  - SSE 流式响应兼容账号池架构
- **v0.1** - 初始化文档，梳理方案与改造点

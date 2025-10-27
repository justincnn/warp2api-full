# Cloudflare Worker 自动化部署配置指南

## 🔧 环境变量配置

为了使用 Cloudflare Worker 自动化部署功能，需要配置以下环境变量：

### 必需的环境变量

```bash
# .env 文件
CLOUDFLARE_API_TOKEN=your_cloudflare_api_token_here
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id_here
```

## 🔑 获取 Cloudflare API Token

### 1. 登录 Cloudflare Dashboard
访问 [dash.cloudflare.com](https://dash.cloudflare.com) 并登录

### 2. 创建 API Token
1. 点击右上角头像 → "My Profile"
2. 选择 "API Tokens" 标签页
3. 点击 "Create Token"

### 3. 配置 Token 权限
选择 "Custom token" 并配置以下权限：

**Permissions:**
- `Account` - `Cloudflare Workers:Edit`
- `Zone` - `Zone:Read` (如果需要自定义域名)

**Account Resources:**
- `Include` - `All accounts` 或选择特定账户

**Zone Resources:**
- `Include` - `All zones` (可选)

### 4. 复制 Token
创建成功后，复制生成的 API Token（只会显示一次）

## 🆔 获取 Account ID

### 方法 1: Dashboard 右侧栏
1. 登录 Cloudflare Dashboard
2. 选择任意域名（或直接在首页）
3. 在右侧栏找到 "Account ID"
4. 点击复制

### 方法 2: API 查询
```bash
curl -X GET "https://api.cloudflare.com/client/v4/accounts" \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json"
```

## 📝 配置示例

### .env 文件示例
```bash
# Warp 相关（可选，系统会自动获取）
WARP_JWT=your_existing_jwt_token
WARP_REFRESH_TOKEN=your_refresh_token

# Cloudflare Worker 自动化（必需）
CLOUDFLARE_API_TOKEN=1234567890abcdef1234567890abcdef12345678
CLOUDFLARE_ACCOUNT_ID=abcdef1234567890abcdef1234567890

# 其他配置
HOST=127.0.0.1
PORT=8010
BRIDGE_BASE_URL=http://localhost:8000
```

## 🚀 使用方式

配置完成后，系统会自动使用 Cloudflare Worker 方案：

### 自动模式（推荐）
```python
# 系统会自动检测环境变量并使用最佳方案
from warp2protobuf.core.auth import get_valid_jwt

# 自动获取有效 token（优先使用 Worker 方案）
token = await get_valid_jwt()
```

### 手动模式
```python
from warp_token_manager import get_fresh_warp_token

# 强制使用 Worker 方案获取新 token
token = await get_fresh_warp_token()
```

## 🔄 工作流程

1. **检查现有 token** - 如果有效则直接使用
2. **需要新 token 时**：
   - 生成随机 Worker 名称（如 `warp-token-1642345678-abc12345`）
   - 部署 Worker 到 Cloudflare
   - 调用 Worker 的 `/token` 端点
   - 获取访问令牌
   - 删除 Worker 释放资源
3. **保存 token** - 更新 `.env` 文件

## ⚡ 优势

- **绕过 IP 限制** - 每次使用新的 Worker IP
- **自动清理** - 用完即删，不占用资源
- **高成功率** - 避免"一小时一个 IP 只能申请一次"的限制
- **零成本** - 利用 Cloudflare 免费套餐
- **自动回退** - Worker 失败时自动使用直接请求

## 🛠️ 故障排除

### 常见错误

#### 1. API Token 权限不足
```
Error: Worker deployment failed: 403 Forbidden
```
**解决方案**: 确保 API Token 有 `Cloudflare Workers:Edit` 权限

#### 2. Account ID 错误
```
Error: Account not found
```
**解决方案**: 检查 `CLOUDFLARE_ACCOUNT_ID` 是否正确

#### 3. Worker 部署失败
```
Error: Worker deployment failed: 400 Bad Request
```
**解决方案**: 检查 `cloudflare-worker.js` 文件是否存在且语法正确

#### 4. 网络超时
```
Error: Request timeout
```
**解决方案**: 检查网络连接，或增加超时时间

### 调试模式

启用详细日志：
```python
import logging
logging.basicConfig(level=logging.DEBUG)

# 然后运行你的代码
```

### 手动测试

测试 API Token 和 Account ID：
```bash
# 测试 API Token
curl -X GET "https://api.cloudflare.com/client/v4/user/tokens/verify" \
  -H "Authorization: Bearer YOUR_API_TOKEN"

# 测试 Account 访问
curl -X GET "https://api.cloudflare.com/client/v4/accounts/YOUR_ACCOUNT_ID" \
  -H "Authorization: Bearer YOUR_API_TOKEN"
```

## 📊 监控

### 查看 Worker 使用情况
1. 登录 Cloudflare Dashboard
2. 进入 "Workers & Pages"
3. 查看使用统计和日志

### 成本控制
- 免费套餐：每天 100,000 次请求
- 每次获取 token 约消耗 3-4 次请求
- 理论上每天可获取 25,000+ 个 token

## 🔒 安全注意事项

1. **保护 API Token** - 不要提交到代码仓库
2. **最小权限原则** - 只给 Token 必需的权限
3. **定期轮换** - 建议定期更新 API Token
4. **监控使用** - 定期检查 Worker 使用情况

## 🎉 完成

配置完成后，你的 Warp2Api 服务将能够：
- 自动绕过 IP 限制
- 无限制获取匿名 token
- 提供更稳定的服务体验

享受无限制的 Warp AI 服务吧！🚀
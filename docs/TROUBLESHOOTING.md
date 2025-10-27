# Warp2Api 故障排查和解决方案

## 常见问题和解决方案

### 1. Claude Code 交互中断问题

#### 问题表现
- Claude Code 在执行工具调用时突然停止响应
- 错误信息：`Invalid request: Expected user query or tool call result as input; got <nil>`

#### 根本原因
1. **工具调用序列不完整**：assistant 发出 tool_use 但缺少对应的 tool_result
2. **空内容问题**：tool_result 的 content 为空或 None
3. **消息展开问题**：多个工具调用展开时设置 content=None

#### 解决方案
```python
# 1. 修复空内容问题 (protobuf2openai/reorder.py)
# 错误：content=None
expanded.append(ChatMessage(role="assistant", content="", tool_calls=[tc]))

# 2. 清理不完整的工具调用序列
history = clean_incomplete_tool_calls(history)

# 3. 跳过 content 为空的 tool_result
if not msg.content or (isinstance(msg.content, str) and not msg.content.strip()):
    logger.warning(f"跳过content为空的tool_result: {msg.tool_call_id}")
    continue
```

### 2. Git Diff 多文件执行问题

#### 问题表现
- 执行 `git diff file1.py file2.py` 时出现执行失败
- Claude Code 无法获取完整的 diff 输出

#### 根本原因
Warp API 对某些命令的多文件参数处理有限制

#### 解决方案
在工具限制提示中添加明确指导：
```python
RESTRICTED_TOOLS = [...]

def get_tool_restrictions_text() -> str:
    tools_list = "\n".join([f"- `{tool}`" for tool in RESTRICTED_TOOLS])
    return f"""<ALERT>you are not allowed to call following tools:
{tools_list}

IMPORTANT: When using git diff or similar commands to view file changes,
always check ONE file at a time to avoid execution issues.

Example:
- ✅ Good: git diff file1.py
- ✅ Good: git diff file2.py
- ❌ Avoid: git diff file1.py file2.py</ALERT>"""
```

### 3. 长文本响应中断问题

#### 问题表现
- 超长响应在流式传输时突然中断
- 部分内容丢失或显示不完整

#### 根本原因
Warp API 对单个消息段的长度有限制

#### 解决方案
实现智能文本分段（protobuf2openai/helpers.py）：
```python
CHUNK_SIZE = 1000  # 每段最大字符数

def smart_split_text(text: str, chunk_size: int) -> List[str]:
    """智能分割文本，尽量在合适的位置断开"""
    if len(text) <= chunk_size:
        return [text]

    # 优先在以下位置断开
    split_chars = ['\n\n', '\n', '. ', '。', '！', '？', ', ', '，', ' ']
    # ... 实现智能分割逻辑
```

### 4. Anthropic API 工具调用索引错误

#### 问题表现
- Anthropic 格式响应中工具调用索引不正确
- 多个工具调用时出现内容块索引混乱

#### 根本原因
工具调用和文本内容的 content_index 计算错误

#### 解决方案
```python
# protobuf2openai/anthropic_sse_transform.py
# 正确递增 content_index
if has_tool_calls:
    content_index += 1

current_tool_call = {
    "id": tool_call["id"],
    "type": "tool_use",
    "name": tool_call["function"]["name"],
    "input": json.loads(tool_call["function"]["arguments"])
}
```

### 5. 上下文重置后的任务丢失

#### 问题表现
- 上下文过长自动重置后，正在进行的任务信息丢失
- 用户不知道需要继续什么任务

#### 解决方案
处理上下文重置事件并提供友好提示：
```python
# protobuf2openai/sse_transform.py
if "update_task_description" in event_data:
    task_desc = event_data.get("update_task_description", {}).get("description", "")

    # 提取待处理任务
    if "Pending Tasks:" in task_desc:
        context_reset_pending_tasks = f"\n\n📋 **上下文已重置，但有待处理任务:**\n..."
        # 发送任务延续提示
        yield f"data: {json.dumps(continuation_delta, ensure_ascii=False)}\n\n"
```

## 性能优化建议

### 1. 超时时间调整
```python
# warp2protobuf/warp/api_client.py
# 从 60 秒提升到 120 秒，适应长任务
async with httpx.AsyncClient(http2=True, timeout=httpx.Timeout(120.0), ...) as client:
```

### 2. 流式响应优化
- 添加流完成状态跟踪，避免处理已结束流的后续事件
- 使用 `stream_completed` 标志防止重复处理

### 3. 日志增强
```python
# 添加详细的工具调用跟踪
logger.info(f"[Anthropic Converter] 检测到 {len(tool_uses)} 个 tool_use: {tool_uses}")
logger.info(f"[Anthropic Converter] 检测到 {len(tool_results)} 个 tool_result: {tool_results}")

# 检查未匹配的工具调用
unmatched_tool_uses = [tu for tu in tool_uses if tu not in tool_results]
if unmatched_tool_uses:
    logger.warning(f"⚠️ 发现未匹配的 tool_use: {unmatched_tool_uses}")
```

## 调试技巧

### 1. 启用详细日志
```bash
export LOG_LEVEL=DEBUG
python server.py
```

### 2. 监控 WebSocket 连接
访问 `http://localhost:8000/ws` 查看实时消息流

### 3. 测试工具调用序列
```python
# 使用测试脚本验证工具调用
python test_api_tool_calls.py
```

### 4. 检查消息重排序
```python
# 在 router.py 中添加日志
logger.info("[OpenAI Compat] 清理前的消息数量: %d", len(history))
history = clean_incomplete_tool_calls(history)
logger.info("[OpenAI Compat] 清理后的消息数量: %d", len(history))
```

## 已知限制

1. **Token 限制**：匿名账号仅有 50 次调用额度
2. **工具限制**：某些内部工具被禁用，需通过 MCP 包装
3. **上下文长度**：过长上下文会触发自动重置
4. **并发限制**：建议使用 Token 池管理并发请求

## 相关文档

- [Token 池实现](./TOKEN_POOL_IMPLEMENTATION.md)
- [Docker 部署指南](./DOCKER_DEPLOYMENT.md)
- [测试指南](./TESTING_GUIDE.md)
- [Function Call 转换机制](./function-call-tool-use-conversion.md)
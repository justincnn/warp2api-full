# Function Call 和 Tool Use 转换机制

本文档详细说明了 Warp2Api 项目中 OpenAI Function Calling 与 Warp AI 服务工具调用系统之间的转换机制。

## 概述

Warp2Api 实现了 OpenAI Chat Completions API 与 Warp AI 服务之间的双向工具调用转换，支持：

- OpenAI 格式的工具定义和调用
- Warp 原生的工具调用系统
- 流式响应中的工具调用处理
- MCP (Model Context Protocol) 工具集成

## 转换架构

### 双向转换流程

```
OpenAI 客户端 ←→ OpenAI API 服务器 ←→ Protobuf 桥接服务器 ←→ Warp AI 服务
   (tools)         (转换层)           (protobuf编解码)        (tool_call)
```

## 1. OpenAI 到 Warp 的转换

### 1.1 数据模型定义

**OpenAI 输入格式** (`protobuf2openai/models.py:7-31`):

```python
class ChatMessage(BaseModel):
    role: str
    content: Optional[Union[str, List[Dict[str, Any]]]] = ""
    tool_call_id: Optional[str] = None          # 工具调用结果ID
    tool_calls: Optional[List[Dict[str, Any]]] = None  # 工具调用请求
    name: Optional[str] = None

class OpenAITool(BaseModel):
    type: str = "function"
    function: OpenAIFunctionDef  # 包含 name, description, parameters

class OpenAIFunctionDef(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
```

### 1.2 工具定义转换

**转换逻辑** (`protobuf2openai/router.py:117-128`):

```python
if req.tools:
    mcp_tools: List[Dict[str, Any]] = []
    for t in req.tools:
        if t.type != "function" or not t.function:
            continue
        mcp_tools.append({
            "name": t.function.name,
            "description": t.function.description or "",
            "input_schema": t.function.parameters or {},
        })
    if mcp_tools:
        packet.setdefault("mcp_context", {}).setdefault("tools", []).extend(mcp_tools)
```

**转换映射**:
- OpenAI `tools[].function.name` → Warp `mcp_context.tools[].name`
- OpenAI `tools[].function.description` → Warp `mcp_context.tools[].description`
- OpenAI `tools[].function.parameters` → Warp `mcp_context.tools[].input_schema`

### 1.3 工具调用请求转换

**Assistant 消息转换** (`protobuf2openai/packets.py:75-86`):

```python
elif m.role == "assistant":
    _assistant_text = segments_to_text(normalize_content_to_list(m.content))
    if _assistant_text:
        msgs.append({"id": mid, "task_id": task_id, "agent_output": {"text": _assistant_text}})
    for tc in (m.tool_calls or []):
        msgs.append({
            "id": str(uuid.uuid4()),
            "task_id": task_id,
            "tool_call": {
                "tool_call_id": tc.get("id") or str(uuid.uuid4()),
                "call_mcp_tool": {
                    "name": (tc.get("function", {}) or {}).get("name", ""),
                    "args": (json.loads((tc.get("function", {}) or {}).get("arguments", "{}")) 
                           if isinstance((tc.get("function", {}) or {}).get("arguments"), str) 
                           else (tc.get("function", {}) or {}).get("arguments", {})) or {},
                },
            },
        })
```

### 1.4 工具调用结果转换

**Tool 消息转换** (`protobuf2openai/packets.py:87-101`):

```python
elif m.role == "tool":
    if m.tool_call_id:
        msgs.append({
            "id": str(uuid.uuid4()),
            "task_id": task_id,
            "tool_call_result": {
                "tool_call_id": m.tool_call_id,
                "call_mcp_tool": {
                    "success": {"results": segments_to_warp_results(normalize_content_to_list(m.content))}
                },
            },
        })
```

### 1.5 用户输入处理

**最新消息处理** (`protobuf2openai/packets.py:105-137`):

```python
def attach_user_and_tools_to_inputs(packet: Dict[str, Any], history: List[ChatMessage], system_prompt_text: Optional[str]) -> None:
    last = history[-1]
    if last.role == "user":
        user_query_payload: Dict[str, Any] = {"query": segments_to_text(normalize_content_to_list(last.content))}
        if system_prompt_text:
            user_query_payload["referenced_attachments"] = {
                "SYSTEM_PROMPT": {
                    "plain_text": f"""<ALERT>you are not allowed to call following tools:  
                    - `read_files`
                    - `write_files`
                    - `run_commands`
                    - `list_files`
                    - `str_replace_editor`
                    - `ask_followup_question`
                    - `attempt_completion`</ALERT>{system_prompt_text}"""
                }
            }
        packet["input"]["user_inputs"]["inputs"].append({"user_query": user_query_payload})
    elif last.role == "tool" and last.tool_call_id:
        packet["input"]["user_inputs"]["inputs"].append({
            "tool_call_result": {
                "tool_call_id": last.tool_call_id,
                "call_mcp_tool": {
                    "success": {"results": segments_to_warp_results(normalize_content_to_list(last.content))}
                },
            }
        })
```

## 2. Warp Protobuf 消息结构

### 2.1 ToolCallResult 定义

**Protobuf 结构** (`proto/request.proto:50-73`):

```protobuf
message ToolCallResult {
    string tool_call_id = 1;
    
    oneof result {
        RunShellCommandResult run_shell_command = 2;
        ReadFilesResult read_files = 3;
        SearchCodebaseResult search_codebase = 4;
        ApplyFileDiffsResult apply_file_diffs = 5;
        SuggestPlanResult suggest_plan = 6;
        SuggestCreatePlanResult suggest_create_plan = 7;
        GrepResult grep = 8;
        FileGlobResult file_glob = 9;
        RefineResult refine = 10;
        ReadMCPResourceResult read_mcp_resource = 11;
        CallMCPToolResult call_mcp_tool = 12;  // MCP 工具调用
        WriteToLongRunningShellCommandResult write_to_long_running_shell_command = 13;
        SuggestNewConversationResult suggest_new_conversation = 14;
        FileGlobV2Result file_glob_v2 = 15;
    }
}
```

### 2.2 MCP 工具支持

**MCP 工具定义** (`proto/request.proto:166-172`):

```protobuf
message MCPTool {
    string name = 1;
    string description = 2;
    google.protobuf.Struct input_schema = 3;
}

message MCPContext {
    repeated MCPResource resources = 1;
    repeated MCPTool tools = 2;
}
```

### 2.3 UserInputs 结构

**用户输入结构** (`proto/request.proto:40-48`):

```protobuf
message UserInputs {
    repeated UserInput inputs = 1;
    message UserInput {
        oneof input {
            UserQuery user_query = 1;
            ToolCallResult tool_call_result = 2;
        }
    }
}
```

## 3. Warp 到 OpenAI 的响应转换

### 3.1 流式响应处理

**SSE 转换逻辑** (`protobuf2openai/sse_transform.py:120-152`):

```python
# 工具调用检测和转换
tool_call = _get(message, "tool_call", "toolCall") or {}
call_mcp = _get(tool_call, "call_mcp_tool", "callMcpTool") or {}
if isinstance(call_mcp, dict) and call_mcp.get("name"):
    try:
        args_obj = call_mcp.get("args", {}) or {}
        args_str = json.dumps(args_obj, ensure_ascii=False)
    except Exception:
        args_str = "{}"
    tool_call_id = tool_call.get("tool_call_id") or str(uuid.uuid4())
    
    # 生成 OpenAI 格式的工具调用 delta
    delta = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created_ts,
        "model": model_id,
        "choices": [{
            "index": 0,
            "delta": {
                "tool_calls": [{
                    "index": 0,
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": call_mcp.get("name"), 
                        "arguments": args_str
                    },
                }]
            }
        }],
    }
    yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"
    tool_calls_emitted = True
```

### 3.2 完成原因处理

**流式完成标记** (`protobuf2openai/sse_transform.py:170-177`):

```python
if "finished" in event_data:
    done_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created_ts,
        "model": model_id,
        "choices": [{"index": 0, "delta": {}, "finish_reason": ("tool_calls" if tool_calls_emitted else "stop")}],
    }
    yield f"data: {json.dumps(done_chunk, ensure_ascii=False)}\n\n"
```

## 4. 转换映射表

### 4.1 请求转换映射

| OpenAI 字段 | Warp 字段 | 说明 |
|-------------|-----------|------|
| `tools[].function.name` | `mcp_context.tools[].name` | 工具名称 |
| `tools[].function.description` | `mcp_context.tools[].description` | 工具描述 |
| `tools[].function.parameters` | `mcp_context.tools[].input_schema` | 工具参数模式 |
| `messages[].tool_calls[].id` | `tool_call.tool_call_id` | 工具调用ID |
| `messages[].tool_calls[].function.name` | `tool_call.call_mcp_tool.name` | 工具调用名称 |
| `messages[].tool_calls[].function.arguments` | `tool_call.call_mcp_tool.args` | 工具调用参数 |
| `messages[].tool_call_id` | `tool_call_result.tool_call_id` | 工具结果ID |
| `messages[].content` (role=tool) | `tool_call_result.call_mcp_tool.success.results` | 工具执行结果 |

### 4.2 响应转换映射

| Warp 字段 | OpenAI 字段 | 说明 |
|-----------|-------------|------|
| `tool_call.tool_call_id` | `choices[].delta.tool_calls[].id` | 工具调用ID |
| `tool_call.call_mcp_tool.name` | `choices[].delta.tool_calls[].function.name` | 工具名称 |
| `tool_call.call_mcp_tool.args` | `choices[].delta.tool_calls[].function.arguments` | 工具参数 |
| `tool_call` 存在 | `finish_reason: "tool_calls"` | 完成原因 |

## 5. 关键处理流程

### 5.1 请求处理流程

1. **接收 OpenAI 请求**
   - 解析 `ChatCompletionsRequest` 包含 `tools` 和消息历史
   - 验证工具定义格式

2. **消息重排序**
   - 调用 `reorder_messages_for_anthropic()` 进行消息格式适配
   - 确保 system prompt 正确处理

3. **工具定义转换**
   - 将 OpenAI `tools` 转换为 Warp `mcp_context.tools`
   - 生成工具 schema 映射

4. **历史消息转换**
   - 遍历消息历史，转换为 Warp 格式
   - 处理 assistant 的 `tool_calls` 为 `tool_call` 消息
   - 处理 tool 的 `tool_call_id` 为 `tool_call_result` 消息

5. **当前输入处理**
   - 将最新用户消息或工具结果放入 `input.user_inputs`
   - 附加 system prompt 到 referenced_attachments

6. **Protobuf 编码**
   - 将 JSON 数据包编码为 protobuf 字节流
   - 发送到 Warp AI 服务

### 5.2 响应处理流程

1. **接收 Warp 响应**
   - 通过 SSE 流接收 Warp 响应
   - 解析 protobuf 数据为 JSON 格式

2. **工具调用检测**
   - 监听 `tool_call` 类型的消息
   - 提取 `call_mcp_tool` 信息

3. **OpenAI 格式转换**
   - 生成符合 OpenAI 格式的 `tool_calls` delta
   - 正确处理工具调用参数的 JSON 序列化

4. **流式响应生成**
   - 通过 SSE 发送工具调用事件
   - 设置正确的 `finish_reason`

5. **完成处理**
   - 发送 `[DONE]` 标记
   - 清理连接状态

## 6. 技术特点

### 6.1 兼容性保证

- **格式兼容**: 完全兼容 OpenAI Chat Completions API 格式
- **工具支持**: 支持 OpenAI Function Calling 规范
- **流式处理**: 支持流式响应中的工具调用

### 6.2 扩展性设计

- **MCP 集成**: 通过 MCP 协议支持多种工具类型
- **动态工具**: 运行时动态添加和移除工具定义
- **参数处理**: 灵活的参数序列化和反序列化

### 6.3 错误处理

- **工具调用失败**: 通过 `tool_call_result` 的错误分支处理
- **参数验证**: 在转换过程中验证参数格式
- **网络异常**: 支持重试和降级处理

## 7. 使用示例

### 7.1 客户端请求示例

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8010/v1",
    api_key="dummy"
)

response = client.chat.completions.create(
    model="claude-3-sonnet",
    messages=[
        {"role": "user", "content": "What files are in the current directory?"}
    ],
    tools=[{
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list"
                    }
                },
                "required": ["path"]
            }
        }
    }],
    tool_choice="auto",
    stream=True
)
```

### 7.2 转换后的 Warp 请求

```json
{
  "task_context": {
    "tasks": [{
      "id": "task-id",
      "description": "",
      "status": {"in_progress": {}},
      "messages": [
        {
          "id": "msg-id",
          "task_id": "task-id",
          "user_query": {"query": "What files are in the current directory?"}
        }
      ]
    }],
    "active_task_id": "task-id"
  },
  "input": {
    "user_inputs": {
      "inputs": [{
        "user_query": {
          "query": "What files are in the current directory?"
        }
      }]
    }
  },
  "mcp_context": {
    "tools": [{
      "name": "list_files",
      "description": "List files in a directory",
      "input_schema": {
        "type": "object",
        "properties": {
          "path": {
            "type": "string",
            "description": "Directory path to list"
          }
        },
        "required": ["path"]
      }
    }]
  }
}
```

### 7.3 Warp 响应转换

```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion.chunk",
  "created": 1234567890,
  "model": "claude-3-sonnet",
  "choices": [{
    "index": 0,
    "delta": {
      "tool_calls": [{
        "index": 0,
        "id": "call_123",
        "type": "function",
        "function": {
          "name": "list_files",
          "arguments": "{\"path\": \".\"}"
        }
      }]
    }
  }]
}
```

## 8. 相关文件

- **核心转换逻辑**: `protobuf2openai/packets.py`
- **路由处理**: `protobuf2openai/router.py`
- **数据模型**: `protobuf2openai/models.py`
- **流式转换**: `protobuf2openai/sse_transform.py`
- **Protobuf 定义**: `proto/request.proto`
- **工具处理**: `protobuf2openai/helpers.py`

这个转换机制实现了 OpenAI Function Calling 与 Warp AI 服务工具调用系统的无缝集成，为开发者提供了标准的 OpenAI API 接口来访问 Warp 的强大工具调用能力。
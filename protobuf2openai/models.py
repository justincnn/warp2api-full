from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: Optional[Union[str, List[Dict[str, Any]]]] = ""
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    name: Optional[str] = None


class OpenAIFunctionDef(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class OpenAITool(BaseModel):
    type: str = Field("function", description="Only 'function' is supported")
    function: OpenAIFunctionDef


class ChatCompletionsRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    tools: Optional[List[OpenAITool]] = None
    tool_choice: Optional[Any] = None


# Anthropic Models
class AnthropicTool(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None


class AnthropicMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]


class AnthropicMessagesRequest(BaseModel):
    model: Optional[str] = None
    messages: List[AnthropicMessage]
    system: Optional[Union[str, List[Dict[str, Any]]]] = None
    max_tokens: Optional[int] = 4096
    temperature: Optional[float] = None
    stream: Optional[bool] = False
    tools: Optional[List[AnthropicTool]] = None
    tool_choice: Optional[Any] = None 
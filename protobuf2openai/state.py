from __future__ import annotations

import uuid
from typing import Optional
from pydantic import BaseModel


class BridgeState(BaseModel):
    conversation_id: Optional[str] = None
    baseline_task_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_message_id: Optional[str] = None


STATE = BridgeState()

# Initialize tool ids lazily when needed

def ensure_tool_ids():
    if not STATE.tool_call_id:
        STATE.tool_call_id = str(uuid.uuid4())
    if not STATE.tool_message_id:
        STATE.tool_message_id = str(uuid.uuid4()) 
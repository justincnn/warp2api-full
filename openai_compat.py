#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenAI Chat Completions compatible server (system-prompt flavored)

Startup entrypoint that exposes the modular app implemented in protobuf2openai.
"""

from __future__ import annotations

import os
import asyncio

from protobuf2openai.app import app  # FastAPI app


if __name__ == "__main__":
    import uvicorn
    # Token 池初始化已在 protobuf2openai/app.py 的 startup 事件中处理
    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8010")),
        log_level="info",
    )

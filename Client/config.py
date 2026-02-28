from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Config:
    dashscope_api_key: str
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_chat_model: str = "qwen3.5-plus"
    dashscope_translate_model: str = "glm-4.7"

    maya_host: str = "127.0.0.1"
    maya_port: int = 7022
    maya_socket_timeout: float = 30.0

    agent_max_history_messages: int = 20
    agent_tool_repeat_limit: int = 3

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
            dashscope_base_url=os.getenv(
                "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).strip(),
            dashscope_chat_model=os.getenv("DASHSCOPE_CHAT_MODEL", "qwen3.5-plus").strip(),
            dashscope_translate_model=os.getenv("DASHSCOPE_TRANSLATE_MODEL", "glm-4.7").strip(),
            maya_host=os.getenv("MAYA_HOST", "127.0.0.1").strip(),
            maya_port=int(os.getenv("MAYA_PORT", "7022")),
            maya_socket_timeout=float(os.getenv("MAYA_SOCKET_TIMEOUT", "30")),
            agent_max_history_messages=int(os.getenv("AGENT_MAX_HISTORY_MESSAGES", "20")),
            agent_tool_repeat_limit=int(os.getenv("AGENT_TOOL_REPEAT_LIMIT", "3")),
        )


config = Config.from_env()

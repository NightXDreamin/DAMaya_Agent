from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── 路径 ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "settings.json"

# ── 兼容旧版 .env ─────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass  # python-dotenv 未安装时安全降级


def _load_settings() -> dict[str, Any]:
    """优先读取 settings.json，不存在则返回空字典（兼容旧版 .env 流程）。"""
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            # 过滤掉注释键（以 _ 开头）
            return {k: v for k, v in data.items() if not k.startswith("_")}
        except Exception as e:
            print(f"[Config] 读取 settings.json 失败，降级到 .env: {e}")
    return {}


_S = _load_settings()


def _get(key_json: str, key_env: str, default: Any) -> Any:
    """优先从 settings.json 取值，否则读取环境变量，否则使用 default。"""
    if key_json in _S:
        return _S[key_json]
    env_val = os.getenv(key_env)
    if env_val is not None:
        return env_val.strip()
    return default


@dataclass(frozen=True)
class Config:
    # ── LLM ─────────────────────────────────────────────────────────────────
    dashscope_api_key: str
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_chat_model: str = "qwen-turbo-latest"
    dashscope_translate_model: str = "qwen-turbo"
    dashscope_embedding_model: str = "text-embedding-v3"
    available_models: tuple[str, ...] = field(
        default_factory=lambda: ("qwen-turbo-latest",)
    )

    # ── Maya ─────────────────────────────────────────────────────────────────
    maya_host: str = "127.0.0.1"
    maya_port: int = 17022
    maya_socket_timeout: float = 30.0

    # ── Server ───────────────────────────────────────────────────────────────
    server_host: str = "127.0.0.1"
    server_port: int = 8000

    # ── Agent ────────────────────────────────────────────────────────────────
    agent_max_history_messages: int = 20
    agent_tool_repeat_limit: int = 3
    agent_max_react_rounds: int = 10

    # ── UI / 前端 ─────────────────────────────────────────────────────────────
    ui_animations_enabled: bool = True
    ui_rag_enabled_default: bool = True
    ui_theme: str = "dark"
    ui_animation_speed: float = 1.0

    # ── 工厂方法 ──────────────────────────────────────────────────────────────
    @classmethod
    def from_settings(cls) -> "Config":
        raw_models = _get("available_models", "", None)
        if isinstance(raw_models, list):
            available = tuple(str(m) for m in raw_models)
        else:
            available = (str(_get("chat_model", "DASHSCOPE_CHAT_MODEL", "qwen-turbo-latest")),)

        return cls(
            dashscope_api_key=str(_get("api_key", "DASHSCOPE_API_KEY", "")).strip(),
            dashscope_base_url=str(_get("base_url", "DASHSCOPE_BASE_URL",
                                        "https://dashscope.aliyuncs.com/compatible-mode/v1")).strip(),
            dashscope_chat_model=str(_get("chat_model", "DASHSCOPE_CHAT_MODEL", "qwen-turbo-latest")).strip(),
            dashscope_translate_model=str(_get("translate_model", "DASHSCOPE_TRANSLATE_MODEL", "qwen-turbo")).strip(),
            dashscope_embedding_model=str(_get("embedding_model", "DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v3")).strip(),
            available_models=available,
            maya_host=str(_get("maya_host", "MAYA_HOST", "127.0.0.1")).strip(),
            maya_port=int(_get("maya_port", "MAYA_PORT", 17022)),
            maya_socket_timeout=float(_get("maya_socket_timeout", "MAYA_SOCKET_TIMEOUT", 30)),
            server_host=str(_get("server_host", "SERVER_HOST", "127.0.0.1")).strip(),
            server_port=int(_get("server_port", "SERVER_PORT", 8000)),
            agent_max_history_messages=int(_get("agent_max_history_messages", "AGENT_MAX_HISTORY_MESSAGES", 20)),
            agent_tool_repeat_limit=int(_get("agent_tool_repeat_limit", "AGENT_TOOL_REPEAT_LIMIT", 3)),
            agent_max_react_rounds=int(_get("agent_max_react_rounds", "AGENT_MAX_REACT_ROUNDS", 10)),
            ui_animations_enabled=bool(_get("ui_animations_enabled", "UI_ANIMATIONS_ENABLED", True)),
            ui_rag_enabled_default=bool(_get("ui_rag_enabled_default", "UI_RAG_ENABLED_DEFAULT", True)),
            ui_theme=str(_get("ui_theme", "UI_THEME", "dark")).strip(),
            ui_animation_speed=float(_get("ui_animation_speed", "UI_ANIMATION_SPEED", 1.0)),
        )

    def to_ui_dict(self) -> dict[str, Any]:
        """返回安全的 UI 配置（不含 API key）。"""
        return {
            "chat_model": self.dashscope_chat_model,
            "available_models": list(self.available_models),
            "ui_animations_enabled": self.ui_animations_enabled,
            "ui_rag_enabled_default": self.ui_rag_enabled_default,
            "ui_theme": self.ui_theme,
            "ui_animation_speed": self.ui_animation_speed,
        }


# 全局单例
config = Config.from_settings()

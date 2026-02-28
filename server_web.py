from __future__ import annotations

import asyncio
import json
import logging
import threading

from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from Client.config import config
from Client.core.agent_loop import AgentCallbacks, AgentLoop
from Client.core.database import ChatDatabase
from Client.core.llm_client import LLMClient
from Client.core.rag import DualTrackRAG
from Client.maya_host.client import MayaSocketClient
from Client.tools.registry import ToolRegistry, register_default_maya_tools

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
DOCS_PATH = PROJECT_ROOT / "Client" / "data" / "maya_cmds_docs.json"

SYSTEM_PROMPT = """你是一个顶级的 Maya Technical Artist 助手。你的核心工作方式是【思考 -> 执行 -> 结构化汇报】。

【约束规则】
1. 在调用任何工具或回答前，必须先用 <think> (请保留英文标签，不要使用 <思考>) 标签输出你的分析与推导过程。
2. 绝对禁止使用"好的"、"让我看看"等废话口语开场白。
3. 最终结果必须使用 Markdown 结构化汇报，善用 emoji 作为视觉锚点：

## 🔍 诊断/执行结果
（简短说明当前状态）

### ✅ 成功项 / √ 正常状态
- `节点名/属性`：说明文字

### ❌ 失败项 / X 异常状态
- **错误对象**：具体原因

### 🎯 解决方案 / 下一步建议
1. 第一步...
"""

APPROVAL_TIMEOUT_SEC = 120
db = ChatDatabase()


class CreateSessionRequest(BaseModel):
    title: str | None = None


class IncomingChatPayload(BaseModel):
    text: str
    use_rag: bool = True


class WebSocketAgentCallbacks(AgentCallbacks):
    def __init__(self, queue: asyncio.Queue[dict[str, Any]], event_loop: asyncio.AbstractEventLoop):
        self._queue = queue
        self._loop = event_loop
        self._approval_event = threading.Event()
        self._approval_result: bool = False

    def _emit(self, payload: dict[str, Any]) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)

    def set_approval_result(self, approved: bool) -> None:
        self._approval_result = approved
        self._approval_event.set()

    def on_text_chunk(self, text: str) -> None:
        self._emit({"type": "stream", "content": text})

    def on_think_chunk(self, text: str) -> None:
        self._emit({"type": "think_stream", "content": text})

    def on_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self._emit({"type": "tool_call", "name": tool_name, "arguments": arguments})

    def on_status_update(self, content: str) -> None:
        self._emit({"type": "status_update", "content": content})

    def on_approval_required(self, tool_name: str, code_preview: str) -> bool:
        self._approval_event.clear()
        self._approval_result = False
        self._emit({
            "type": "approval_required",
            "name": tool_name,
            "preview": code_preview,
        })
        if not self._approval_event.wait(timeout=APPROVAL_TIMEOUT_SEC):
            self._emit({"type": "error", "message": f"审批超时（{APPROVAL_TIMEOUT_SEC}s），已自动拒绝"})
            return False
        return self._approval_result

    def on_tool_result(self, tool_name: str, result: dict[str, Any]) -> None:
        self._emit({"type": "tool_result", "name": tool_name, "result": result})

    def on_error(self, error: str) -> None:
        self._emit({"type": "error", "message": error})

    def on_complete(self) -> None:
        self._emit({"type": "done"})


app = FastAPI(title="DAMaya Agent Web API", version="1.5")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/sessions")
def get_sessions() -> list[dict[str, Any]]:
    return [{"id": s.id, "title": s.title, "created_at": s.created_at} for s in db.list_sessions()]


@app.post("/api/sessions")
def create_session(req: CreateSessionRequest) -> dict[str, Any]:
    record = db.create_session(req.title)
    return {"id": record.id, "title": record.title, "created_at": record.created_at}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict[str, str]:
    db.delete_session(session_id)
    return {"status": "ok"}


@app.get("/api/sessions/{session_id}/messages")
def get_messages(session_id: str) -> list[dict[str, Any]]:
    rows = db.get_messages(session_id)
    result = []
    for row in rows:
        payload: dict[str, Any] = {
            "id": row.id,
            "session_id": row.session_id,
            "role": row.role,
            "content": row.content,
            "timestamp": row.timestamp,
        }
        if row.tool_call_id:
            payload["tool_call_id"] = row.tool_call_id
        if row.tool_calls:
            try:
                payload["tool_calls"] = json.loads(row.tool_calls)
            except (json.JSONDecodeError, TypeError):
                payload["tool_calls"] = row.tool_calls
        result.append(payload)
    return result


def _build_agent_components(callbacks: AgentCallbacks) -> tuple[AgentLoop, DualTrackRAG]:
    maya_client = MayaSocketClient(host=config.maya_host, port=config.maya_port, timeout=config.maya_socket_timeout)
    registry = ToolRegistry()
    register_default_maya_tools(registry, maya_client)
    llm_client = LLMClient(api_key=config.dashscope_api_key, base_url=config.dashscope_base_url, chat_model=config.dashscope_chat_model)

    rag = DualTrackRAG(
        docs_path=DOCS_PATH,
        translate_api_key=config.dashscope_api_key,
        translate_base_url=config.dashscope_base_url,
        translate_model=config.dashscope_translate_model,
        selection_context_provider=lambda: registry.execute_tool("query_selection_context", {}),
    )
    loop = AgentLoop(
        llm_client=llm_client,
        tool_executor=registry.execute_tool,
        callbacks=callbacks,
        system_prompt=SYSTEM_PROMPT,
        tool_schemas=registry.get_all_schemas(),
        max_history_messages=config.agent_max_history_messages,
        tool_repeat_limit=config.agent_tool_repeat_limit,
        is_tool_dangerous=registry.is_dangerous_tool,
    )
    return loop, rag


def _run_turn_sync(session_id: str, user_text: str, use_rag: bool, callbacks: WebSocketAgentCallbacks) -> None:
    try:
        db.append_message(session_id=session_id, role="user", content=user_text)
        agent_loop, rag = _build_agent_components(callbacks)
        history = db.get_messages_for_llm(session_id=session_id, max_messages=config.agent_max_history_messages)
        agent_loop._messages = [agent_loop._messages[0], *history]
        before_len = len(agent_loop.messages)

        injected_context = ""
        if use_rag:
            callbacks.on_status_update("✨ 正在通过 RAG 解析场景上下文与意图...")
            try:
                injected_context = rag.build_injected_context(user_text)
            except Exception as exc:
                logger.warning("RAG 构建上下文失败，降级为无上下文模式: %s", exc)
                callbacks.on_status_update("⚠️ RAG 上下文获取失败，降级为直接对话...")

        callbacks.on_status_update("🧠 正在思考与制定执行计划...")

        agent_loop.process_user_input(user_text, injected_context=injected_context)

        new_messages = agent_loop.messages[before_len:]
        skipped_user = False
        for msg in new_messages:
            role = msg.get("role", "assistant")
            if role == "user" and not skipped_user:
                skipped_user = True
                continue
            db.append_message(
                session_id=session_id,
                role=role,
                content=msg.get("content", ""),
            )
    except Exception as exc:
        logger.exception("Agent 处理异常")
        callbacks.on_error(f"服务端异常: {exc}")
        callbacks.on_complete()


@app.websocket("/ws/chat/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    if not db.session_exists(session_id):
        await websocket.close(code=1008)
        return

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    callbacks = WebSocketAgentCallbacks(queue=queue, event_loop=asyncio.get_running_loop())
    worker_task = None

    async def _drain_queue() -> None:
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except asyncio.CancelledError:
            pass

    drain_task = asyncio.create_task(_drain_queue())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                continue

            if data.get("type") == "approval_response":
                callbacks.set_approval_result(bool(data.get("approved", False)))
                continue

            try:
                payload = IncomingChatPayload(**data)
            except Exception:
                continue

            if worker_task and not worker_task.done():
                await websocket.send_json({"type": "error", "message": "Agent 正在处理中，请等待上一条指令完成"})
                continue

            worker_task = asyncio.create_task(
                asyncio.to_thread(_run_turn_sync, session_id, payload.text.strip(), payload.use_rag, callbacks)
            )

    except WebSocketDisconnect:
        pass
    finally:
        drain_task.cancel()
        if worker_task and not worker_task.done():
            worker_task.cancel()


if __name__ == "__main__":
    uvicorn.run("server_web:app", host="127.0.0.1", port=8000, reload=False)

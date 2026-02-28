from __future__ import annotations

import asyncio
import json
import threading
import traceback
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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
from Client.tools.maya_tools import CreateAndConnectNodeTool, QuerySelectionContextTool, RunCustomPythonTool
from Client.tools.registry import ToolRegistry

PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
DOCS_PATH = PROJECT_ROOT / "Client" / "data" / "maya_cmds_docs.json"

SYSTEM_PROMPT = "你是 Maya 技术助手。优先给出安全、可撤销的操作步骤，并在需要时调用工具。"
APPROVAL_TIMEOUT_SEC = 120  # 前端审批超时秒数


db = ChatDatabase()


class CreateSessionRequest(BaseModel):
    title: str | None = None


class IncomingChatPayload(BaseModel):
    text: str
    use_rag: bool = True


class WebSocketAgentCallbacks(AgentCallbacks):
    """将 AgentLoop 回调桥接到 asyncio.Queue，由 WS 转发给前端。

    高危工具审批时，工作线程通过 ``threading.Event`` 阻塞等待，
    异步侧收到前端的 ``approval_response`` 后设置结果并唤醒。
    """

    def __init__(self, queue: asyncio.Queue[dict[str, Any]], event_loop: asyncio.AbstractEventLoop):
        self._queue = queue
        self._loop = event_loop
        # 审批同步原语 —— 供工作线程阻塞等待
        self._approval_event = threading.Event()
        self._approval_result: bool = False

    def _emit(self, payload: dict[str, Any]) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)

    # ------ 审批结果注入（由异步侧调用） ------
    def set_approval_result(self, approved: bool) -> None:
        """前端回传审批结果后，由异步侧调用此方法唤醒工作线程。"""
        self._approval_result = approved
        self._approval_event.set()

    # ------ AgentCallbacks 实现 ------
    def on_text_chunk(self, text: str) -> None:
        self._emit({"type": "stream", "content": text})

    def on_tool_call(self, tool_name: str, arguments: dict) -> None:
        self._emit({"type": "tool_call", "name": tool_name, "arguments": arguments})

    def on_approval_required(self, tool_name: str, code_preview: str) -> bool:
        # 重置事件，发出审批请求
        self._approval_event.clear()
        self._approval_result = False
        self._emit({
            "type": "approval_required",
            "name": tool_name,
            "preview": code_preview,
        })
        # 工作线程阻塞等待前端回传
        if not self._approval_event.wait(timeout=APPROVAL_TIMEOUT_SEC):
            self._emit({"type": "error", "message": f"审批超时（{APPROVAL_TIMEOUT_SEC}s），已自动拒绝"})
            return False
        return self._approval_result

    def on_tool_result(self, tool_name: str, result: dict) -> None:
        self._emit({"type": "tool_result", "name": tool_name, "result": result})

    def on_error(self, error: str) -> None:
        self._emit({"type": "error", "message": error})

    def on_complete(self) -> None:
        self._emit({"type": "done"})


app = FastAPI(title="DAMaya Agent Web API", version="1.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def home() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="static/index.html 不存在")
    return FileResponse(index_path)


@app.get("/api/sessions")
def get_sessions() -> list[dict[str, Any]]:
    sessions = db.list_sessions()
    return [{"id": s.id, "title": s.title, "created_at": s.created_at} for s in sessions]


@app.post("/api/sessions")
def create_session(req: CreateSessionRequest) -> dict[str, Any]:
    record = db.create_session(req.title)
    return {"id": record.id, "title": record.title, "created_at": record.created_at}


@app.get("/api/sessions/{session_id}/messages")
def get_messages(session_id: str) -> list[dict[str, Any]]:
    if not db.session_exists(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")

    rows = db.get_messages(session_id)
    result: list[dict[str, Any]] = []
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
            except json.JSONDecodeError:
                payload["tool_calls"] = row.tool_calls
        result.append(payload)
    return result


def _build_agent_components(callbacks: AgentCallbacks) -> tuple[AgentLoop, DualTrackRAG]:
    maya_client = MayaSocketClient(
        host=config.maya_host,
        port=config.maya_port,
        timeout=config.maya_socket_timeout,
    )

    registry = ToolRegistry()
    registry.register(QuerySelectionContextTool(maya_client))
    registry.register(RunCustomPythonTool(maya_client))
    registry.register(CreateAndConnectNodeTool(maya_client))

    llm_client = LLMClient(
        api_key=config.dashscope_api_key,
        base_url=config.dashscope_base_url,
        chat_model=config.dashscope_chat_model,
    )

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


def _run_turn_sync(
    session_id: str,
    user_text: str,
    use_rag: bool,
    callbacks: WebSocketAgentCallbacks,
) -> None:
    try:
        db.append_message(session_id=session_id, role="user", content=user_text)
        agent_loop, rag = _build_agent_components(callbacks)

        history = db.get_messages_for_llm(session_id=session_id, max_messages=config.agent_max_history_messages)
        agent_loop._messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

        before_len = len(agent_loop.messages)
        injected_context = rag.build_injected_context(user_text) if use_rag else ""
        agent_loop.process_user_input(user_text, injected_context=injected_context)

        new_messages = agent_loop.messages[before_len:]
        skipped_user = False
        for msg in new_messages:
            role = msg.get("role", "assistant")
            content = msg.get("content", "")

            if role == "user" and not skipped_user:
                skipped_user = True
                continue

            db.append_message(
                session_id=session_id,
                role=role,
                content=content,
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            )

    except Exception as exc:
        callbacks.on_error(f"服务端异常: {exc}")
        db.append_message(
            session_id=session_id,
            role="assistant",
            content=f"服务端异常: {exc}\n{traceback.format_exc()}",
        )
        callbacks.on_complete()


@app.websocket("/ws/chat/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()

    if not db.session_exists(session_id):
        await websocket.send_json({"type": "error", "message": "会话不存在"})
        await websocket.close(code=1008)
        return

    if not config.dashscope_api_key:
        await websocket.send_json({"type": "error", "message": "缺少 DASHSCOPE_API_KEY，请先配置 .env"})
        await websocket.close(code=1011)
        return

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = IncomingChatPayload.model_validate_json(raw)
            except Exception:
                await websocket.send_json({"type": "error", "message": "消息格式错误，应为 JSON 且包含 text 字段"})
                continue

            user_text = payload.text.strip()
            if not user_text:
                await websocket.send_json({"type": "error", "message": "text 不能为空"})
                continue

            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            current_loop = asyncio.get_running_loop()
            callbacks = WebSocketAgentCallbacks(queue=queue, event_loop=current_loop)

            worker = asyncio.create_task(
                asyncio.to_thread(
                    _run_turn_sync,
                    session_id,
                    user_text,
                    payload.use_rag,
                    callbacks,
                )
            )

            # 并行：转发 agent 事件到 WS，同时监听前端可能发来的审批响应
            async def _drain_queue() -> None:
                while True:
                    event = await queue.get()
                    await websocket.send_json(event)
                    if event.get("type") == "done":
                        return

            async def _listen_approval() -> None:
                """在 agent 工作期间监听前端审批回传。"""
                while not worker.done():
                    try:
                        raw_approval = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    except WebSocketDisconnect:
                        return

                    try:
                        msg = json.loads(raw_approval)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("type") == "approval_response":
                        callbacks.set_approval_result(bool(msg.get("approved", False)))

            drain_task = asyncio.create_task(_drain_queue())
            approval_task = asyncio.create_task(_listen_approval())

            # 等待 drain 结束（即收到 done 事件），然后取消 approval 监听
            await drain_task
            approval_task.cancel()
            try:
                await approval_task
            except asyncio.CancelledError:
                pass
            await worker

    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": f"WebSocket 异常: {exc}"})
        await websocket.close(code=1011)


if __name__ == "__main__":
    uvicorn.run("server_web:app", host="127.0.0.1", port=8000, reload=False)

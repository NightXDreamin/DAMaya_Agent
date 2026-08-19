from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from Client.config import config
from Client.core.database import ChatDatabase
from Client.core.graph_agent import AgentCallbacks, GraphAgent
from Client.core.vector_rag import MayaDocsRetriever, build_injected_context
from Client.maya_host.client import MayaSocketClient
from Client.tools.langchain_tools import create_maya_tools, get_dangerous_tool_names

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
UPLOADS_DIR = PROJECT_ROOT / "uploads"
DOCS_PATH = PROJECT_ROOT / "Client" / "data" / "maya_cmds_docs.json"
CHECKPOINT_DB_PATH = PROJECT_ROOT / "maya_agent_graph.db"

UPLOADS_DIR.mkdir(exist_ok=True)

SYSTEM_PROMPT = """你是一个顶级的 Maya Technical Artist 助手。你的核心工作方式是【分析 → 调用工具执行 → 结构化汇报】。

你可以通过 Function Calling 调用已绑定的 Maya 工具来完成任务。
如需查询场景信息，优先使用 run_custom_python 工具执行 Maya Python 代码。

【约束规则】
1. 绝对禁止使用"好的"、"让我看看"等废话口语开场白。
2. 最终结果必须使用 Markdown 结构化汇报，善用 emoji 作为视觉锚点：

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
    model: str | None = None      # 前端可选择模型
    attached_files: list[str] = []  # 已上传文件的 URL 列表


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
        self._emit({
            "type": "tool_call",
            "name": tool_name,
            "arguments": arguments,
            "ts": time.time(),          # ← 新增：工具开始时间戳
        })

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
        self._emit({
            "type": "tool_result",
            "name": tool_name,
            "result": result,
            "ts": time.time(),          # ← 新增：工具结束时间戳
        })

    def on_error(self, error: str) -> None:
        self._emit({"type": "error", "message": error})

    def on_complete(self) -> None:
        self._emit({"type": "done"})


# ── FastAPI App ──────────────────────────────────────────────────────────────
app = FastAPI(title="DAMaya Agent Web API", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
if UPLOADS_DIR.exists():
    app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ── Config API ───────────────────────────────────────────────────────────────
@app.get("/api/config")
def get_config() -> dict[str, Any]:
    """返回 UI 所需配置（不含 API key）。"""
    return config.to_ui_dict()


# ── Upload API ───────────────────────────────────────────────────────────────
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}
ALLOWED_FILE_TYPES = {
    "text/plain", "text/markdown", "application/json",
    "application/pdf", "application/zip",
    *ALLOWED_IMAGE_TYPES,
}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)) -> JSONResponse:
    content_type = file.content_type or "application/octet-stream"
    data = await file.read()

    if len(data) > MAX_FILE_SIZE:
        return JSONResponse({"error": "文件过大，最大支持 20MB"}, status_code=413)

    # 安全文件名
    safe_name = Path(file.filename or "upload").name
    # 加时间戳防重名
    ts_prefix = str(int(time.time() * 1000))
    save_name = f"{ts_prefix}_{safe_name}"
    save_path = UPLOADS_DIR / save_name
    save_path.write_bytes(data)

    is_image = content_type in ALLOWED_IMAGE_TYPES
    return JSONResponse({
        "url": f"/uploads/{save_name}",
        "filename": safe_name,
        "type": "image" if is_image else "file",
        "content_type": content_type,
        "size": len(data),
    })


# ── Sessions API ──────────────────────────────────────────────────────────────
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


# ── Agent Builder ──────────────────────────────────────────────────────────────────────────────
def _build_agent_components(
    callbacks: AgentCallbacks,
    model_override: str | None = None,
) -> tuple[GraphAgent, MayaDocsRetriever, Any]:
    maya_client = MayaSocketClient(
        host=config.maya_host,
        port=config.maya_port,
        timeout=config.maya_socket_timeout,
    )
    tools = create_maya_tools(maya_client, DOCS_PATH)
    dangerous_names = get_dangerous_tool_names(tools)

    agent = GraphAgent(
        api_key=config.dashscope_api_key,
        base_url=config.dashscope_base_url,
        chat_model=model_override or config.dashscope_chat_model,
        tools=tools,
        callbacks=callbacks,
        dangerous_tool_names=dangerous_names,
        system_prompt=SYSTEM_PROMPT,
        max_history_messages=config.agent_max_history_messages,
        max_react_rounds=config.agent_max_react_rounds,
        tool_repeat_limit=config.agent_tool_repeat_limit,
        db_path=CHECKPOINT_DB_PATH,
    )

    # RAG — 新版双轨检索器
    from Client.tools.langchain_tools import _query_selection_context
    import json as _json
    retriever = MayaDocsRetriever(
        docs_path=DOCS_PATH,
        api_key=config.dashscope_api_key,
        base_url=config.dashscope_base_url,
        translate_model=config.dashscope_translate_model,
        embedding_model=config.dashscope_embedding_model,
    )
    scene_provider = lambda: _json.loads(_query_selection_context(maya_client))
    return agent, retriever, scene_provider


async def _run_turn_async(
    session_id: str,
    user_text: str,
    use_rag: bool,
    callbacks: WebSocketAgentCallbacks,
    model_override: str | None = None,
    attached_files: list[str] | None = None,
) -> None:
    try:
        # 将已上传文件注入 prompt 上下文
        file_context = ""
        if attached_files:
            file_lines = ["[Attached Files]"]
            for url in attached_files:
                file_lines.append(f"- {url}")
            file_context = "\n".join(file_lines) + "\n\n"

        full_user_text = user_text if not file_context else f"{file_context}{user_text}"

        db.append_message(session_id=session_id, role="user", content=full_user_text)
        agent, retriever, scene_provider = _build_agent_components(callbacks, model_override)
        history = db.get_messages_for_llm(
            session_id=session_id,
            max_messages=config.agent_max_history_messages,
        )

        injected_context = ""
        if use_rag:
            callbacks.on_status_update("✨ 正在通过 RAG 解析场景上下文与意图...")
            try:
                injected_context = build_injected_context(
                    retriever, full_user_text, scene_context_provider=scene_provider,
                )
            except Exception as exc:
                logger.warning("RAG 构建上下文失败，降级: %s", exc)
                callbacks.on_status_update("⚠️ RAG 上下文获取失败，降级为直接对话...")

        callbacks.on_status_update("🧠 正在思考与制定执行计划...")
        new_messages = await agent.arun(
            session_id=session_id,
            user_text=full_user_text,
            history_messages=history,
            injected_context=injected_context,
        )

        # 持久化新产生的消息
        for msg in new_messages:
            db.append_message(
                session_id=session_id,
                role=msg.get("role", "assistant"),
                content=msg.get("content", ""),
            )
    except Exception as exc:
        logger.exception("Agent 处理异常")
        callbacks.on_error(f"服务端异常: {exc}")
        callbacks.on_complete()


# ── WebSocket ──────────────────────────────────────────────────────────────────
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
                _run_turn_async(
                    session_id,
                    payload.text.strip(),
                    payload.use_rag,
                    callbacks,
                    payload.model,
                    payload.attached_files,
                )
            )

    except WebSocketDisconnect:
        pass
    finally:
        drain_task.cancel()
        if worker_task and not worker_task.done():
            worker_task.cancel()


if __name__ == "__main__":
    uvicorn.run(
        "server_web:app",
        host=config.server_host,
        port=config.server_port,
        reload=False,
    )

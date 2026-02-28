from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionRecord:
    id: str
    title: str
    created_at: str


@dataclass(frozen=True)
class MessageRecord:
    id: int
    session_id: str
    role: str
    content: str
    tool_calls: str | None
    tool_call_id: str | None
    timestamp: str


class ChatDatabase:
    def __init__(self, db_path: Path | str | None = None):
        root = Path(__file__).resolve().parents[2]
        self.db_path = Path(db_path) if db_path else (root / "maya_agent.db")
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session_timestamp
                ON messages(session_id, timestamp);
                """
            )
            # 兼容迁移：为旧表添加 tool_call_id 列（若不存在）
            cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
            if "tool_call_id" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN tool_call_id TEXT")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_session(self, title: str | None = None) -> SessionRecord:
        session_id = str(uuid.uuid4())
        created_at = self._now_iso()
        normalized_title = (title or "新会话").strip() or "新会话"

        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions(id, title, created_at) VALUES (?, ?, ?)",
                (session_id, normalized_title, created_at),
            )

        return SessionRecord(id=session_id, title=normalized_title, created_at=created_at)

    def session_exists(self, session_id: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return bool(row)

    def list_sessions(self) -> list[SessionRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at FROM sessions ORDER BY created_at DESC"
            ).fetchall()

        return [SessionRecord(id=row["id"], title=row["title"], created_at=row["created_at"]) for row in rows]

    def get_messages(self, session_id: str) -> list[MessageRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, role, content, tool_calls, tool_call_id, timestamp
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        return [
            MessageRecord(
                id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                tool_calls=row["tool_calls"],
                tool_call_id=row["tool_call_id"],
                timestamp=row["timestamp"],
            )
            for row in rows
        ]

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | dict[str, Any] | str | None = None,
        tool_call_id: str | None = None,
    ) -> MessageRecord:
        if role not in {"user", "assistant", "system", "tool"}:
            raise ValueError(f"非法 role: {role}")

        timestamp = self._now_iso()
        normalized_content = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

        serialized_tool_calls: str | None
        if tool_calls is None:
            serialized_tool_calls = None
        elif isinstance(tool_calls, str):
            serialized_tool_calls = tool_calls
        else:
            serialized_tool_calls = json.dumps(tool_calls, ensure_ascii=False)

        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO messages(session_id, role, content, tool_calls, tool_call_id, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, normalized_content, serialized_tool_calls, tool_call_id, timestamp),
            )
            message_id = int(cursor.lastrowid)

        return MessageRecord(
            id=message_id,
            session_id=session_id,
            role=role,
            content=normalized_content,
            tool_calls=serialized_tool_calls,
            tool_call_id=tool_call_id,
            timestamp=timestamp,
        )

    def get_messages_for_llm(self, session_id: str, max_messages: int = 20) -> list[dict[str, Any]]:
        rows = self.get_messages(session_id)
        llm_messages: list[dict[str, Any]] = []

        for row in rows:
            if row.role == "tool":
                item: dict[str, Any] = {"role": "tool", "content": row.content}
                if row.tool_call_id:
                    item["tool_call_id"] = row.tool_call_id
                llm_messages.append(item)
                continue

            if row.role not in {"user", "assistant"}:
                continue

            item = {"role": row.role, "content": row.content}
            if row.role == "assistant" and row.tool_calls:
                try:
                    item["tool_calls"] = json.loads(row.tool_calls)
                except json.JSONDecodeError:
                    pass
            llm_messages.append(item)

        if max_messages > 0 and len(llm_messages) > max_messages:
            llm_messages = llm_messages[-max_messages:]

        return llm_messages

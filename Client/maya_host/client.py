from __future__ import annotations

import base64
import json
import re
import socket
import time
from dataclasses import dataclass
from typing import Optional

START_MARKER = "MCP_JSON_START"
END_MARKER = "MCP_JSON_END"


@dataclass
class ExecutionResult:
    success: bool
    result: Optional[object]
    stdout: Optional[str]
    error: Optional[str]
    execution_time: float
    raw_output: str = ""


class MayaSocketClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 7022, timeout: float = 30.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: Optional[socket.socket] = None

    def connect(self) -> bool:
        if self.is_connected():
            return True

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(self.timeout)
        self._socket.connect((self.host, self.port))
        return True

    def disconnect(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def is_connected(self) -> bool:
        return self._socket is not None

    def execute_code(self, code: str, timeout: Optional[float] = None) -> ExecutionResult:
        start_ts = time.time()
        try:
            if not self.is_connected():
                self.connect()

            if self._socket is None:
                raise RuntimeError("Socket not connected")

            if timeout is not None:
                self._socket.settimeout(timeout)

            payload = self._build_payload(code)
            self._socket.sendall(payload.encode("utf-8"))

            output = self._recv_until_result(timeout=timeout or self.timeout)
            extracted = self._extract_json(output)
            if extracted is None:
                return ExecutionResult(
                    success=False,
                    result=None,
                    stdout=None,
                    error="无法从 Maya 返回中提取结构化 JSON。",
                    execution_time=time.time() - start_ts,
                    raw_output=output,
                )

            return ExecutionResult(
                success=bool(extracted.get("success", False)),
                result=extracted.get("result"),
                stdout=extracted.get("stdout"),
                error=extracted.get("error"),
                execution_time=time.time() - start_ts,
                raw_output=output,
            )
        except Exception as exc:
            return ExecutionResult(
                success=False,
                result=None,
                stdout=None,
                error=str(exc),
                execution_time=time.time() - start_ts,
            )
        finally:
            if self._socket is not None:
                self._socket.settimeout(self.timeout)

    @staticmethod
    def _build_payload(code: str) -> str:
        encoded = base64.b64encode(code.encode("utf-8")).decode("utf-8")
        return (
            "try:\n"
            f"    __damaya_exec(\"{encoded}\")\n"
            "except NameError:\n"
            "    try:\n"
            "        import Modules.server as _damaya_server\n"
            f"        _damaya_server.__damaya_exec(\"{encoded}\")\n"
            "    except Exception:\n"
            "        import server as _damaya_server\n"
            f"        _damaya_server.__damaya_exec(\"{encoded}\")\n"
        )

    def _recv_until_result(self, timeout: float) -> str:
        if self._socket is None:
            raise RuntimeError("Socket not connected")

        deadline = time.time() + timeout
        chunks: list[str] = []

        while time.time() < deadline:
            try:
                data = self._socket.recv(4096)
                if not data:
                    break

                chunks.append(data.decode("utf-8", errors="replace"))
                merged = "".join(chunks)
                if START_MARKER in merged and END_MARKER in merged:
                    return merged
            except socket.timeout:
                continue

        return "".join(chunks)

    @staticmethod
    def _extract_json(raw_output: str) -> Optional[dict]:
        pattern = rf"{START_MARKER}(.*?){END_MARKER}"
        match = re.search(pattern, raw_output, flags=re.DOTALL)
        if not match:
            return None

        payload = match.group(1).strip()
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

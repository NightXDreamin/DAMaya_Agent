from __future__ import annotations

import json
import socket
import struct
import traceback
from typing import Any

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024


class MayaSocketClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 17022,
        timeout: float = DEFAULT_TIMEOUT,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.max_payload_bytes = max_payload_bytes

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        return None

    def is_connected(self) -> bool:
        return True

    def execute_code(self, code: str, timeout: float | None = None) -> dict[str, Any]:
        request = {"code": code}
        effective_timeout = self.timeout if timeout is None else timeout

        try:
            response = self._send_request(request, timeout=float(effective_timeout))
            if not isinstance(response, dict):
                raise ValueError("Maya 返回格式错误：不是 JSON object")

            return {
                "success": bool(response.get("success", False)),
                "result": response.get("result"),
                "stdout": response.get("stdout", ""),
                "traceback": response.get("traceback"),
                "error": response.get("error"),
            }
        except Exception as exc:
            return {
                "success": False,
                "result": None,
                "stdout": "",
                "traceback": traceback.format_exc(),
                "error": str(exc),
            }

    def _send_request(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        raw_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(raw_payload) > self.max_payload_bytes:
            raise ValueError(f"请求体过大: {len(raw_payload)} bytes")

        with socket.create_connection((self.host, self.port), timeout=timeout) as conn:
            conn.settimeout(timeout)
            conn.sendall(struct.pack("!I", len(raw_payload)) + raw_payload)

            header = self._recv_exact(conn, 4)
            body_len = struct.unpack("!I", header)[0]
            if body_len <= 0 or body_len > self.max_payload_bytes:
                raise ValueError(f"非法响应体长度: {body_len}")

            body = self._recv_exact(conn, body_len)
            return json.loads(body.decode("utf-8"))

    @staticmethod
    def _recv_exact(conn: socket.socket, nbytes: int) -> bytes:
        data = bytearray()
        while len(data) < nbytes:
            chunk = conn.recv(nbytes - len(data))
            if not chunk:
                raise ConnectionError("连接已关闭，数据接收不完整")
            data.extend(chunk)
        return bytes(data)

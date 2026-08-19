from __future__ import annotations

import io
import json
import socket
import struct
import threading
import traceback
from contextlib import redirect_stdout
from typing import Any

import maya.cmds as cmds
import maya.utils as maya_utils

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17022
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024


class _ServerState:
    def __init__(self) -> None:
        self.server_socket: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.host = DEFAULT_HOST
        self.port = DEFAULT_PORT
        self.read_timeout = DEFAULT_READ_TIMEOUT
        self.max_payload_bytes = DEFAULT_MAX_PAYLOAD_BYTES


_STATE = _ServerState()


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def _execute_code_mainthread(code: str) -> dict[str, Any]:
    stdout_buffer = io.StringIO()
    result: Any = None
    tb_text: str | None = None
    error_text: str | None = None

    cmds.undoInfo(openChunk=True)
    try:
        with redirect_stdout(stdout_buffer):
            local_scope = {"cmds": cmds}
            exec(code, {"cmds": cmds, "__builtins__": __builtins__}, local_scope)
            result = local_scope.get("result")
    except Exception as exc:
        error_text = str(exc)
        tb_text = traceback.format_exc()
    finally:
        cmds.undoInfo(closeChunk=True)

    return {
        "success": tb_text is None,
        "result": _to_jsonable(result),
        "stdout": stdout_buffer.getvalue(),
        "traceback": tb_text,
        "error": error_text,
    }


def _recv_exact(conn: socket.socket, nbytes: int) -> bytes:
    data = bytearray()
    while len(data) < nbytes:
        chunk = conn.recv(nbytes - len(data))
        if not chunk:
            raise ConnectionError("连接已关闭，数据未接收完整。")
        data.extend(chunk)
    return bytes(data)


def _recv_message(conn: socket.socket, max_payload_bytes: int) -> dict[str, Any]:
    header = _recv_exact(conn, 4)
    length = struct.unpack("!I", header)[0]
    if length <= 0 or length > max_payload_bytes:
        raise ValueError(f"非法请求体长度: {length}")

    payload = _recv_exact(conn, length)
    obj = json.loads(payload.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("请求格式错误：必须是 JSON object。")
    return obj


def _send_message(conn: socket.socket, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    conn.sendall(struct.pack("!I", len(raw)) + raw)


def _handle_client(conn: socket.socket) -> None:
    with conn:
        conn.settimeout(_STATE.read_timeout)
        try:
            request = _recv_message(conn, _STATE.max_payload_bytes)
            code = request.get("code")
            if not isinstance(code, str):
                raise ValueError("字段 code 缺失或不是字符串。")

            response = maya_utils.executeInMainThreadWithResult(_execute_code_mainthread, code)
            if not isinstance(response, dict):
                response = {
                    "success": False,
                    "result": None,
                    "stdout": "",
                    "traceback": None,
                    "error": "主线程返回了无效结果类型。",
                }
            _send_message(conn, response)
        except Exception as exc:
            _send_message(
                conn,
                {
                    "success": False,
                    "result": None,
                    "stdout": "",
                    "traceback": traceback.format_exc(),
                    "error": str(exc),
                },
            )


def _accept_loop() -> None:
    server = _STATE.server_socket
    if server is None:
        return

    while not _STATE.stop_event.is_set():
        try:
            conn, _addr = server.accept()
        except socket.timeout:
            continue
        except OSError:
            break

        threading.Thread(target=_handle_client, args=(conn,), daemon=True).start()


def start_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> int:
    with _STATE.lock:
        if _STATE.thread is not None and _STATE.thread.is_alive():
            if _STATE.port == port and _STATE.host == host:
                return _STATE.port
            stop_server()

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(16)
        server.settimeout(0.5)

        _STATE.server_socket = server
        _STATE.host = host
        _STATE.port = port
        _STATE.read_timeout = float(read_timeout)
        _STATE.max_payload_bytes = int(max_payload_bytes)
        _STATE.stop_event.clear()

        _STATE.thread = threading.Thread(target=_accept_loop, name="DAMayaPluginSocketServer", daemon=True)
        _STATE.thread.start()
        print(f"DAMaya plugin socket server started at {host}:{port}")
        return port


def stop_server() -> None:
    with _STATE.lock:
        _STATE.stop_event.set()

        if _STATE.server_socket is not None:
            try:
                _STATE.server_socket.close()
            finally:
                _STATE.server_socket = None

        thread = _STATE.thread
        _STATE.thread = None

    if thread is not None and thread.is_alive():
        thread.join(timeout=1.0)
    print("DAMaya plugin socket server stopped")

import base64
import io
import json
import traceback
from contextlib import redirect_stdout

import maya.cmds as cmds

START_MARKER = "MCP_JSON_START"
END_MARKER = "MCP_JSON_END"
DEFAULT_PORT = 7022


def _safe_print_json(payload: dict) -> None:
    print(f"{START_MARKER}{json.dumps(payload, ensure_ascii=False)}{END_MARKER}")


def __damaya_exec(encoded_code: str):
    code = base64.b64decode(encoded_code.encode("utf-8")).decode("utf-8")

    stdout_buffer = io.StringIO()
    result = None
    error = None

    cmds.undoInfo(openChunk=True)
    try:
        with redirect_stdout(stdout_buffer):
            local_scope = {"cmds": cmds}
            exec(code, globals(), local_scope)
            result = local_scope.get("result")
    except Exception:
        error = traceback.format_exc()
    finally:
        cmds.undoInfo(closeChunk=True)

    payload = {
        "success": error is None,
        "result": result,
        "stdout": stdout_buffer.getvalue(),
        "error": error,
    }
    _safe_print_json(payload)
    return payload


def start_server(port: int = DEFAULT_PORT) -> int:
    endpoint = f":{port}"
    if cmds.commandPort(endpoint, q=True):
        cmds.commandPort(name=endpoint, close=True)

    cmds.commandPort(name=endpoint, sourceType="python", echoOutput=True)
    print(f"DAMaya commandPort server started at {endpoint}")
    return port


def stop_server(port: int = DEFAULT_PORT) -> None:
    endpoint = f":{port}"
    if cmds.commandPort(endpoint, q=True):
        cmds.commandPort(name=endpoint, close=True)
        print(f"DAMaya commandPort server stopped: {endpoint}")

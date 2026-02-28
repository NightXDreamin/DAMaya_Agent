from __future__ import annotations

import json
from typing import Any

from Client.maya_host.client import MayaSocketClient
from Client.tools.registry import BaseTool


class QuerySelectionContextTool(BaseTool):
    name = "query_selection_context"
    description = "获取当前 Maya 选择集合的上下文信息（数量、对象清单、首个对象类型）。"
    is_dangerous = False

    def __init__(self, maya_client: MayaSocketClient):
        self.maya_client = maya_client

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

    def execute(self, **kwargs) -> dict:
        code = """
import maya.cmds as cmds
selection = cmds.ls(selection=True, long=True) or []
first_type = cmds.nodeType(selection[0]) if selection else None
result = {
    'selection_count': len(selection),
    'objects': selection,
    'first_type': first_type,
}
""".strip()
        res = self.maya_client.execute_code(code)
        if not res.success:
            return {"success": False, "error": res.error, "stdout": res.stdout}

        parsed = _parse_json_or_text(res.result)
        if isinstance(parsed, dict):
            return {"success": True, "data": parsed, "stdout": res.stdout}
        return {"success": True, "data": {"raw": res.result}, "stdout": res.stdout}


class RunCustomPythonTool(BaseTool):
    name = "run_custom_python"
    description = "执行自定义 Python 代码（高危操作）。"
    is_dangerous = True

    def __init__(self, maya_client: MayaSocketClient):
        self.maya_client = maya_client

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "python_code": {"type": "string", "description": "要在 Maya 中执行的 Python 代码"}
                    },
                    "required": ["python_code"],
                },
            },
        }

    def execute(self, **kwargs) -> dict:
        python_code = kwargs.get("python_code", "")
        res = self.maya_client.execute_code(python_code)
        return {
            "success": res.success,
            "result": res.result,
            "stdout": res.stdout,
            "error": res.error,
        }


class CreateAndConnectNodeTool(BaseTool):
    name = "create_and_connect_node"
    description = "创建 Maya 节点并连接属性（高危操作）。"
    is_dangerous = True

    def __init__(self, maya_client: MayaSocketClient):
        self.maya_client = maya_client

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node_type": {"type": "string", "description": "节点类型，如 multiplyDivide"},
                        "node_name": {"type": "string", "description": "节点名（可选）"},
                        "connect_from": {"type": "string", "description": "源属性，如 pCube1.tx"},
                        "connect_to": {"type": "string", "description": "目标属性，如 pCube2.ty"},
                    },
                    "required": ["node_type"],
                },
            },
        }

    def execute(self, **kwargs) -> dict:
        node_type = kwargs.get("node_type", "")
        node_name = kwargs.get("node_name", "")
        connect_from = kwargs.get("connect_from", "")
        connect_to = kwargs.get("connect_to", "")

        lines = [
            "import maya.cmds as cmds",
            f"created = cmds.createNode({json.dumps(node_type)}, name={json.dumps(node_name)}) if {bool(node_name)} else cmds.createNode({json.dumps(node_type)})",
        ]
        if connect_from and connect_to:
            lines.append(
                f"cmds.connectAttr({json.dumps(connect_from)}, {json.dumps(connect_to)}, force=True)"
            )
        lines.append("result = {'created_node': created, 'connected': bool('" + connect_from + "' and '" + connect_to + "')} ")

        code = "\n".join(lines)
        res = self.maya_client.execute_code(code)
        parsed = _parse_json_or_text(res.result)
        return {
            "success": res.success,
            "result": parsed,
            "stdout": res.stdout,
            "error": res.error,
        }


def _parse_json_or_text(text: Any) -> Any:
    if text is None:
        return None
    if not isinstance(text, str):
        return text

    trimmed = text.strip()
    if not trimmed:
        return ""

    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        return text

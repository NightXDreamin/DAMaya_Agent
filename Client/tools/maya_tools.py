from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from Client.maya_host.client import MayaSocketClient
from Client.tools.registry import BaseTool


_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


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
                "parameters": {"type": "object", "properties": {}, "required": []},
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
        return _execute_with_data(self.maya_client, code)


class GetMayaDocsTool(BaseTool):
    name = "get_maya_docs"
    description = "查询 Maya Python 命令文档。"
    is_dangerous = False

    def __init__(self, docs_path: Path):
        self._docs = self._load_docs(docs_path)

    @staticmethod
    def _load_docs(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Maya 命令名，如 polyCube"},
                    },
                    "required": ["command"],
                },
            },
        }

    def execute(self, **kwargs) -> dict:
        cmd = str(kwargs.get("command", "")).strip()
        if not cmd:
            return _error_result("command 不能为空")
        
        # 模糊匹配
        result = self._docs.get(cmd)
        if not result:
            lowered = cmd.lower()
            for key, val in self._docs.items():
                if key.lower() == lowered:
                    result = val
                    break
        
        if result:
            return {"success": True, "result": result}
        return {"success": False, "result": None, "error": f"未找到命令文档: {cmd}"}


class GetSceneContextTool(QuerySelectionContextTool):
    """
    兼容性工具：Agent 有时会产生幻觉调用 get_scene_context，
    这里将其映射为 query_selection_context 的功能。
    """
    name = "get_scene_context"
    description = "获取场景上下文（当前默认返回选择集信息）。"



class RunCustomPythonTool(BaseTool):
    name = "run_custom_python"
    description = "在 Maya 中执行任意自定义 Python 代码（这是最通用的工具，任何 Maya 操作都可以通过此工具完成）。将 Python 代码作为 python_code 参数传入即可。"
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
                    "properties": {
                        "python_code": {"type": "string", "description": "要在 Maya 中执行的 Python 代码"}
                    },
                    "required": ["python_code"],
                },
            },
        }

    def execute(self, **kwargs) -> dict:
        python_code = str(kwargs.get("python_code", ""))
        if not python_code.strip():
            return _error_result("python_code 不能为空")
        return _execute_with_result(self.maya_client, python_code)


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
        node_type = str(kwargs.get("node_type", "")).strip()
        node_name = str(kwargs.get("node_name", "")).strip()
        connect_from = str(kwargs.get("connect_from", "")).strip()
        connect_to = str(kwargs.get("connect_to", "")).strip()
        if not node_type:
            return _error_result("node_type 不能为空")

        lines = [
            "import maya.cmds as cmds",
            f"created = cmds.createNode({json.dumps(node_type)}, name={json.dumps(node_name)}) if {bool(node_name)} else cmds.createNode({json.dumps(node_type)})",
        ]
        if connect_from and connect_to:
            lines.append(f"cmds.connectAttr({json.dumps(connect_from)}, {json.dumps(connect_to)}, force=True)")
        lines.append("result = {'created_node': created, 'connected': bool('" + connect_from + "' and '" + connect_to + "')}")
        return _execute_with_result(self.maya_client, "\n".join(lines))


class TransformTool(BaseTool):
    name = "transform_node"
    description = "修改物体位移/旋转/缩放（高危操作）。"
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
                        "node": {"type": "string", "description": "目标节点名"},
                        "translate": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                            "description": "平移 [tx, ty, tz]",
                        },
                        "rotate": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                            "description": "旋转 [rx, ry, rz]",
                        },
                        "scale": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                            "description": "缩放 [sx, sy, sz]",
                        },
                        "space": {
                            "type": "string",
                            "enum": ["world", "object"],
                            "description": "坐标空间，默认 object",
                        },
                    },
                    "required": ["node"],
                },
            },
        }

    def execute(self, **kwargs) -> dict:
        node = str(kwargs.get("node", "")).strip()
        translate = kwargs.get("translate")
        rotate = kwargs.get("rotate")
        scale = kwargs.get("scale")
        space = str(kwargs.get("space", "object")).strip().lower() or "object"

        if not node:
            return _error_result("node 不能为空")
        if not any(v is not None for v in (translate, rotate, scale)):
            return _error_result("translate/rotate/scale 至少提供一个")
        if space not in {"world", "object"}:
            return _error_result("space 必须是 world 或 object")

        for field_name, value in (("translate", translate), ("rotate", rotate), ("scale", scale)):
            if value is None:
                continue
            if not isinstance(value, list) or len(value) != 3:
                return _error_result(f"{field_name} 必须是长度为 3 的数组")

        world_flag = "True" if space == "world" else "False"
        lines = [
            "import maya.cmds as cmds",
            f"node = {json.dumps(node)}",
            "if not cmds.objExists(node):",
            "    raise RuntimeError(f'节点不存在: {node}')",
        ]
        if translate is not None:
            lines.append(f"cmds.xform(node, translation={json.dumps(translate)}, worldSpace={world_flag}, objectSpace={str(space == 'object')})")
        if rotate is not None:
            lines.append(f"cmds.xform(node, rotation={json.dumps(rotate)}, worldSpace={world_flag}, objectSpace={str(space == 'object')})")
        if scale is not None:
            lines.append(f"cmds.xform(node, scale={json.dumps(scale)}, worldSpace={world_flag}, objectSpace={str(space == 'object')})")
        lines.append(
            "result = {'node': node, 'translate': cmds.xform(node, q=True, translation=True, worldSpace=True), 'rotate': cmds.xform(node, q=True, rotation=True, worldSpace=True), 'scale': cmds.xform(node, q=True, scale=True, relative=False)}"
        )
        return _execute_with_result(self.maya_client, "\n".join(lines))


class GetSetAttributeTool(BaseTool):
    name = "get_set_attribute"
    description = "读取或强制覆盖节点属性值（写入为高危操作）。"
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
                        "node": {"type": "string", "description": "节点名"},
                        "attribute": {"type": "string", "description": "属性名（不含节点前缀）"},
                        "mode": {
                            "type": "string",
                            "enum": ["get", "set"],
                            "description": "get 读取；set 写入",
                        },
                        "value": {
                            "description": "set 模式写入值，可为 number/string/bool/array",
                            "anyOf": [
                                {"type": "number"},
                                {"type": "string"},
                                {"type": "boolean"},
                                {"type": "array", "items": {"type": "number"}},
                            ],
                        },
                        "force": {
                            "type": "boolean",
                            "description": "set 模式是否强制解锁后写入，默认 true",
                        },
                    },
                    "required": ["node", "attribute", "mode"],
                },
            },
        }

    def execute(self, **kwargs) -> dict:
        node = str(kwargs.get("node", "")).strip()
        attr = str(kwargs.get("attribute", "")).strip()
        mode = str(kwargs.get("mode", "")).strip().lower()
        value = kwargs.get("value")
        force = bool(kwargs.get("force", True))

        if not node or not attr:
            return _error_result("node 与 attribute 不能为空")
        if mode not in {"get", "set"}:
            return _error_result("mode 必须是 get 或 set")
        if mode == "set" and value is None:
            return _error_result("set 模式必须提供 value")

        lines = [
            "import maya.cmds as cmds",
            f"node = {json.dumps(node)}",
            f"attr = {json.dumps(attr)}",
            "plug = f'{node}.{attr}'",
            "if not cmds.objExists(node):",
            "    raise RuntimeError(f'节点不存在: {node}')",
            "if not cmds.objExists(plug):",
            "    raise RuntimeError(f'属性不存在: {plug}')",
        ]

        if mode == "get":
            lines.append("result = {'mode': 'get', 'plug': plug, 'value': cmds.getAttr(plug)}")
            return _execute_with_result(self.maya_client, "\n".join(lines))

        lines.append(f"force = {json.dumps(force)}")
        lines.append("if force:")
        lines.append("    try:")
        lines.append("        cmds.setAttr(plug, lock=False)")
        lines.append("    except Exception:")
        lines.append("        pass")
        try:
            lines.extend(_build_set_attr_lines(value))
        except ValueError as exc:
            return _error_result(str(exc))
        lines.append("result = {'mode': 'set', 'plug': plug, 'value': cmds.getAttr(plug)}")
        return _execute_with_result(self.maya_client, "\n".join(lines))



class ConstraintTool(BaseTool):
    name = "create_constraint"
    description = "创建 Parent/Point/Orient 约束（高危操作）。"
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
                        "constraint_type": {
                            "type": "string",
                            "enum": ["parent", "point", "orient"],
                            "description": "约束类型",
                        },
                        "driver": {"type": "string", "description": "驱动对象"},
                        "driven": {"type": "string", "description": "被驱动对象"},
                        "maintain_offset": {
                            "type": "boolean",
                            "description": "是否保持偏移，默认 true",
                        },
                    },
                    "required": ["constraint_type", "driver", "driven"],
                },
            },
        }

    def execute(self, **kwargs) -> dict:
        constraint_type = str(kwargs.get("constraint_type", "")).strip().lower()
        driver = str(kwargs.get("driver", "")).strip()
        driven = str(kwargs.get("driven", "")).strip()
        maintain_offset = bool(kwargs.get("maintain_offset", True))

        if constraint_type not in {"parent", "point", "orient"}:
            return _error_result("constraint_type 仅支持 parent/point/orient")
        if not driver or not driven:
            return _error_result("driver 与 driven 不能为空")

        lines = [
            "import maya.cmds as cmds",
            f"driver = {json.dumps(driver)}",
            f"driven = {json.dumps(driven)}",
            "for n in (driver, driven):",
            "    if not cmds.objExists(n):",
            "        raise RuntimeError(f'节点不存在: {n}')",
            f"constraint_type = {json.dumps(constraint_type)}",
            f"mo = {json.dumps(maintain_offset)}",
            "mapping = {'parent': cmds.parentConstraint, 'point': cmds.pointConstraint, 'orient': cmds.orientConstraint}",
            "fn = mapping.get(constraint_type)",
            "if fn is None:",
            "    raise RuntimeError(f'不支持约束类型: {constraint_type}')",
            "created = fn(driver, driven, mo=mo)",
            "result = {'constraint': created[0] if created else None, 'constraint_type': constraint_type, 'driver': driver, 'driven': driven, 'maintain_offset': mo}",
        ]
        return _execute_with_result(self.maya_client, "\n".join(lines))


class ExecuteSkillTool(BaseTool):
    name = "execute_skill"
    description = "执行本地预置 Skill 脚本。"
    is_dangerous = True

    def __init__(self, maya_client: MayaSocketClient, skills_dir: Path | None = None):
        self.maya_client = maya_client
        self.skills_dir = skills_dir or _SKILLS_DIR
        self._skills = self._scan_skills()

    def _scan_skills(self) -> dict[str, Path]:
        if not self.skills_dir.exists():
            return {}
        result: dict[str, Path] = {}
        for path in sorted(self.skills_dir.glob("*.py")):
            if path.name == "__init__.py":
                continue
            result[path.stem] = path
        return result

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "enum": sorted(self._skills.keys()),
                            "description": "要执行的技能名（来源于 Client/tools/skills/*.py）",
                        }
                    },
                    "required": ["skill_name"],
                },
            },
        }

    def execute(self, **kwargs) -> dict:
        skill_name = str(kwargs.get("skill_name", "")).strip()
        skill_path = self._skills.get(skill_name)
        if not skill_path:
            return _error_result(f"未知 skill_name: {skill_name}，可用项: {sorted(self._skills.keys())}")

        try:
            code = skill_path.read_text(encoding="utf-8")
        except Exception as exc:
            return _error_result(f"读取技能脚本失败: {exc}")

        return _execute_with_result(self.maya_client, code, extra={"skill_name": skill_name, "skill_path": str(skill_path)})


def _build_set_attr_lines(value: Any) -> list[str]:
    if isinstance(value, bool):
        return [f"cmds.setAttr(plug, {json.dumps(value)})"]
    if isinstance(value, (int, float)):
        return [f"cmds.setAttr(plug, {json.dumps(value)})"]
    if isinstance(value, str):
        return [f"cmds.setAttr(plug, {json.dumps(value)}, type='string')"]
    if isinstance(value, list):
        if len(value) in (2, 3, 4) and all(isinstance(i, (int, float)) for i in value):
            return [f"cmds.setAttr(plug, *{json.dumps(value)}, type='double{len(value)}')"]
        raise ValueError("数组 value 仅支持 2~4 个数值")
    raise ValueError("value 类型不支持，仅支持 number/string/bool/number-array")


def _execute_with_data(maya_client: MayaSocketClient, code: str) -> dict:
    res = maya_client.execute_code(code)
    if not res.get("success"):
        return {
            "success": False,
            "error": res.get("error"),
            "traceback": res.get("traceback"),
            "stdout": res.get("stdout"),
            "result": None,
        }
    parsed = _parse_json_or_text(res.get("result"))
    return {
        "success": True,
        "result": parsed if parsed is not None else {},
        "traceback": res.get("traceback"),
        "stdout": res.get("stdout"),
        "error": None,
    }


def _execute_with_result(maya_client: MayaSocketClient, code: str, extra: dict[str, Any] | None = None) -> dict:
    try:
        res = maya_client.execute_code(code)
    except Exception as exc:
        return _error_result(str(exc))

    payload = {
        "success": bool(res.get("success", False)),
        "result": _parse_json_or_text(res.get("result")),
        "stdout": res.get("stdout"),
        "traceback": res.get("traceback"),
        "error": res.get("error"),
    }
    if extra:
        if isinstance(payload.get("result"), dict):
            payload["result"].update(extra)
        else:
            payload["meta"] = extra
    return payload


def _error_result(message: str) -> dict:
    return {
        "success": False,
        "result": None,
        "stdout": None,
        "traceback": None,
        "error": message,
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

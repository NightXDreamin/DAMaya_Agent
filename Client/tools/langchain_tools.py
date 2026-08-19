"""LangChain StructuredTool 定义 — 将 9 个 Maya 工具迁移为标准 LangChain 工具。

底层执行逻辑复用 MayaSocketClient，保持与原 maya_tools.py 相同的行为。
通过 metadata["is_dangerous"] 标记高危工具，供 LangGraph 审批节点消费。
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from Client.maya_host.client import MayaSocketClient

# ---------------------------------------------------------------------------
# 公共执行辅助（与 maya_tools.py 保持一致）
# ---------------------------------------------------------------------------
_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _error_result(message: str) -> dict:
    return {"success": False, "result": None, "stdout": None, "traceback": None, "error": message}


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


def _execute_with_result(
    maya_client: MayaSocketClient,
    code: str,
    extra: dict[str, Any] | None = None,
) -> dict:
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


# ---------------------------------------------------------------------------
# Pydantic Input Schema 定义
# ---------------------------------------------------------------------------

class RunCustomPythonInput(BaseModel):
    python_code: str = Field(description="要在 Maya 中执行的 Python 代码")


class GetMayaDocsInput(BaseModel):
    command: str = Field(description="Maya 命令名，如 polyCube")


class CreateAndConnectNodeInput(BaseModel):
    node_type: str = Field(description="节点类型，如 multiplyDivide")
    node_name: str = Field(default="", description="节点名（可选）")
    connect_from: str = Field(default="", description="源属性，如 pCube1.tx")
    connect_to: str = Field(default="", description="目标属性，如 pCube2.ty")


class TransformNodeInput(BaseModel):
    node: str = Field(description="目标节点名")
    translate: Optional[list[float]] = Field(default=None, description="平移 [tx, ty, tz]")
    rotate: Optional[list[float]] = Field(default=None, description="旋转 [rx, ry, rz]")
    scale: Optional[list[float]] = Field(default=None, description="缩放 [sx, sy, sz]")
    space: str = Field(default="object", description="坐标空间: world 或 object")


class GetSetAttributeInput(BaseModel):
    node: str = Field(description="节点名")
    attribute: str = Field(description="属性名（不含节点前缀）")
    mode: str = Field(description="get 读取；set 写入")
    value: Optional[Any] = Field(default=None, description="set 模式写入值")
    force: bool = Field(default=True, description="set 模式是否强制解锁后写入")


class ConstraintInput(BaseModel):
    constraint_type: str = Field(description="约束类型: parent/point/orient")
    driver: str = Field(description="驱动对象")
    driven: str = Field(description="被驱动对象")
    maintain_offset: bool = Field(default=True, description="是否保持偏移")


class ExecuteSkillInput(BaseModel):
    skill_name: str = Field(description="要执行的技能名（来源于 Client/tools/skills/*.py）")


# ---------------------------------------------------------------------------
# 工具执行函数
# ---------------------------------------------------------------------------

def _query_selection_context(maya_client: MayaSocketClient) -> str:
    code = (
        "import maya.cmds as cmds\n"
        "selection = cmds.ls(selection=True, long=True) or []\n"
        "first_type = cmds.nodeType(selection[0]) if selection else None\n"
        "result = {\n"
        "    'selection_count': len(selection),\n"
        "    'objects': selection,\n"
        "    'first_type': first_type,\n"
        "}"
    )
    return json.dumps(_execute_with_data(maya_client, code), ensure_ascii=False)


def _get_maya_docs(docs: dict[str, Any], command: str) -> str:
    cmd = command.strip()
    if not cmd:
        return json.dumps(_error_result("command 不能为空"), ensure_ascii=False)

    result = docs.get(cmd)
    if not result:
        lowered = cmd.lower()
        for key, val in docs.items():
            if key.lower() == lowered:
                result = val
                break

    if result:
        return json.dumps({"success": True, "result": result}, ensure_ascii=False)
    return json.dumps({"success": False, "result": None, "error": f"未找到命令文档: {cmd}"}, ensure_ascii=False)


def _run_custom_python(maya_client: MayaSocketClient, python_code: str) -> str:
    if not python_code.strip():
        return json.dumps(_error_result("python_code 不能为空"), ensure_ascii=False)
    return json.dumps(_execute_with_result(maya_client, python_code), ensure_ascii=False)


def _create_and_connect_node(
    maya_client: MayaSocketClient,
    node_type: str,
    node_name: str = "",
    connect_from: str = "",
    connect_to: str = "",
) -> str:
    node_type = node_type.strip()
    node_name = node_name.strip()
    connect_from = connect_from.strip()
    connect_to = connect_to.strip()
    if not node_type:
        return json.dumps(_error_result("node_type 不能为空"), ensure_ascii=False)

    lines = [
        "import maya.cmds as cmds",
        f"created = cmds.createNode({json.dumps(node_type)}, name={json.dumps(node_name)}) if {bool(node_name)} else cmds.createNode({json.dumps(node_type)})",
    ]
    if connect_from and connect_to:
        lines.append(f"cmds.connectAttr({json.dumps(connect_from)}, {json.dumps(connect_to)}, force=True)")
    lines.append(
        "result = {'created_node': created, 'connected': bool('"
        + connect_from + "' and '" + connect_to + "')}"
    )
    return json.dumps(_execute_with_result(maya_client, "\n".join(lines)), ensure_ascii=False)


def _transform_node(
    maya_client: MayaSocketClient,
    node: str,
    translate: list[float] | None = None,
    rotate: list[float] | None = None,
    scale: list[float] | None = None,
    space: str = "object",
) -> str:
    node = node.strip()
    space = space.strip().lower() or "object"
    if not node:
        return json.dumps(_error_result("node 不能为空"), ensure_ascii=False)
    if not any(v is not None for v in (translate, rotate, scale)):
        return json.dumps(_error_result("translate/rotate/scale 至少提供一个"), ensure_ascii=False)
    if space not in {"world", "object"}:
        return json.dumps(_error_result("space 必须是 world 或 object"), ensure_ascii=False)

    for field_name, value in (("translate", translate), ("rotate", rotate), ("scale", scale)):
        if value is None:
            continue
        if not isinstance(value, list) or len(value) != 3:
            return json.dumps(_error_result(f"{field_name} 必须是长度为 3 的数组"), ensure_ascii=False)

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
        "result = {'node': node, 'translate': cmds.xform(node, q=True, translation=True, worldSpace=True), "
        "'rotate': cmds.xform(node, q=True, rotation=True, worldSpace=True), "
        "'scale': cmds.xform(node, q=True, scale=True, relative=False)}"
    )
    return json.dumps(_execute_with_result(maya_client, "\n".join(lines)), ensure_ascii=False)


def _get_set_attribute(
    maya_client: MayaSocketClient,
    node: str,
    attribute: str,
    mode: str,
    value: Any = None,
    force: bool = True,
) -> str:
    node = node.strip()
    attribute = attribute.strip()
    mode = mode.strip().lower()

    if not node or not attribute:
        return json.dumps(_error_result("node 与 attribute 不能为空"), ensure_ascii=False)
    if mode not in {"get", "set"}:
        return json.dumps(_error_result("mode 必须是 get 或 set"), ensure_ascii=False)
    if mode == "set" and value is None:
        return json.dumps(_error_result("set 模式必须提供 value"), ensure_ascii=False)

    lines = [
        "import maya.cmds as cmds",
        f"node = {json.dumps(node)}",
        f"attr = {json.dumps(attribute)}",
        "plug = f'{node}.{attr}'",
        "if not cmds.objExists(node):",
        "    raise RuntimeError(f'节点不存在: {node}')",
        "if not cmds.objExists(plug):",
        "    raise RuntimeError(f'属性不存在: {plug}')",
    ]

    if mode == "get":
        lines.append("result = {'mode': 'get', 'plug': plug, 'value': cmds.getAttr(plug)}")
        return json.dumps(_execute_with_result(maya_client, "\n".join(lines)), ensure_ascii=False)

    lines.append(f"force = {json.dumps(force)}")
    lines.append("if force:")
    lines.append("    try:")
    lines.append("        cmds.setAttr(plug, lock=False)")
    lines.append("    except Exception:")
    lines.append("        pass")
    try:
        lines.extend(_build_set_attr_lines(value))
    except ValueError as exc:
        return json.dumps(_error_result(str(exc)), ensure_ascii=False)
    lines.append("result = {'mode': 'set', 'plug': plug, 'value': cmds.getAttr(plug)}")
    return json.dumps(_execute_with_result(maya_client, "\n".join(lines)), ensure_ascii=False)


def _create_constraint(
    maya_client: MayaSocketClient,
    constraint_type: str,
    driver: str,
    driven: str,
    maintain_offset: bool = True,
) -> str:
    constraint_type = constraint_type.strip().lower()
    driver = driver.strip()
    driven = driven.strip()

    if constraint_type not in {"parent", "point", "orient"}:
        return json.dumps(_error_result("constraint_type 仅支持 parent/point/orient"), ensure_ascii=False)
    if not driver or not driven:
        return json.dumps(_error_result("driver 与 driven 不能为空"), ensure_ascii=False)

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
        "result = {'constraint': created[0] if created else None, 'constraint_type': constraint_type, "
        "'driver': driver, 'driven': driven, 'maintain_offset': mo}",
    ]
    return json.dumps(_execute_with_result(maya_client, "\n".join(lines)), ensure_ascii=False)


def _execute_skill(
    maya_client: MayaSocketClient,
    skills: dict[str, Path],
    skill_name: str,
) -> str:
    skill_name = skill_name.strip()
    skill_path = skills.get(skill_name)
    if not skill_path:
        return json.dumps(
            _error_result(f"未知 skill_name: {skill_name}，可用项: {sorted(skills.keys())}"),
            ensure_ascii=False,
        )
    try:
        code = skill_path.read_text(encoding="utf-8")
    except Exception as exc:
        return json.dumps(_error_result(f"读取技能脚本失败: {exc}"), ensure_ascii=False)

    return json.dumps(
        _execute_with_result(maya_client, code, extra={"skill_name": skill_name, "skill_path": str(skill_path)}),
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# 工具工厂函数
# ---------------------------------------------------------------------------

def _scan_skills(skills_dir: Path) -> dict[str, Path]:
    if not skills_dir.exists():
        return {}
    result: dict[str, Path] = {}
    for path in sorted(skills_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        result[path.stem] = path
    return result


def create_maya_tools(maya_client: MayaSocketClient, docs_path: Path) -> list[StructuredTool]:
    """创建并返回所有 LangChain 格式的 Maya 工具列表。"""

    # 加载文档
    docs: dict[str, Any] = {}
    if docs_path.exists():
        with docs_path.open("r", encoding="utf-8") as f:
            docs = json.load(f)

    # 扫描技能
    skills = _scan_skills(_SKILLS_DIR)

    tools: list[StructuredTool] = []

    # ── 安全工具 ──────────────────────────────────────────────────────

    tools.append(StructuredTool.from_function(
        func=lambda: _query_selection_context(maya_client),
        name="query_selection_context",
        description="获取当前 Maya 选择集合的上下文信息（数量、对象清单、首个对象类型）。",
    ))

    tools.append(StructuredTool.from_function(
        func=lambda: _query_selection_context(maya_client),
        name="get_scene_context",
        description="获取场景上下文（当前默认返回选择集信息）。",
    ))

    tools.append(StructuredTool.from_function(
        func=lambda command: _get_maya_docs(docs, command),
        name="get_maya_docs",
        description="查询 Maya Python 命令文档。",
        args_schema=GetMayaDocsInput,
    ))

    tools.append(StructuredTool.from_function(
        func=lambda python_code: _run_custom_python(maya_client, python_code),
        name="run_custom_python",
        description="在 Maya 中执行任意自定义 Python 代码（最通用工具）。将 Python 代码作为 python_code 参数传入。",
        args_schema=RunCustomPythonInput,
        metadata={"is_dangerous": True},
    ))

    # ── 高危工具 ──────────────────────────────────────────────────────

    tools.append(StructuredTool.from_function(
        func=lambda node_type, node_name="", connect_from="", connect_to="": _create_and_connect_node(
            maya_client, node_type, node_name, connect_from, connect_to,
        ),
        name="create_and_connect_node",
        description="创建 Maya 节点并连接属性（高危操作）。",
        args_schema=CreateAndConnectNodeInput,
        metadata={"is_dangerous": True},
    ))

    tools.append(StructuredTool.from_function(
        func=lambda node, translate=None, rotate=None, scale=None, space="object": _transform_node(
            maya_client, node, translate, rotate, scale, space,
        ),
        name="transform_node",
        description="修改物体位移/旋转/缩放（高危操作）。",
        args_schema=TransformNodeInput,
        metadata={"is_dangerous": True},
    ))

    tools.append(StructuredTool.from_function(
        func=lambda node, attribute, mode, value=None, force=True: _get_set_attribute(
            maya_client, node, attribute, mode, value, force,
        ),
        name="get_set_attribute",
        description="读取或强制覆盖节点属性值（写入为高危操作）。",
        args_schema=GetSetAttributeInput,
        metadata={"is_dangerous": True},
    ))

    tools.append(StructuredTool.from_function(
        func=lambda constraint_type, driver, driven, maintain_offset=True: _create_constraint(
            maya_client, constraint_type, driver, driven, maintain_offset,
        ),
        name="create_constraint",
        description="创建 Parent/Point/Orient 约束（高危操作）。",
        args_schema=ConstraintInput,
        metadata={"is_dangerous": True},
    ))

    tools.append(StructuredTool.from_function(
        func=lambda skill_name: _execute_skill(maya_client, skills, skill_name),
        name="execute_skill",
        description=f"执行本地预置 Skill 脚本（高危操作）。可用技能: {sorted(skills.keys())}",
        args_schema=ExecuteSkillInput,
        metadata={"is_dangerous": True},
    ))

    return tools


def get_dangerous_tool_names(tools: list[StructuredTool]) -> set[str]:
    """返回所有标记为高危的工具名称集合。"""
    return {
        t.name
        for t in tools
        if t.metadata and t.metadata.get("is_dangerous")
    }

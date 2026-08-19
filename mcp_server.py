"""
DAMaya MCP Server
使用 MCP (Model Context Protocol) 将 Maya 工具暴露给 IDE（Cursor、Claude Desktop、Codebuddy 等）

【配置方法】
Cursor: 在 .cursor/mcp.json (或全局 ~/AppData/Roaming/Cursor/User/mcp.json) 中添加：
{
  "mcpServers": {
    "damaya": {
      "command": "python",
      "args": ["C:/path/to/DAMaya_Agent/mcp_server.py"]
    }
  }
}

Claude Desktop: 在 claude_desktop_config.json 中添加同样格式。

启动方式（Claude/Cursor 会自动通过 stdio 启动）：
  python mcp_server.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from Client.config import config
from Client.maya_host.client import MayaSocketClient

# ── 初始化 ────────────────────────────────────────────────────────
mcp = FastMCP(
    name="DAMaya Agent",
    instructions=(
        "你正在通过 MCP 协议与运行于 Autodesk Maya 中的 DAMaya Agent 通信。\n"
        "所有 maya_* 工具都会在 Maya 中直接执行。\n"
        "执行高危操作前请先用 maya_get_scene_info 了解场景状态。"
    ),
)

def _get_maya_client() -> MayaSocketClient:
    return MayaSocketClient(
        host=config.maya_host,
        port=config.maya_port,
        timeout=config.maya_socket_timeout,
    )


def _run_python(code: str) -> dict:
    """封装 Maya Python 执行，统一错误格式。"""
    client = _get_maya_client()
    try:
        return client.execute_code(code)
    except ConnectionRefusedError:
        return {
            "success": False, "result": None, "stdout": "",
            "error": f"无法连接 Maya（{config.maya_host}:{config.maya_port}）。"
                     "请确认 Maya 已运行且 DAMaya 插件服务器已启动。",
        }
    except Exception as e:
        return {"success": False, "result": None, "stdout": "", "error": str(e)}


# ── MCP Tools ────────────────────────────────────────────────────

@mcp.tool()
def maya_run_python(python_code: str) -> str:
    """
    在 Maya 中执行任意 Python 代码。
    将需要返回的数据赋值给变量 `result`，它会自动被捕获并返回。

    示例：
      import maya.cmds as cmds
      result = cmds.ls(type='mesh')
    """
    res = _run_python(python_code)
    output = []
    if res.get("stdout"):
        output.append(f"[stdout]\n{res['stdout']}")
    if res.get("success"):
        output.append(f"[result]\n{json.dumps(res.get('result'), ensure_ascii=False, indent=2)}")
    else:
        err = res.get("error") or "Unknown error"
        tb  = res.get("traceback") or ""
        output.append(f"[error] {err}")
        if tb:
            output.append(f"[traceback]\n{tb}")
    return "\n".join(output) if output else "(no output)"


@mcp.tool()
def maya_get_scene_info() -> str:
    """
    获取 Maya 场景概览信息：
    - 当前选中对象
    - 场景中所有 mesh/transform 节点（前 50 个）
    - 当前帧范围与时间
    """
    code = """
import maya.cmds as cmds
selection = cmds.ls(sl=True, long=True) or []
meshes = cmds.ls(type='mesh', long=True) or []
transforms = cmds.ls(type='transform', long=True) or []
timeline_start = cmds.playbackOptions(q=True, min=True)
timeline_end   = cmds.playbackOptions(q=True, max=True)
current_frame  = cmds.currentTime(q=True)
result = {
    "selection": selection,
    "selection_count": len(selection),
    "mesh_nodes": meshes[:50],
    "mesh_count": len(meshes),
    "transform_count": len(transforms),
    "timeline": {"start": timeline_start, "end": timeline_end, "current": current_frame},
}
""".strip()
    res = _run_python(code)
    if res.get("success"):
        return json.dumps(res.get("result"), ensure_ascii=False, indent=2)
    return f"[error] {res.get('error')}"


@mcp.tool()
def maya_get_selection() -> str:
    """获取当前 Maya 选中对象的详细信息（名称、类型、变换值）。"""
    code = """
import maya.cmds as cmds
sel = cmds.ls(sl=True, long=True) or []
details = []
for obj in sel:
    try:
        t = cmds.xform(obj, q=True, t=True, ws=True)
        r = cmds.xform(obj, q=True, ro=True, ws=True)
        s = cmds.xform(obj, q=True, s=True, r=True)
        details.append({
            "name": obj,
            "type": cmds.nodeType(obj),
            "translate": t,
            "rotate": r,
            "scale": s,
        })
    except Exception as e:
        details.append({"name": obj, "type": cmds.nodeType(obj), "error": str(e)})
result = {"count": len(sel), "objects": details}
""".strip()
    res = _run_python(code)
    if res.get("success"):
        return json.dumps(res.get("result"), ensure_ascii=False, indent=2)
    return f"[error] {res.get('error')}"


@mcp.tool()
def maya_set_transform(
    node: str,
    translate: list[float] | None = None,
    rotate: list[float] | None = None,
    scale: list[float] | None = None,
    world_space: bool = True,
) -> str:
    """
    修改 Maya 节点的位移、旋转、缩放。
    每个参数均为 [x, y, z] 格式，未提供则不修改。
    """
    lines = ["import maya.cmds as cmds", f"node = {json.dumps(node)}",
             "if not cmds.objExists(node): raise RuntimeError(f'节点不存在: {node}')"]
    ws = "True" if world_space else "False"
    os = "False" if world_space else "True"
    if translate:
        lines.append(f"cmds.xform(node, t={json.dumps(translate)}, ws={ws}, os={os})")
    if rotate:
        lines.append(f"cmds.xform(node, ro={json.dumps(rotate)}, ws={ws}, os={os})")
    if scale:
        lines.append(f"cmds.xform(node, s={json.dumps(scale)}, r=True)")
    lines.append("result = {'node': node, "
                 "'translate': cmds.xform(node, q=True, t=True, ws=True), "
                 "'rotate': cmds.xform(node, q=True, ro=True, ws=True), "
                 "'scale': cmds.xform(node, q=True, s=True, r=True)}")
    res = _run_python("\n".join(lines))
    if res.get("success"):
        return json.dumps(res.get("result"), ensure_ascii=False, indent=2)
    return f"[error] {res.get('error')}\n{res.get('traceback', '')}"


@mcp.tool()
def maya_get_attribute(node: str, attribute: str) -> str:
    """读取 Maya 节点属性值。例如：node='pCube1', attribute='translateX'"""
    code = f"""
import maya.cmds as cmds
plug = '{node}.{attribute}'
if not cmds.objExists(plug):
    raise RuntimeError(f'属性不存在: {{plug}}')
result = {{'plug': plug, 'value': cmds.getAttr(plug)}}
""".strip()
    res = _run_python(code)
    if res.get("success"):
        return json.dumps(res.get("result"), ensure_ascii=False, indent=2)
    return f"[error] {res.get('error')}"


@mcp.tool()
def maya_set_attribute(node: str, attribute: str, value: float | str | bool) -> str:
    """设置 Maya 节点属性值。例如：node='pCube1', attribute='translateX', value=5.0"""
    val_repr = json.dumps(value)
    code = f"""
import maya.cmds as cmds
plug = '{node}.{attribute}'
if not cmds.objExists('{node}'):
    raise RuntimeError('节点不存在: {node}')
if not cmds.objExists(plug):
    raise RuntimeError(f'属性不存在: {{plug}}')
cmds.setAttr(plug, {val_repr})
result = {{'plug': plug, 'new_value': cmds.getAttr(plug)}}
""".strip()
    res = _run_python(code)
    if res.get("success"):
        return json.dumps(res.get("result"), ensure_ascii=False, indent=2)
    return f"[error] {res.get('error')}"


@mcp.tool()
def maya_list_attributes(node: str) -> str:
    """列出 Maya 节点的所有可读属性及当前值。"""
    code = f"""
import maya.cmds as cmds
node = '{node}'
if not cmds.objExists(node):
    raise RuntimeError(f'节点不存在: {{node}}')
attrs = cmds.listAttr(node, scalar=True, settable=True) or []
out = {{}}
for a in attrs[:60]:
    try:
        out[a] = cmds.getAttr(f'{{node}}.{{a}}')
    except Exception:
        out[a] = '<unreadable>'
result = {{'node': node, 'attributes': out}}
""".strip()
    res = _run_python(code)
    if res.get("success"):
        return json.dumps(res.get("result"), ensure_ascii=False, indent=2)
    return f"[error] {res.get('error')}"


@mcp.tool()
def maya_get_docs(command: str) -> str:
    """查询 Maya Python 命令文档。例如：command='polyCube'"""
    import json as _json
    docs_path = PROJECT_ROOT / "Client" / "data" / "maya_cmds_docs.json"
    if not docs_path.exists():
        return f"[error] 文档文件不存在: {docs_path}"
    with open(docs_path, encoding="utf-8") as f:
        docs = _json.load(f)
    result = docs.get(command) or docs.get(command.lower())
    if result:
        return _json.dumps(result, ensure_ascii=False, indent=2)
    # 模糊搜索
    matches = [k for k in docs if command.lower() in k.lower()][:10]
    return f"未找到精确匹配 '{command}'。相似命令：{matches}"


@mcp.tool()
def damaya_read_file(relative_path: str) -> str:
    """
    读取 DAMaya Agent 项目内的源代码文件。
    路径相对于项目根目录。例如：'Client/core/agent_loop.py'
    用于 IDE 开发时理解项目结构。
    """
    target = PROJECT_ROOT / relative_path
    if not target.exists():
        return f"[error] 文件不存在: {target}"
    if not target.resolve().is_relative_to(PROJECT_ROOT):
        return "[error] 禁止访问项目根目录以外的文件"
    try:
        content = target.read_text(encoding="utf-8")
        lines = content.splitlines()
        # 超过 300 行时截断并提示
        if len(lines) > 300:
            content = "\n".join(lines[:300]) + f"\n\n... [截断，共 {len(lines)} 行，请指定行范围]"
        return content
    except Exception as e:
        return f"[error] {e}"


@mcp.tool()
def damaya_list_project_files(subdirectory: str = "") -> str:
    """
    列出 DAMaya Agent 项目的文件结构。
    subdirectory 为空则列出根目录，否则列出指定子目录（如 'Client/core'）。
    """
    target = PROJECT_ROOT / subdirectory if subdirectory else PROJECT_ROOT
    if not target.exists() or not target.is_dir():
        return f"[error] 目录不存在: {target}"

    IGNORE = {".git", "__pycache__", ".venv", "venv", "node_modules", "uploads", ".codebuddy"}
    result = []
    for item in sorted(target.rglob("*")):
        if any(part in IGNORE for part in item.parts):
            continue
        rel = item.relative_to(PROJECT_ROOT)
        prefix = "📁 " if item.is_dir() else "📄 "
        size = f"  ({item.stat().st_size:,} B)" if item.is_file() else ""
        result.append(f"{prefix}{rel}{size}")
        if len(result) > 200:
            result.append("... (文件过多，已截断)")
            break
    return "\n".join(result)


@mcp.tool()
def damaya_get_config() -> str:
    """获取当前 DAMaya Agent 的运行配置（不含 API Key）。"""
    return json.dumps(config.to_ui_dict(), ensure_ascii=False, indent=2)


# ── 入口 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[DAMaya MCP] 启动中 (Maya: {config.maya_host}:{config.maya_port})", file=sys.stderr)
    mcp.run(transport="stdio")

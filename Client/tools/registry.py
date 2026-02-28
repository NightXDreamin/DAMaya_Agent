from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    name: str
    description: str
    is_dangerous: bool = False

    @abstractmethod
    def get_schema(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def execute(self, **kwargs) -> dict:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> BaseTool:
        if name not in self._tools:
            raise KeyError(f"未注册工具: {name}")
        return self._tools[name]

    def get_all_schemas(self) -> list[dict]:
        return [tool.get_schema() for tool in self._tools.values()]

    def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict:
        tool = self.get_tool(name)
        return tool.execute(**arguments)

    def is_dangerous_tool(self, name: str) -> bool:
        tool = self._tools.get(name)
        return bool(tool and tool.is_dangerous)


def register_default_maya_tools(registry: ToolRegistry, maya_client: Any) -> ToolRegistry:
    from Client.tools.maya_tools import (
        ConstraintTool,
        CreateAndConnectNodeTool,
        ExecuteSkillTool,
        GetMayaDocsTool,
        GetSceneContextTool,
        GetSetAttributeTool,
        QuerySelectionContextTool,
        RunCustomPythonTool,
        TransformTool,
    )

    registry.register(QuerySelectionContextTool(maya_client))
    registry.register(GetSceneContextTool(maya_client))
    
    # Docs tool
    from pathlib import Path
    project_root = Path(__file__).resolve().parent.parent.parent
    docs_path = project_root / "Client" / "data" / "maya_cmds_docs.json"
    registry.register(GetMayaDocsTool(docs_path))

    registry.register(RunCustomPythonTool(maya_client))
    registry.register(CreateAndConnectNodeTool(maya_client))
    registry.register(TransformTool(maya_client))
    registry.register(GetSetAttributeTool(maya_client))
    registry.register(ConstraintTool(maya_client))
    registry.register(ExecuteSkillTool(maya_client))
    return registry

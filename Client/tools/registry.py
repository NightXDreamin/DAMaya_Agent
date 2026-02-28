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

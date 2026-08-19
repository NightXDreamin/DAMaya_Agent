"""Maya LangChain 结构化工具测试 (TDD)。"""
import json
import pytest
from pathlib import Path
from Client.tools.langchain_tools import get_dangerous_tool_names, create_maya_tools

def test_dangerous_tool_names():
    """验证高危工具名单是否被正确提取。"""
    # mock client
    class DummyClient:
        def execute_code(self, code: str) -> dict:
            return {"success": True, "result": None}

    tools = create_maya_tools(DummyClient(), docs_path=Path("dummy_path.json"))
    dangerous = get_dangerous_tool_names(tools)
    
    # 根据我们预期的硬编码，检查几个必在里面的高危工具
    assert "transform_node" in dangerous
    assert "get_set_attribute" in dangerous
    assert "create_and_connect_node" in dangerous
    assert "create_constraint" in dangerous
    assert "execute_skill" in dangerous
    
    # 万能 python 执行工具也是高危
    assert "run_custom_python" in dangerous
    
    # 普通查询工具不应该在高危里
    assert "query_selection_context" not in dangerous
    assert "get_maya_docs" not in dangerous

def test_tool_serialization_boolean():
    """测试工具在序列化为 Maya Python 参数时，尤其是 Boolean / Array 类型是否合法。"""
    class SpyClient:
        def __init__(self):
            self.last_code = ""
        def execute_code(self, code: str) -> dict:
            self.last_code = code
            return {"success": True, "result": json.dumps({"status": "ok"})}

    client = SpyClient()
    tools = create_maya_tools(client, docs_path=Path("dummy_path.json"))
    
    # 找到 transform 工具
    transform_tool = next(t for t in tools if t.name == "transform_node")
    
    # 触发调用，传入一些参数
    transform_tool.invoke({
        "node": "pCube1",
        "translate": [1.0, 2.0, 3.0],
        "space": "world"
    })
    
    code = client.last_code
    # 验证生成的 python 代码片段
    assert "cmds.xform" in code
    assert '"pCube1"' in code
    assert "worldSpace=True" in code # 验证坐标系序列化
    assert "translation=[1.0, 2.0, 3.0]" in code # 验证列表序列化

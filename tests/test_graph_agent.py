"""GraphAgent 与万向状态机层级单测 (TDD)。"""
import pytest
from Client.core.graph_agent import _RepeatDetector

def test_repeat_detector():
    """测试防死循环滑动窗口机制。"""
    detector = _RepeatDetector(limit=3)
    
    # 前两次相同的调用不算死循环
    assert detector.check("test_tool", {"arg": 1}) is False
    assert detector.check("test_tool", {"arg": 1}) is False
    
    # 第三次相同调用触发阈值
    assert detector.check("test_tool", {"arg": 1}) is True
    
    # 只要参数或者工具名变了，窗口就会打破
    assert detector.check("test_tool", {"arg": 2}) is False
    assert detector.check("test_tool", {"arg": 2}) is False
    assert detector.check("other_tool", {"arg": 2}) is False

@pytest.mark.asyncio
async def test_agent_graph_initial_state():
    """测试 GraphAgent 的图编译和基础初始化。"""
    from Client.core.graph_agent import GraphAgent
    from Client.tools.langchain_tools import create_maya_tools
    
    # 只需要 Dummy Callbacks 即可
    class DummyCallbacks:
        def on_text_chunk(self, text: str) -> None: pass
        def on_think_chunk(self, text: str) -> None: pass
        def on_status_update(self, content: str) -> None: pass
        def on_tool_call(self, tool_name: str, arguments: dict) -> None: pass
        def on_approval_required(self, tool_name: str, code_preview: str) -> bool: return True
        def on_tool_result(self, tool_name: str, result: dict) -> None: pass
        def on_error(self, error: str) -> None: pass
        def on_complete(self) -> None: pass

    # 可以用一个空的 tools 列表或者 dummy
    agent = GraphAgent(
        api_key="test",
        base_url="http://test",
        chat_model="test-model",
        tools=[],
        callbacks=DummyCallbacks(),
        dangerous_tool_names=set(),
        system_prompt="Test Prompt",
        db_path=None # 内存模式
    )
    
    graph = await agent._build_graph()
    assert graph is not None
    
    # 验证内存数据库初始化成功并可以查询
    state = await graph.aget_state({"configurable": {"thread_id": "test_1"}})
    assert state.values == {} # 初始为空

@pytest.mark.asyncio
async def test_dangerous_tool_rejection(mocker):
    """测试高危工具被拒绝执行时的图状态转换。"""
    from Client.core.graph_agent import GraphAgent
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    
    # 构建一个 mock 工具
    def mock_dangerous(arg: str) -> str:
        """这个工具很危险。"""
        return "executed"
    dangerous_tool = StructuredTool.from_function(mock_dangerous)
    
    # 模拟用户拒绝 (on_approval_required 返回 False)
    class RejectCallbacks:
        def on_text_chunk(self, text: str) -> None: pass
        def on_think_chunk(self, text: str) -> None: pass
        def on_status_update(self, content: str) -> None: pass
        def on_tool_call(self, tool_name: str, arguments: dict) -> None: pass
        def on_approval_required(self, tool_name: str, code_preview: str) -> bool: return False
        def on_tool_result(self, tool_name: str, result: dict) -> None: pass
        def on_error(self, error: str) -> None: pass
        def on_complete(self) -> None: pass
        
    agent = GraphAgent(
        api_key="test",
        base_url="http://test",
        chat_model="test-model",
        tools=[dangerous_tool],
        callbacks=RejectCallbacks(),
        dangerous_tool_names={"mock_dangerous"}, # 设置为高危
        system_prompt="Test Prompt",
        db_path=None
    )
    
    # 直接调用 _tool_node 发送伪造的 AIMessage 带有工具调用
    # 绕过 LLM 直接测试 tool_node
    tool_call = {"name": "mock_dangerous", "args": {"arg": "test"}, "id": "call_123"}
    state = {"messages": [AIMessage(content="", tool_calls=[tool_call])]}
    
    # 执行 tool_node
    result = await agent._tool_node(state)
    messages = result.get("messages", [])
    
    # 必须产生一个 ToolMessage 而且内容包含拒绝
    assert len(messages) == 1
    assert "拒绝" in messages[0].content
    assert messages[0].name == "mock_dangerous"

@pytest.mark.asyncio
async def test_graph_recursion_limit(mocker):
    """测试死循环跳出（非单一工具重复，而是整体图迭代超限）。"""
    from Client.core.graph_agent import GraphAgent
    from langchain_core.messages import AIMessage, ToolMessage
    from langgraph.errors import GraphRecursionError
    
    agent = GraphAgent(
        api_key="test", base_url="test", chat_model="test", tools=[],
        callbacks=None, dangerous_tool_names=set(), system_prompt="", db_path=None
    )
    
    # 劫持 LLM，让它永远想要调用一个工具
    import asyncio
    class FakeLLM:
        def bind_tools(self, tools): return self
        async def ainvoke(self, state, config=None):
            return AIMessage(content="", tool_calls=[{"name": "fake_tool", "args": {}, "id": "1"}])
            
    agent._llm = FakeLLM()
    
    # 劫持 Tool node，让它总是成功返回，诱导下一个 LLM 轮次
    async def fake_tool_node(state):
        return {"messages": [ToolMessage(content="ok", name="fake_tool", tool_call_id="1")]}
    agent._tool_node = fake_tool_node
    
    # 构建图
    graph = await agent._build_graph()
    
    # 执行并期待 GraphRecursionError，配置极小的 recursion_limit
    with pytest.raises(GraphRecursionError):
        config = {"configurable": {"thread_id": "test_recursion"}, "recursion_limit": 3}
        async for _ in graph.astream({"messages": [("user", "start")]}, config=config, stream_mode="values"):
            pass


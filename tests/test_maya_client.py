"""Maya API Client 层级单测 (TDD)。"""
import pytest
from Client.maya_host.client import MayaSocketClient

def test_client_initialization():
    """测试客户端的基本初始化参数。"""
    client = MayaSocketClient("localhost", 1234, 5.0)
    assert client.host == "localhost"
    assert client.port == 1234
    assert client.timeout == 5.0

def test_execute_code_wrap(mock_maya_host):
    """测试发送 Python 代码时，是否被正确构建了 request dict。"""
    code = "import maya.cmds as cmds\ncmds.polyCube()"
    mock_maya_host.mock_host.set_next_response({"success": True, "result": "pCube1"})
    
    result = mock_maya_host.execute_code(code)
    
    sent = mock_maya_host.mock_host.sent_payloads[0]
    # 我们期望它向 Maya 发送了 JSON 封装并且包含 code 字段
    assert '"code":' in sent
    assert "cmds.polyCube" in sent
    assert result["success"] is True
    assert result["result"] == "pCube1"

def test_maya_disconnect_recovery(mock_maya_host):
    """测试如果是断开连接的状态，在发送指令时能否得到对应的包络结果。"""
    mock_maya_host.mock_host.is_connected = False
    
    result = mock_maya_host.execute_code("print('hello')")
        
    assert result["success"] is False
    assert "Mock Socket is disconnected" in result["error"]

def test_maya_bad_json_response(mock_maya_host):
    """模拟返回非法的响应类型，测试容错。"""
    # 让底层返回一个不带 dict 的 string
    mock_maya_host.mock_host.set_next_response("bad_json")
    
    result = mock_maya_host.execute_code("print('hello')")
    
    assert result["success"] is False
    assert "不是 JSON object" in result["error"]

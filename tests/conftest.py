"""测试脚手架配置与公共 Mock。"""
import json
import pytest
from typing import Any

from Client.maya_host.client import MayaSocketClient

class MockMayaHost:
    """模拟 Maya 通信主机的响应能力。
    
    允许在测试中注入期望的响应字符串、异常，或者校验发出的 payload。
    """
    def __init__(self):
        self.sent_payloads: list[str] = []
        self.next_response: str = '{"success": true, "data": null}'
        self.next_error: Exception | None = None
        self.is_connected: bool = True

    def mock_send(self, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        self.sent_payloads.append(json.dumps(payload))
        if not self.is_connected:
            raise ConnectionError("Mock Socket is disconnected")
        if self.next_error:
            err = self.next_error
            self.next_error = None
            raise err
            
        resp = json.loads(self.next_response)
        self.next_response = '{"success": true, "result": null}' # 消费后重置为默认
        return resp
        
    def set_next_response(self, data: Any):
        """便利语法：直接将 dict 转成 json 字符串并在下一次返回。"""
        self.next_response = json.dumps(data, ensure_ascii=False)

@pytest.fixture
def mock_maya_host(mocker):
    """提供一个拦截了底层 Socket _send_request 的 MayaClient 实例。"""
    host = MockMayaHost()
    
    # 真实 Client 实例，但其网络行为被 Mock
    client = MayaSocketClient(host="127.0.0.1", port=17022, timeout=2.0)
    
    # 我们拦截 _send_request 方法本身以避免真实网络 IO
    mocker.patch.object(client, "_send_request", side_effect=host.mock_send)
    
    # 挂载 host 到 client 方便测试时读取状态
    client.mock_host = host
    
    return client

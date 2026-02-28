# DAMaya_Agent

Maya AI Agent：
- `Client/`：外部 Agent（LLM、RAG、工具系统、PySide6 UI）
- `Modules/`：Maya 宿主端 socket 服务
- `server_web.py`：V1.1 Web 服务（FastAPI + SQLite + WebSocket）

## 快速开始

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 配置环境变量

填写 `.env` 中的 `DASHSCOPE_API_KEY`、`MAYA_HOST`、`MAYA_PORT`。

3. 在 Maya 中启动宿主服务（示例：17022）

```python
import Modules.plugin_server as ps
ps.stop_server()
ps.start_server(port=17022)
```

## V1.0 桌面端（PySide6）

```bash
python -m Client.run
```

## V1.1 Web 服务（FastAPI）

启动后端：

```bash
python server_web.py
```

默认地址：`http://127.0.0.1:8000`

- 静态调试页：`/`
- 会话列表：`GET /api/sessions`
- 创建会话：`POST /api/sessions`
- 会话消息：`GET /api/sessions/{session_id}/messages`
- 聊天流：`WS /ws/chat/{session_id}`

WebSocket 发送示例：

```json
{"text":"帮我查询当前选择对象", "use_rag": true}
```

WebSocket 接收事件类型：
- `stream`：流式文本
- `tool_call`：工具调用
- `tool_result`：工具执行结果
- `approval_required`：高危工具审批占位事件（当前默认拒绝）
- `error`：错误
- `done`：本轮完成

## 关键模块

- `Client/core/database.py`：SQLite 会话/消息持久化
- `server_web.py`：REST + WebSocket 编排层
- `Client/core/agent_loop.py`：状态机 Agent Loop、工具调用、重复调用阻断
- `Client/maya_host/client.py`：Maya socket 客户端
- `Client/tools/`：工具注册表与 Maya 工具实现

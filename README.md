# DAMaya_Agent

Maya AI Agent V1.0（双进程架构）：
- `Client/`：外部 Agent（LLM、RAG、工具系统、PySide6 UI）
- `Modules/`：Maya 宿主端 commandPort 服务

## 快速开始

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 配置环境变量

```bash
copy .env.example .env
```

填写 `DASHSCOPE_API_KEY` 等配置。

3. 在 Maya 中启动宿主服务

```python
from Modules.server import start_server
start_server(port=7022)
```

4. 启动客户端

```bash
python -m Client.run
```

## 关键模块

- `Modules/server.py`：`commandPort` 服务、Undo Chunk 包裹、结构化 JSON 返回
- `Client/maya_host/client.py`：Socket 客户端、边界标记解析
- `Client/core/agent_loop.py`：状态机 Agent Loop、工具调用、重复调用阻断
- `Client/core/rag.py`：双轨 RAG（场景上下文 + 术语翻译/文档匹配）
- `Client/tools/`：工具注册表与 Maya 工具实现
- `Client/ui/`：PySide6 界面与高危审批流程

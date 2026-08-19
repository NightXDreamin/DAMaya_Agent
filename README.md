# DAMaya Agent — Maya 智能化协作助手

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Maya 2022+](https://img.shields.io/badge/Maya-2022+-orange.svg)](https://www.autodesk.com/products/maya/overview)
[![License MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

DAMaya Agent 是专为 Autodesk Maya 打造的智能化技术美术（Technical Artist）助手。它基于大语言模型（LLM）、LangGraph 与 RAG（检索增强生成）技术，能理解自然语言指令，并在 Maya 中自主执行场景操作、Python 脚本编写与调试。

采用 **Client–Server** 架构：通过自研 TCP Socket（4 字节长度头 + JSON）连接外部 Agent 进程与 Maya 宿主，实现实时双向通信。

---

## ✨ 核心特性

- **🤖 自然语言交互**：直接使用中文指令控制 Maya。
- **🧠 LangGraph Agent**：基于 LangGraph 的异步 ReAct 循环（`agent → tool → agent`），`AsyncSqliteSaver` checkpointer 维护跨轮次对话状态。
- **🛠️ 工具集（Function Calling）**：9 个结构化 LangChain 工具（`create_maya_tools`），含场景感知、属性读写、约束创建、预置 Skill 执行等，并区分「安全 / 高危」。
- **📚 双轨 RAG**：关键词轨（LLM 意图翻译 + 精确/模糊匹配）+ 向量轨（FAISS 语义检索）；`faiss`/`numpy` 为可选依赖，缺失时自动降级为纯关键词检索。
- **⚡ Web UI**：深色主题、流式输出、折叠式时间线、附件上传、模型切换。
- **🛡️ 安全机制**：高危操作审批（`is_dangerous` 工具触发前端确认卡片）、重复调用阻断、容错重试。

---

## 🏗️ 架构概览

```
┌─────────────── 用户端 ───────────────┐
│  Web Browser (http://127.0.0.1:8000) │
│  IDE / Cursor (MCP stdio)            │
└──────────────┬───────────────────────┘
               │ WebSocket / MCP stdio
┌──────────────▼───────────────────────┐
│         server_web.py (FastAPI)      │
│  /ws/chat/{id} · /api/upload · 静态  │
└──────────────┬───────────────────────┘
               │
┌──────────────▼───────────────────────┐
│      Client/core/graph_agent.py      │
│  LangGraph 异步 Agent（bind_tools）   │
│  checkpointer: AsyncSqliteSaver       │
└──────┬────────────┬──────────────┬────┘
       │            │              │
  LLMClient    ToolRegistry     MayaDocsRetriever
  (OpenAI      (9 个工具 +       (双轨 RAG)
   兼容 API)      危险审批)
       │            │
       │    MayaSocketClient (TCP, 4字节头)
┌──────▼────────────▼─────────────────┐
│   Autodesk Maya + plugin_server.py   │
│   主线程安全执行 Python，undoChunk   │
└──────────────────────────────────────┘
```

---

## 🚀 快速开始

### 1. 环境准备

```bash
git clone <repo-url>
cd DAMaya_Agent
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置

复制模板为 `settings.json`（该文件已被 `.gitignore` 排除，不会提交）：

```bash
cp settings.example.json settings.json
```

编辑 `settings.json`，填入大模型 API Key（推荐阿里云 DashScope）：

```json
{
  "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "chat_model": "qwen-turbo-latest",
  "maya_host": "127.0.0.1",
  "maya_port": 17022
}
```

### 3. 启动 Maya 宿主服务

在 Maya Script Editor（Python 标签页）运行：

```python
import sys
sys.path.insert(0, r"C:\path\to\DAMaya_Agent")
from Modules.plugin_server import start_server
start_server(port=17022)   # 与 settings.json 的 maya_port 一致
```

### 4. 启动 Agent Web 服务

```bash
python server_web.py
```

浏览器访问 http://127.0.0.1:8000

---

## 🧪 运行测试

```bash
python -m pytest tests/ -q
```

测试使用 `tests/conftest.py` 的 `MockMayaHost` 拦截底层 Socket，无需真实 Maya 即可跑通核心链路。

---

## 📖 使用指南

1. **连接**：Web 页面左侧 `+` 新建会话。
2. **对话**：底部输入指令，如 *"创建一个球体"* 或 *"检查场景中的五边面"*。
3. **观察**：Timeline 展示思考过程 → 工具调用 → 执行结果 → 最终回答。
4. **审批**：高危操作弹确认卡片，批准后才执行。

---

## 📂 目录结构

```text
DAMaya_Agent/
├── server_web.py           # FastAPI 主入口（WebSocket/REST/静态）
├── mcp_server.py           # MCP Server（IDE 接入，stdio）
├── settings.example.json   # 配置模板（复制为 settings.json）
├── requirements.txt
├── Client/
│   ├── config.py           # 配置加载（settings.json → .env 降级）
│   ├── core/
│   │   ├── graph_agent.py  # LangGraph 异步 Agent
│   │   ├── vector_rag.py   # 双轨 RAG（关键词 + FAISS）
│   │   └── database.py     # SQLite 会话/消息持久化
│   ├── tools/
│   │   ├── langchain_tools.py  # 9 个 StructuredTool 定义
│   │   ├── registry.py         # 工具注册（旧协议兼容）
│   │   └── skills/             # 预置 Python 技能脚本
│   └── maya_host/client.py     # Maya Socket 客户端
├── Modules/plugin_server.py    # Maya 侧 socket 服务器（主线程执行）
├── static/                     # Web 前端（index.html/app.js/styles.css）
├── launcher/                   # 系统托盘启动器（pystray）
└── tests/                      # pytest 测试
```

## 📄 许可证

MIT License

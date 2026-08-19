# DAMaya Agent — 项目总览

> Maya Technical Artist AI 助手 | v3.0
> 一个专为 Autodesk Maya 定制的本地部署 AI Agent，基于 LangGraph + 双轨 RAG，通过 Socket 与 Maya 实时互通，具备 Web UI 与 MCP IDE 接入。

---

## 项目定位

DAMaya Agent 将大语言模型（LLM）与 Maya Python 执行环境直连，用自然语言完成：
- 场景诊断与批量操作
- 节点属性读写、约束创建、位移变换
- 执行任意自定义 Python/MEL 脚本
- 基于 RAG 的 Maya 命令文档智能检索

---

## 系统架构

```
┌─────────────────────────────────────────────────────┐
│                     用户端                           │
│  Web Browser (http://127.0.0.1:8000)               │
│  IDE / Cursor / Claude Desktop (MCP stdio)          │
└──────────────────┬──────────────────────────────────┘
                   │ WebSocket / MCP stdio
┌──────────────────▼──────────────────────────────────┐
│              server_web.py  (FastAPI)                │
│  • WebSocket /ws/chat/{session_id}                  │
│  • REST: /api/config /api/sessions /api/upload      │
│  • Static: /static/* /uploads/*                     │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│         Client/core/graph_agent.py (LangGraph)       │
│  agent_node → should_continue → tool_node → agent    │
│  checkpointer: AsyncSqliteSaver (thread_id=session) │
└──────┬─────────────┬───────────────┬────────────────┘
       │             │               │
  ChatOpenAI    langchain_tools    vector_rag
  (OpenAI       (9 StructuredTool   (双轨: 关键词
   兼容 API)     + 危险审批)          + FAISS 向量)
       │             │
       │      MayaSocketClient (TCP 4字节头协议)
┌──────▼─────────────▼──────────────────────────────┐
│         Autodesk Maya + plugin_server.py           │
│  executeInMainThreadWithResult + undoChunk         │
└─────────────────────────────────────────────────────┘
```

---

## 核心技术栈

| 层次 | 技术 |
|------|------|
| **Agent 框架** | LangGraph（异步 StateGraph + checkpointer） |
| **LLM 通信** | OpenAI 兼容 API（DashScope），`ChatOpenAI` + `bind_tools` |
| **RAG** | 双轨：关键词匹配 + FAISS 向量（`faiss`/`numpy` 惰性导入，缺失降级） |
| **Web 服务** | FastAPI + Uvicorn + WebSocket |
| **前端** | 纯 HTML/CSS/JS，Marked.js + Highlight.js |
| **IDE 接入** | MCP（FastMCP，stdio 传输） |
| **Maya 通信** | 自研 TCP Socket（4 字节长度头 + JSON），默认端口 17022 |
| **持久化** | SQLite（会话/消息）+ LangGraph AsyncSqliteSaver |
| **配置** | settings.json（优先，已被 .gitignore 排除）+ .env 兼容 |

---

## 文件结构

```
DAMaya_Agent/
├── server_web.py          # FastAPI 主入口
├── mcp_server.py          # MCP Server（IDE 接入）
├── settings.example.json  # 配置模板
├── settings.json          # 实际配置（已忽略，不入库）
├── requirements.txt
├── Client/
│   ├── config.py          # 配置加载（settings.json → .env）
│   ├── core/
│   │   ├── graph_agent.py # LangGraph Agent
│   │   ├── vector_rag.py  # 双轨 RAG
│   │   └── database.py    # SQLite 持久化
│   ├── tools/
│   │   ├── langchain_tools.py # 9 个 StructuredTool
│   │   ├── registry.py    # 工具注册（旧协议）
│   │   └── skills/        # 预置技能脚本
│   └── maya_host/client.py   # Maya Socket 客户端
├── Modules/plugin_server.py  # Maya 侧 socket 服务器
├── static/                # Web 前端
├── launcher/              # 托盘启动器
└── tests/                 # pytest（MockMayaHost）
```

---

## 可用工具列表（Agent 工具白名单）

| 工具名 | 危险级 | 功能 |
|--------|--------|------|
| `query_selection_context` | 安全 | 获取当前选择集信息 |
| `get_scene_context` | 安全 | 兼容别名（同选择集） |
| `get_maya_docs` | 安全 | 查询 Maya Python 命令文档 |
| `run_custom_python` | ⚠️ 高危 | 执行任意 Python（最万能） |
| `create_and_connect_node` | ⚠️ 高危 | 创建节点并连接属性 |
| `transform_node` | ⚠️ 高危 | 修改位移/旋转/缩放 |
| `get_set_attribute` | ⚠️ 高危 | 读写节点属性（写需审批） |
| `create_constraint` | ⚠️ 高危 | 创建 Parent/Point/Orient 约束 |
| `execute_skill` | ⚠️ 高危 | 执行预置 Skill 脚本 |

> `langchain_tools.py` 用 `metadata["is_dangerous"]` 标记高危，供 GraphAgent 审批节点消费。

---

## MCP Server 工具（IDE 调用）

| MCP 工具 | 功能 |
|----------|------|
| `maya_run_python` | 执行任意 Maya Python |
| `maya_get_scene_info` | 场景总览 |
| `maya_get_selection` | 选中对象详情 |
| `maya_set_transform` | 修改位移/旋转/缩放 |
| `maya_get_attribute` / `maya_set_attribute` | 读/写节点属性 |
| `maya_list_attributes` | 列出节点属性 |
| `maya_get_docs` | 查 Maya 命令文档 |
| `damaya_read_file` / `damaya_list_project_files` | 读/浏览项目源文件 |
| `damaya_get_config` | 查看配置（不含 api_key） |

---

## 配置（settings.json）

```json
{
  "api_key": "sk-xxx",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "chat_model": "qwen-turbo-latest",
  "available_models": ["qwen-turbo-latest", "qwen-plus"],
  "translate_model": "qwen-turbo",
  "embedding_model": "text-embedding-v3",
  "maya_host": "127.0.0.1",
  "maya_port": 17022,
  "server_host": "127.0.0.1",
  "server_port": 8000,
  "agent_max_history_messages": 20,
  "agent_tool_repeat_limit": 3,
  "agent_max_react_rounds": 10
}
```

> `settings.json` 含敏感 api_key，已被 `.gitignore` 排除；模板见 `settings.example.json`。

---

## 启动方式

- **开发模式**：`python server_web.py`（或 `start_dev.bat`）
- **系统托盘**：`python launcher/launcher.py`
- **MCP 接入**：`.cursor/mcp.json` 配置 `mcp_server.py`
- **Maya 插件**（必须先启动）：`from Modules.plugin_server import start_server; start_server(port=17022)`

# DAMaya Agent — 项目总览

> Maya Technical Artist AI 助手 | v2.0  
> 一个专为 Autodesk Maya 定制的本地部署 AI Agent，通过 Socket 与 Maya 实时互通，具备 Web UI、MCP IDE 接入和独立托盘启动器。

---

## 项目定位

DAMaya Agent 将大语言模型（LLM）与 Autodesk Maya Python 执行环境直连，让用户用自然语言完成：
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
│  • REST: /api/config  /api/sessions  /api/upload    │
│  • Static: /static/*  /uploads/*                   │
└──────────────────┬──────────────────────────────────┘
                   │ 函数调用
┌──────────────────▼──────────────────────────────────┐
│            Client/core/agent_loop.py                 │
│  自研 ReAct Agent（XML 标签协议）                    │
│  <think> → <action> → [Observation] → <final_answer>│
│  流式逐 token 解析，分离思考流与答案流               │
└──────┬─────────────┬───────────────┬────────────────┘
       │             │               │
  LLMClient    ToolRegistry      DualTrackRAG
  (DashScope   (工具白名单         (Maya 命令文档
   兼容 API)    + 危险审批)         向量/关键词检索)
       │             │
       │      MayaSocketClient
       │      ↕ TCP Socket（自研 4字节头协议）
┌──────▼──────────────────────────────────────────────┐
│         Autodesk Maya + plugin_server.py             │
│  在 Maya 主线程安全执行 Python，捕获 result/stdout  │
└─────────────────────────────────────────────────────┘
```

---

## 核心技术栈

| 层次 | 技术 |
|------|------|
| **Agent 框架** | 自研 ReAct（无 LangChain / AutoGen 依赖） |
| **LLM 通信** | OpenAI 兼容 API（DashScope），SSE 流式 |
| **Web 服务** | FastAPI + Uvicorn + WebSocket |
| **前端** | 纯 HTML/CSS/JS（无框架），Marked.js + Highlight.js |
| **IDE 接入** | MCP Protocol（FastMCP，stdio 传输） |
| **Maya 通信** | 自研 TCP Socket（4字节长度头 + JSON） |
| **持久化** | SQLite（会话、消息历史） |
| **配置** | settings.json（优先）+ .env（兼容） |
| **启动器** | pystray 系统托盘（Windows/macOS/Linux） |

---

## 文件结构

```
DAMaya_Agent/
├── server_web.py          # FastAPI 主入口，WebSocket、REST、文件上传
├── mcp_server.py          # MCP Server（IDE 接入，stdio）
├── settings.json          # 主配置文件（模型、端口、UI 开关等）
├── start_dev.bat          # 开发模式启动（可见控制台）
├── requirements.txt
│
├── Client/
│   ├── config.py          # 配置加载（settings.json → .env 降级）
│   ├── core/
│   │   ├── agent_loop.py  # ReAct 主循环（XML 状态机 + 流式解析）
│   │   ├── llm_client.py  # LLM 流式客户端
│   │   ├── database.py    # SQLite 会话/消息持久化
│   │   └── rag.py         # 双轨 RAG（关键词 + 语义检索）
│   ├── tools/
│   │   ├── registry.py    # 工具注册表、危险审批
│   │   ├── maya_tools.py  # 9 个 Maya 工具实现
│   │   └── skills/        # 预置 Python 技能脚本
│   └── maya_host/
│       └── client.py      # Maya Socket 客户端（TCP，4字节头协议）
│
├── Modules/
│   └── plugin_server.py   # Maya 侧插件服务器（主线程安全执行）
│
├── static/
│   ├── index.html         # Web UI 入口（引用独立 CSS/JS）
│   ├── styles.css         # Premium 深色主题，全局动效
│   └── app.js             # 前端逻辑（Timeline、WebSocket、文件上传）
│
├── launcher/
│   ├── launcher.py        # 系统托盘启动器（pystray）
│   └── requirements_launcher.txt
│
└── .cursor/
    └── mcp.json           # Cursor IDE MCP 配置模板
```

---

## 可用工具列表（Agent 工具白名单）

| 工具名 | 危险级 | 功能 |
|--------|--------|------|
| `run_custom_python` | 安全 | 在 Maya 执行任意 Python（最万能） |
| `query_selection_context` | 安全 | 获取当前选中集合信息 |
| `get_scene_context` | 安全 | 同上（兼容别名） |
| `get_maya_docs` | 安全 | 查询 Maya Python 命令文档 |
| `transform_node` | ⚠️ 高危 | 修改节点位移/旋转/缩放（需审批） |
| `get_set_attribute` | ⚠️ 高危 | 读写节点属性（写入需审批） |
| `create_and_connect_node` | ⚠️ 高危 | 创建节点并连接属性 |
| `create_constraint` | ⚠️ 高危 | 创建 Parent/Point/Orient 约束 |
| `execute_skill` | ⚠️ 高危 | 执行预置 Skill 脚本 |

---

## 启动方式

### 方式 1：开发模式（推荐调试）
```bat
start_dev.bat
# 或直接：
python server_web.py
```
可见控制台，Ctrl+C 停止。访问 http://127.0.0.1:8000

### 方式 2：系统托盘（日常使用）
```bash
pip install -r launcher/requirements_launcher.txt
python launcher/launcher.py
# 任务栏右下角出现托盘图标，自动开启浏览器
```

### 方式 3：IDE MCP 接入（Cursor / Claude Desktop）
`.cursor/mcp.json` 已预生成，内容：
```json
{
  "mcpServers": {
    "damaya": {
      "command": "python",
      "args": ["C:/.../DAMaya_Agent/mcp_server.py"]
    }
  }
}
```
IDE 启动后自动通过 `mcp_server.py` 连接 Maya。

### Maya 插件启动（必须先做）
在 Maya Script Editor 中：
```python
import sys
sys.path.insert(0, r"C:/path/to/DAMaya_Agent")
from Modules.plugin_server import start_server
start_server(port=17022)   # 与 settings.json maya_port 一致
```

---

## 配置文件（settings.json）

```json
{
  "api_key":                  "sk-xxx",
  "base_url":                 "https://...",
  "chat_model":               "glm-5",
  "available_models":         ["glm-5", "qwen-turbo-latest", "..."],
  "translate_model":          "qwen3-coder-next",

  "maya_host":                "127.0.0.1",
  "maya_port":                17022,
  "maya_socket_timeout":      30,

  "server_host":              "127.0.0.1",
  "server_port":              8000,

  "agent_max_history_messages": 20,
  "agent_tool_repeat_limit":    3,
  "agent_max_react_rounds":     10,

  "ui_animations_enabled":    true,
  "ui_rag_enabled_default":   true,
  "ui_animation_speed":       1.0
}
```

> `ui_animations_enabled=false` 可完全关闭前端动效（低配机器推荐）  
> `available_models` 列表决定 Web UI 模型下拉选项  
> 修改配置后重启 `server_web.py` 生效

---

## Web UI 特性

- **Timeline 布局**：用户指令 → 思考过程（可折叠）→ 工具调用（带耗时）→ 最终回答
- **流光边框动效**：思考中（蓝色）、工具执行中（紫色）实时呼吸发光
- **Mac 风格代码块**：红黄绿圆点 + 一键复制 + 折叠按钮
- **扩展输入框**：📎 文件上传 + 🖼️ 图片上传 + RAG 开关 + 模型选择器
- **高危操作审批**：`is_dangerous` 工具触发前端确认卡片，用户批准后才执行
- **Settings 抽屉**：侧边栏 ⚙ 按钮，动效/RAG/模型实时切换
- **历史回放**：切换会话自动加载完整对话历史，正确渲染思考块和工具记录

---

## MCP Server 工具（IDE 调用）

| MCP 工具 | 功能 |
|----------|------|
| `maya_run_python` | 执行任意 Maya Python |
| `maya_get_scene_info` | 场景总览（选中、mesh 数、时间轴） |
| `maya_get_selection` | 选中对象详情 |
| `maya_set_transform` | 修改位移/旋转/缩放 |
| `maya_get_attribute` | 读取节点属性 |
| `maya_set_attribute` | 写入节点属性 |
| `maya_list_attributes` | 列出节点所有属性 |
| `maya_get_docs` | 查 Maya 命令文档 |
| `damaya_read_file` | 读取项目源文件（用于 IDE 自主迭代） |
| `damaya_list_project_files` | 浏览项目文件结构 |
| `damaya_get_config` | 查看当前运行配置 |

---

## ReAct 协议说明

LLM 输出格式（由 System Prompt 规范 + `agent_loop.py` 解析）：

```xml
<think>
分析当前场景，决定下一步操作...
</think>
<action>{"tool": "run_custom_python", "arguments": {"python_code": "..."}}</action>
```

收到 Observation 后继续：

```xml
<think>工具执行成功，可以给出最终结论了</think>
<final_answer>
## 🔍 执行结果
...
</final_answer>
```

- `<think>` 内容实时推送到前端（蓝色思考卡片）
- `<action>` 内容由后端静默解析执行，不推送原始 XML
- `<final_answer>` 内容实时推送并 Markdown 渲染
- 最多执行 `agent_max_react_rounds`（默认 10）轮

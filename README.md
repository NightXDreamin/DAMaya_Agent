# DAMaya Agent - Maya 智能化协作助手

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Maya 2022+](https://img.shields.io/badge/Maya-2022+-orange.svg)](https://www.autodesk.com/products/maya/overview)
[![License MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

DAMaya Agent 是专为 Autodesk Maya 打造的智能化技术美术（Technical Artist）助手。它基于 LLM（大语言模型）与 RAG（检索增强生成）技术，能够理解自然语言指令，并在 Maya 中自主执行复杂的场景操作、Python 脚本编写与调试任务。

该项目采用 **Client-Server** 架构，通过 WebSocket 实现外部 Agent 进程与 Maya 宿主环境的实时通信，旨在提升 TA 与美术人员的工作效率，将繁琐的操作自动化。

---

## ✨ 核心特性

- **🤖 自然语言交互**：直接使用中文指令控制 Maya，无需记忆复杂的菜单或命令。
    - *"创建一个 5x5 的立方体阵列"*
    - *"帮我检查当前场景中有哪些五边面"*
    - *"把选中的物体材质改为 Lambert 并赋予红色"*
- **🧠 智能思考链 (Chain of Thought)**：Agent 具备完整的推理能力，在执行前会展示 `<Thinking>` 思考过程，确保操作逻辑透明、可控。
- **🛠️ 强大的工具集 (Tool Use)**：
    - **自动代码执行**：自主编写并运行 `maya.cmds` / `pymel` 脚本。
    - **场景感知**：实时获取选择集、节点属性、DAG 层级结构。
    - **文档查询**：内置 Maya Python Command 文档库，支持 RAG 检索，减少幻觉。
- **⚡ 现代化的 Web UI**：
    - 极简主义深色主题 (Dark Mode)，适配专业生产环境。
    - **流式响应**：实时打字机效果展示 Agent 的思考与回复。
    - **折叠式时间线**：优雅地折叠冗长的思考过程与工具调用日志，保持界面整洁。
- **🛡️ 安全机制**：
    - **高危操作审批**：涉及文件删除、系统命令等高危操作需人工二次确认（开发中）。
    - **容错重试**：代码执行报错时，Agent 会自动阅读 Traceback 并尝试修正代码。

---

## 🏗️ 架构概览

项目主要由两部分组成：

1.  **Maya 宿主端 (`Modules/`)**：
    - 运行在 Maya 内部的 Python 插件服务。
    - 监听 Socket 端口，负责接收并执行来自 Agent 的 Python 代码，返回执行结果或报错信息。

2.  **Agent 客户端 (`Client/` & `server_web.py`)**：
    - 外部独立的 Python 进程，承载 LLM 核心逻辑。
    - **FastAPI** 后端：提供 REST API 和 WebSocket 服务。
    - **RAG 引擎**：管理向量数据库与文档检索。
    - **Web 前端**：基于 HTML/JS 的可视化交互界面。

---

## 🚀 快速开始

### 1. 环境准备

确保您已安装 Python 3.10+ 和 Autodesk Maya。

```bash
# 克隆项目
git clone https://github.com/NightXDreamin/DAMaya_Agent.git
cd DAMaya_Agent

# 创建并激活虚拟环境 (推荐)
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置 API Key

在项目根目录创建 `.env` 文件，填入您的大模型 API 密钥（推荐使用阿里云 DashScope/Qwen）：

```ini
# .env
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
MAYA_HOST=127.0.0.1
MAYA_PORT=17022
```

### 3. 启动 Maya 宿主服务

打开 Maya，打开 **Script Editor (脚本编辑器)**，输入并运行以下 Python 代码以启动监听服务：

```python
import sys
# 将项目 Modules 目录加入 Maya 路径 (请修改为实际路径)
sys.path.append(r"\path\to\your\folder\DAMaya_Agent") 

import Modules.server as server
# 启动服务，端口需与 .env 中一致
server.start_server(port=17022) 
```

*注：看到 `Server started on 127.0.0.1:17022` 即表示启动成功。*

### 4. 启动 Agent Web 服务

在终端中运行：

```bash
python server_web.py
```

服务启动后，浏览器访问：[http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## 📖 使用指南

1.  **连接**：打开 Web 页面，左侧栏点击 `+` 创建一个新会话。
2.  **对话**：在底部输入框输入指令，例如 *"创建一个球体"*。
3.  **观察**：
    - **Thinking**：查看 Agent 的拆解思路。
    - **Action**：观察 Agent 调用的 Maya 工具及参数。
    - **Observation**：查看 Maya 的执行反馈。
4.  **多轮交互**：您可以基于上一步的结果继续提问，例如 *"把它往上移动 5 个单位"*。

---

## 📂 目录结构

```text
DAMaya_Agent/
├── Client/                 # Agent 核心逻辑
│   ├── core/               # 核心模块 (AgentLoop, LLMClient, RAG)
│   ├── tools/              # 工具定义 (MayaTools, Registry)
│   └── maya_host/          # Socket 客户端通讯代码
├── Modules/                # Maya 内部插件 (Socket 服务端)
├── static/                 # Web 前端资源 (HTML/CSS/JS)
├── server_web.py           # FastAPI 启动入口
├── requirements.txt        # 项目依赖
├── README.md               # 项目文档
└── .env                    # 配置文件
```

## 🤝 贡献与反馈

欢迎提交 Issue 反馈 Bug 或建议。如果您有兴趣参与开发，请遵循 GitHub Flow 提交 Pull Request。

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

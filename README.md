<div align="center">
  <img src="desktop/frontend/public/logo.png" alt="MoFox Code Logo" width="180" />
</div>

<h1 align="center">MoFox Code Desktop</h1>

<p align="center">
  <b>AI 编程助手，一次安装，即刻拥有</b><br>
  基于 Neo-MoFox 框架的桌面整合版——图形化、零门槛、开箱即用
</p>

<p align="center">
  <a href="https://github.com/MoFox-Studio/MoFox-Code-Desktop/releases"><img src="https://img.shields.io/github/v/release/MoFox-Studio/MoFox-Code-Desktop?include_prereleases&label=release&color=blue" alt="Release"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-green.svg" alt="License"></a>
  <a href="https://tauri.app/"><img src="https://img.shields.io/badge/built%20with-Tauri-ffc131?logo=tauri" alt="Tauri"></a>
</p>

---

## 🖥️ 这是什么？

MoFox Code Desktop 是 [Neo-MoFox 框架](https://github.com/MoFox-Studio/Neo-MoFox) 的**桌面整合版**。我们把 Python 运行时、AI 引擎、WebUI 管理面板和本地推理链路全部打包进了一个原生桌面应用里。

你不用装 Python、不用配命令行、不用折腾环境——下载、安装、跟着向导点几下，一个属于你自己的 AI 编程助手就跑起来了。

> 如果你是开发者、想深入了解框架能力或二次开发，请查看底层框架 [Neo-MoFox](https://github.com/MoFox-Studio/Neo-MoFox)。

---

## ✨ 核心特性

### 🎯 真正的桌面体验
- **原生窗口**：基于 Tauri 2 构建，轻量（壳约 5MB）、启动快、内存省
- **自定义窗口装饰**：无边框设计，自绘标题栏按钮，现代风格
- **Setup 向导**：首次运行引导式配置 LLM 提供商、模型和用户偏好，全程 GUI
- **系统设置面板**：运行时随时调整模型、提供商等配置

### 🧠 完整 AI 能力
- 多 LLM 后端支持（OpenAI / Anthropic / 兼容 OpenAI API 的服务）
- 工具调用 + 插件系统（MCP 协议）
- 长短期记忆与对话归档
- 向量数据库（ChromaDB）本地持久化

### 🏗️ 双前端架构
- **Tauri 壳**：React + TypeScript + Tailwind CSS + Vite
- **WebUI 主体**：独立的 React 前端，通过 iframe 嵌入，支持 HMR 开发

### 📦 打包与分发
- NSIS 安装包（支持"为当前用户安装"和"为所有用户安装"）
- 便携版 zip 包备选
- 中英双语安装界面

---

## 🚀 快速上手

### 下载安装

去 [Releases](https://github.com/MoFox-Studio/MoFox-Code-Desktop/releases) 页面下载最新版：

- `MoFox-Code_*.exe` — NSIS 安装程序（推荐）
- `MoFox-Code-portable.zip` — 便携版，解压即用

### 首次启动

1. 启动应用，进入启动画面
2. 首次运行自动进入 **Setup 向导**
3. 选择 LLM 提供商 → 填入 API Key → 选择模型 → 设定你的昵称
4. 点击「完成并启动」— 你的 AI 伙伴就上线了

### 日常使用

- 主界面是完整的 WebUI 工作台，聊天、项目管理、插件市场一站搞定
- 按标题栏 `⋮` 按钮唤起 **系统设置** 随时换模型或改配置
- 关窗时自动优雅退出后端进程

---

## 🔧 开发者指南

### 技术栈

| 层 | 技术 |
|---|---|
| 桌面壳 | Tauri 2 (Rust) |
| 壳前端 | React 19 + TypeScript + Tailwind CSS + Vite |
| 后端 | Python 3.11+ (FastAPI + WebSocket) |
| WebUI | React 19 + Vite + Tailwind CSS |
| 打包 | PyInstaller + NSIS |
| 依赖管理 | uv (Python) / npm (Node) / Cargo (Rust) |

### 项目结构

```
MoFox-Code-Desktop/
├── desktop/                    # 桌面端目录
│   ├── frontend/               # Tauri 壳前端 (React)
│   │   ├── src/
│   │   │   ├── App.tsx         # 主应用（状态机：启动→向导→主界面）
│   │   │   └── components/     # 闪屏、设置向导、系统设置弹窗
│   │   └── package.json
│   ├── tauri/                  # Tauri Rust 源码
│   │   ├── src/lib.rs          # Rust 侧：启动/停止/重启后端进程
│   │   ├── Cargo.toml
│   │   └── tauri.conf.json     # Tauri 配置 & 打包参数
│   ├── launcher.py             # Python 后端启动器
│   ├── mofox-backend.spec      # PyInstaller 打包配置
│   └── build.bat               # 本地一键构建脚本
├── plugins/                    # Neo-MoFox 插件
│   └── coding_agent_webui/
│       └── frontend/           # WebUI 主体前端 (React)
├── src/                        # Neo-MoFox 框架源码
├── config/                     # 运行时配置（首次运行自动生成）
└── docs/                       # 框架文档
```

### 本地开发

```bash
# 1. 克隆仓库
git clone https://github.com/MoFox-Studio/MoFox-Code-Desktop.git
cd MoFox-Code-Desktop

# 2. 安装 Python 依赖
uv sync

# 3. 安装前端依赖（两个前端工程各自独立）
cd plugins/coding_agent_webui/frontend && npm install
cd desktop/frontend && npm install

# 4. 构建 WebUI 主体
cd plugins/coding_agent_webui/frontend && npm run build

# 5. 确保 Tauri dev 资源目录存在
mkdir -p desktop/dist/mofox-backend

# 6. 启动 Tauri 开发模式（从源码运行 Python 后端）
cd desktop/tauri
cargo tauri dev
```

> **💡 提示**：`cargo tauri dev` 会自动从 `.venv` 运行 Python 源码，无需先 PyInstaller 打包。改了 Python 代码直接重启即可生效。

### 调试 WebUI

高频调试 UI 时，建议直接在浏览器访问 Vite 开发服务器：

```bash
cd plugins/coding_agent_webui/frontend && npm run dev
# 访问 http://localhost:5173
# API 请求会自动代理到 Python 后端端口 (8681)
```

### 打包发布

```bash
# Windows 一键构建
./desktop/build.bat

# 产出
#   desktop/tauri/target/release/bundle/nsis/*.exe   (安装包)
#   或 desktop/dist/MoFox-Code-portable/              (便携包)
```

GitHub Actions 会在你发布 Release 时自动构建并 attach 产物，详见 [build-release.yml](.github/workflows/build-release.yml)。

---

## 🏗️ 架构简图

```
┌──────────────────────────────────────────┐
│              Tauri Shell (Rust)           │
│  ┌────────────────────────────────────┐   │
│  │   React 壳前端 (TypeScript)         │   │
│  │   • 启动画面 / 向导 / 设置弹窗      │   │
│  │   • 窗口控制 (最小化/最大化/关闭)    │   │
│  │   • 通过 Tauri invoke 管理后端进程   │   │
│  └──────────────┬─────────────────────┘   │
│                 │ iframe                   │
│  ┌──────────────▼─────────────────────┐   │
│  │   WebUI 主体 (React + Vite)         │   │
│  │   • 聊天界面 / 项目管理 / 插件市场   │   │
│  │   • 通过 HTTP/WS 与后端通信         │   │
│  └──────────────┬─────────────────────┘   │
└─────────────────┼────────────────────────┘
                  │ HTTP :8681 / WS :8765
┌─────────────────▼────────────────────────┐
│        Python 后端 (FastAPI)              │
│  ┌────────────────────────────────────┐   │
│  │   Neo-MoFox Framework               │   │
│  │   • LLM 多后端调度                   │   │
│  │   • 插件系统 (MCP 协议)              │   │
│  │   • 记忆管理 (ChromaDB)             │   │
│  │   • 事件总线 / 调度器               │   │
│  └────────────────────────────────────┘   │
└──────────────────────────────────────────┘
```

---

## 🔗 相关项目

| 项目 | 说明 |
|---|---|
| [Neo-MoFox](https://github.com/MoFox-Studio/Neo-MoFox) | 底层 AI 框架 — MoFox Code 的能力来源 |
| [Neo-MoFox-Launcher](https://github.com/MoFox-Studio/Neo-MoFox-Launcher) | 启动器部署版 — 可视化、无代码运行 |

---

## 🧭 社区

- 📚 **官方文档**：[https://docs.mofox-sama.com/](https://docs.mofox-sama.com/)
- 🐧 **用户 QQ 群**：169850076
- 🎧 **KOOK 频道**：[https://kook.vip/NmrFgn](https://kook.vip/NmrFgn)

---

## 📄 开源协议

本项目基于 [Neo-MoFox](https://github.com/MoFox-Studio/Neo-MoFox) 框架，采用 [AGPL-3.0](LICENSE) 协议开源。

<div align="center">
  <b>不再只是聊天机器人 — 这是你的 AI 伙伴 ❤️</b><br>
  如果 MoFox Code 帮到了你，欢迎点亮 ⭐ Star！
</div>

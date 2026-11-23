# PaneBell

[![Verify PaneBell](https://github.com/llm4omics/panebell/actions/workflows/ci.yml/badge.svg)](https://github.com/llm4omics/panebell/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: macOS 13+](https://img.shields.io/badge/macOS-13%2B-black.svg)](README.md#requirements)

**[English](README.md) · [简体中文](README.zh-CN.md)**

**PaneBell** 是一个原生 macOS 菜单栏工具，用来按需打开本机或远程 SSH 主机上的 `tmux` 项目，并把**待人工确认事项**与**未读完成回执**留在菜单栏里。点一下即可回到对应终端窗格；即使任务在 Mac 合盖或断网期间跑完，重连后结果依然保留在未读列表中，直到你主动标为已读。

```text
  菜单栏:  [ PaneBell · 2 ]
             ├── Waiting for you (1)      ──▶ 待确认/待授权事项，点击直达原 tmux 窗格
             ├── Unread completions (1)   ──▶ 持久化完成摘要，断线可补收，未读不丢失
             ├── Pinned / Recently opened ──▶ 置顶快捷入口与最近主动打开的 8 个项目
             ├── Projects                 ──▶ 按配置目录复用已有窗格或新建会话窗口
             └── Live windows (3)         ──▶ 实时列出主机活跃窗格，支持并排独立视图
```

## 核心特点

- **一键回到正确窗格，多窗互不抢焦点**：按项目目录匹配或复用终端窗口；选择 **Open side by side** 可创建独立 `tmux` 视图（`pb-view-…`），两个窗口查看同一会话的不同窗格互不干扰。
- **断线不丢回执，状态诚实不臆测**：结果摘要保存在项目主机的 SQLite 收件箱（`~/.local/state/panebell/events.sqlite3`），Mac 离线已读确认会在重连后自动同步；关闭等待中的窗格会自动隐藏过期提醒，绝不把“进程退出/空闲”伪造为任务成功。
- **仅观察、不越权的 AI 工具链集成**：内置 **Claude Code** 权限提醒钩子（仅提示工具名并定位窗格，不自动放行权限、不落盘命令参数）与 **Codex CLI** 回合完成回执适配，也可通过一行 `emit` 命令接入任意批处理脚本。
- **零常驻服务端、零云账号、零模型轮询**：直接复用系统 OpenSSH 与 `tmux`，日常状态同步均为普通本地/SSH 命令，不开放端口，不消耗任何模型 Token。

## 30 秒免配置体验

在装有 Xcode Command Line Tools 的 Mac 上直接运行：

```sh
./demo.sh
```

点击菜单栏 **PaneBell Demo · 1 → Waiting for you → Paper Planes**，在弹出的演示窗口点击 **Allow check** 并关闭窗口，再打开 **Unread completions** 查看持久化未读结果。演示完全在内存中运行，不需要远程主机、`tmux` 会话、SSH 密钥或模型账号。

## 安装与日常使用

**环境要求**：Mac 端需 macOS 13+、Xcode Command Line Tools 与 Python 3.9+（可选 WezTerm、VS Code）；项目所在机器（本机或远程 Linux/macOS）需 `tmux` 与 Python 3.9+。

1. **安装菜单栏应用**：
   ```sh
   ./install.sh
   open "$HOME/Applications/PaneBell.app"
   ```
   首次安装会生成空的个人配置 `~/.config/panebell/config.json`（留在仓库之外，参考 [虚构配置示例](examples/config.json)）。已有窗格会立即显示在 **Live windows**；如需保存常用项目入口，点击菜单 **Open settings…** 填入项目名、`tmux` 会话名和工作目录后重启应用即可。
2. **在项目主机安装助手并发送事件**：
   ```sh
   ./scripts/install-host.sh
   sh examples/sample-task.sh   # 在 tmux 窗格中试跑交互式虚构示例
   ```
   在自有脚本或工作流中，为每次运行分配稳定 ID 即可上报状态流转：
   ```sh
   helper="$HOME/.local/lib/panebell/panebell.py"
   python3 "$helper" emit waiting palette-001 --name "Paper Planes" --summary "Check the colour list?"
   python3 "$helper" emit complete palette-001 --name "Paper Planes" --summary "One duplicate colour."
   ```

## 进阶配置与本地验证

- **详细集成指南**：远程 SSH 主机配置、WezTerm 焦点记录（[`examples/wezterm-focus.lua`](examples/wezterm-focus.lua)）、自定义启动命令 `recipes.json`、Codex 完成通知及 Claude Code 自动权限提醒（[`examples/claude-hooks.json`](examples/claude-hooks.json)），详见 **[配置与集成说明（英文）](docs/setup.md)**。
- **本地构建与自检**：运行 `./scripts/build.sh && ./build/PaneBell.app/Contents/MacOS/PaneBell --self-test`。本版本以纯源码方式分发，在本机编译并完成 ad hoc 签名。遵循 [MIT 许可证](LICENSE)。

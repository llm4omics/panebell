# PaneBell

[![Verify PaneBell](https://github.com/llm4omics/panebell/actions/workflows/ci.yml/badge.svg)](https://github.com/llm4omics/panebell/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: macOS 13+](https://img.shields.io/badge/macOS-13%2B-black.svg)](README.md#requirements)

**[English](README.md) · [简体中文](README.zh-CN.md) · [在线交互演示](https://llm4omics.github.io/panebell/)**

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

## 30 秒安装与自动配置（推荐）

**环境要求**：Mac 端需 macOS 13+、Xcode Command Line Tools 与 Python 3.9+（可选 WezTerm、VS Code）；项目所在机器（本机或远程 Linux/macOS）需 `tmux` 与 Python 3.9+。

### 方式一：发给你的 AI 编程助手自动完成（Claude Code / Codex 等）

把下面这段引导词直接复制发给正在 Mac 上运行的终端 Agent：

> 请帮我安装并配置 PaneBell（仓库：`https://github.com/llm4omics/panebell.git`，若当前不在仓库目录请先克隆到 `~/Applications/panebell-src`）。先阅读 `README.zh-CN.md` 与 `docs/setup.md`：若我通过 `~/.ssh/config` 连接远程 SSH 主机跑 `tmux`，请先确认主机别名并运行 `./install.sh --setup --ssh-host <别名>`；若仅在本机使用 `tmux`，直接运行 `./install.sh --setup`。该命令会自动编译自检 `PaneBell.app`、安装 `~/.local/lib/panebell/panebell.py`、扫描当前活跃 `tmux` 会话生成或补充 `~/.config/panebell/config.json`（保留已有配置），并无损合并 Claude Code 的 `PermissionRequest` 观察钩子（`~/.claude/settings.json`）与 Codex CLI 的 `notify` 完成回调（`~/.codex/config.toml`，已有通知器会自动串联保留）。最后启动 `~/Applications/PaneBell.app` 并汇报配置摘要。

### 方式二：命令行一键向导

克隆仓库后在项目根目录执行一行命令，即可在 30 秒内完成应用编译、助手部署、活跃 `tmux` 会话导入以及 Claude Code / Codex 配置无损合并（执行前可先跑 `python3 ./panebell.py setup --dry-run` 预览变更）：

```sh
# 仅在本机 Mac 运行 tmux：
./install.sh --setup && open "$HOME/Applications/PaneBell.app"

# 或配对远程 SSH 主机别名（对应 ~/.ssh/config 中的 Host 别名，如 studio-box）：
./install.sh --setup --ssh-host studio-box && open "$HOME/Applications/PaneBell.app"
```

向导会自动跳过裸 `$HOME` 与根目录窗格、保留已有项目配置，并在 `~/.local/state/panebell/backups/` 保存修改前的原始配置备份（未检测到 `~/.claude` 或 `~/.codex` 时默认跳过，可追加 `--claude` / `--codex` 显式写入）。

### 分步手工安装与自定义事件上报

1. **仅编译安装菜单栏应用（不自动扫描或改写 Agent 配置）**：
   ```sh
   ./install.sh
   open "$HOME/Applications/PaneBell.app"
   ```
   首次安装会生成空的个人配置 `~/.config/panebell/config.json`（留在仓库之外，参考 [虚构配置示例](examples/config.json)）。已有窗格会立即显示在 **Live windows**；也可点击菜单 **Open settings…** 手动维护项目列表。
2. **单独在项目主机安装助手并发送自定义事件**：
   ```sh
   ./scripts/install-host.sh
   python3 ./panebell.py setup --host-only   # 可选：仅在远程主机无损合并 Claude Code / Codex 配置
   sh examples/sample-task.sh                # 在 tmux 窗格中试跑交互式虚构示例
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

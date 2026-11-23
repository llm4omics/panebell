# PaneBell

PaneBell 是一个 macOS 菜单栏工具，用于按需打开本机或 SSH 主机上的 tmux 项目，并把待授权事项和未读结果留在菜单里。

**先体验**。在装有 Xcode Command Line Tools 的 Mac 上运行 `./demo.sh`。点菜单栏 **PaneBell Demo · 1 → Waiting for you → Paper Planes**，再点 **Allow check**。示例会检查三种虚构颜色，发现一项重复。关掉窗口后，结果仍在 **Unread completions**，直到你标为已读。演示不需要服务器、模型账号或真实项目。

**开始使用**。运行 `./install.sh`，再运行 `open "$HOME/Applications/PaneBell.app"`。项目所在机器需要 Python 3.9+ 和 tmux。菜单中的 **Live windows** 列出已有窗格；要保存可重开的入口，可在 **Open settings** 中填写自己的项目目录与会话名称，然后重新启动。个人配置留在仓库之外。[虚构配置示例](examples/config.json)

**提醒如何产生**。任务进入待授权、取消、完成状态时，通过 `panebell.py emit` 上报。同一运行重复上报只留一份结果，Mac 断线后能补收。点待授权项可以返回对应终端；权限决定由任务本身处理。日常采样不调用模型。

[英文使用说明](README.md) · [SSH、WezTerm、Codex 和自定义启动命令](docs/setup.md) · [MIT 许可证](LICENSE)

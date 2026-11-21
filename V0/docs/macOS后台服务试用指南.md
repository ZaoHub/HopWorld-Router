# macOS：用户后台服务试用指南

本指南使用 macOS 的 LaunchAgent 在当前用户登录后运行 HopWorld Router，无需一直打开终端。**配置和运行流程尚未实测**；它不是系统级服务，用户未登录、退出登录或电脑休眠时不能保证持续运行。先完成 README 的 macOS 本机试用，确认项目、Python 虚拟环境和依赖可用，并停止手动运行的服务，以免占用同一端口。

## 1. 创建配置

在终端进入已有项目目录：

```sh
cd /你的路径/HopWorld-Router
.venv/bin/python deploy/create-macos-launch-agent.py
```

按提示输入两次至少 16 个字符且不包含冒号的管理密码，输入时不会显示。脚本会在当前用户的 `~/Library/LaunchAgents/com.hopworld.router.local.plist` 创建配置，权限为 `600`；已存在时不会覆盖。配置文件含有管理密码，不要分享或提交。数据和日志保存在项目的 `data/` 目录，同样需要保护。

脚本记录当前项目的绝对路径，移动或删除项目目录后需重新创建配置。服务仅监听 `127.0.0.1:8080`。使用节点测试或本地网络节点时，还需安装对应架构的 Xray；服务的 PATH 包含 `/opt/homebrew/bin` 和 `/usr/local/bin`。

## 2. 加载和检查

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.hopworld.router.local.plist"
launchctl print "gui/$(id -u)/com.hopworld.router.local"
```

用浏览器打开 `http://127.0.0.1:8080`，用户名为 `admin`，密码为创建配置时输入的值。若无法访问，可检查 `data/launchd.stdout.log` 和 `data/launchd.stderr.log`，以及 8080 是否已被其他程序占用；日志可能包含敏感信息，不要随意分享。

服务配置为用户登录后启动，异常退出时由 launchd 重新启动。首次加载需要处于当前用户的图形登录会话；休眠唤醒、重启和异常恢复的行为尚需实机验证。

## 3. 停止和卸载

停止并从当前会话卸载服务：

```sh
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.hopworld.router.local.plist"
```

以后要再次启动，可重新执行上面的 `launchctl bootstrap`。如果要彻底移除后台服务，确认已停止后再删除配置：

```sh
rm "$HOME/Library/LaunchAgents/com.hopworld.router.local.plist"
```

这不会删除项目或 `data/`。更换密码、移动项目或重建配置时，先停止并删除旧配置，再运行创建脚本和加载命令；保留 `data/` 以保留原有设置。更新前应备份数据，并在更新完成后检查服务状态。

IKEv2、证书自动签发和动态 DNS 的 macOS 后台集成未完成；请按[运行环境适配路线图](运行环境适配路线图.md)分别验证，不要将本服务的启动结果视作全部功能已支持。

# Windows：WSL 2 试用指南

本指南使用 Windows 的 WSL 2 运行 Ubuntu 中的 Linux 版 HopWorld Router，**尚未在 WSL 2 实测**。它不是 Windows 原生程序，暂不包含 Windows 服务、开机自动启动或 IKEv2、证书自动签发的完整部署。

## 1. 准备 WSL 2 和 Ubuntu

按 [Microsoft 的 WSL 安装说明](https://learn.microsoft.com/zh-cn/windows/wsl/install)安装 WSL 2 与 Ubuntu，并完成 Ubuntu 的首次用户设置。在 PowerShell 检查：

```powershell
wsl --list --verbose
```

确认 Ubuntu 的 `VERSION` 是 `2`，然后从开始菜单打开 Ubuntu。以下命令都在 Ubuntu 终端执行，不是在 PowerShell 执行。

## 2. 安装并启动

需要 Ubuntu 中的 Python 3.10 或更高版本。安装 Git、Python 和 curl：

```sh
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip curl ca-certificates
python3 --version
```

将项目与数据保存在 Ubuntu 用户的 Linux 主目录中：

```sh
cd ~
git clone https://github.com/ZaoHub/HopWorld-Router.git
cd HopWorld-Router
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export HOPWORLD_ADMIN_PASSWORD='请替换为至少16字符的随机密码'
.venv/bin/python -m router.server
```

启动前将密码替换为至少 16 个字符、不含冒号的随机字符串。服务默认只监听 WSL 内的 `127.0.0.1:8080`。保持 Ubuntu 终端运行，在 Windows 浏览器中尝试打开 `http://localhost:8080`；用户名为 `admin`，密码为设置的值。Windows 到 WSL 的 localhost 访问取决于 WSL 的实际网络配置；打不开时先按 [Microsoft WSL 网络说明](https://learn.microsoft.com/zh-cn/windows/wsl/networking)检查，不要直接将管理接口暴露到公网。

在 Ubuntu 终端按 `Ctrl+C` 停止服务。下次进入项目目录后重新设置密码环境变量并运行 `.venv/bin/python -m router.server`。此方法依赖 WSL 与进程持续运行，Windows 关机、休眠或 WSL 停止时服务可能不可用，恢复后需要检查服务状态。

## 3. 功能与数据

- 上面的命令仅准备订阅管理。节点测试还需要 Xray 和 curl，本地网络节点还需要 Xray，以及从客户端到 WSL 入站端口的实际网络路径。Xray 应使用与 WSL CPU 架构匹配的 Linux 版本；可参考项目 README 的 Linux Xray 安装步骤。
- WSL 的外网入站与端口转发取决于 Windows、WSL 网络模式、防火墙和上游路由；不要把 Windows 上能打开管理页视作公网节点已可访问。
- 该试用方式不配置 IKEv2、证书自动签发或动态 DNS 的系统集成。它们需要单独配置和验证。
- 数据默认保存在 `~/HopWorld-Router/data/`，包含订阅、节点凭据和访问密钥。备份时先停止服务并保护备份；不要提交或分享 `data/`、环境文件或订阅地址。
- 更新代码前先备份数据。在项目没有本地改动时，可停止服务后执行 `git pull --ff-only`，再运行 `.venv/bin/pip install -r requirements.txt` 并重新启动。

在确认设备、Windows 与 WSL 版本及测试结果前，本指南只作为试用途径；详细验收项目见[运行环境适配路线图](运行环境适配路线图.md)。

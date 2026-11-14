# Windows 原生：订阅管理试用指南

本指南使用 Windows 的 Python 手动运行订阅管理，**尚未在 Windows 设备上实测**。目前没有原生安装包、Windows 后台服务或开机启动。本地网络节点在 Windows 原生环境中禁用；如需该功能，可考虑 [WSL 2](Windows-WSL2试用指南.md) 或 [Docker Desktop](Docker-Desktop试用指南.md) 的试用方案，它们同样需要实际验证。节点测试、IKEv2、证书自动签发和动态 DNS 的 Windows 原生支持尚未验证，不应依赖。

## 准备并启动

安装 Git 和 Python 3.10 或更高版本，然后打开 PowerShell，在自己的用户目录中执行：

```powershell
git clone https://github.com/ZaoHub/HopWorld-Router.git
cd HopWorld-Router
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

为当前 PowerShell 会话设置管理密码并启动。将示例内容替换成至少 16 个字符、且不包含冒号的随机密码：

```powershell
$env:HOPWORLD_ADMIN_PASSWORD = '请替换为至少16字符的随机密码'
.\.venv\Scripts\python.exe -m router.server
```

打开 `http://127.0.0.1:8080`，用户名是 `admin`。默认仅监听本机；按 `Ctrl+C` 停止服务。下次打开 PowerShell 后，需要重新进入项目目录、设置密码环境变量并运行启动命令。密码可能留在 PowerShell 命令历史中，请按你的系统设置保护或清理相关历史记录，不要分享密码。

## 数据和功能限制

数据默认保存在项目的 `data` 目录，包含订阅链接、节点凭据和访问密钥。Windows 文件权限由所在目录的 ACL 控制，程序中的 Unix 权限位不能替代 ACL；请将项目和备份放在仅受信任账号可访问的目录，并在备份前停止服务。不要提交或公开这些文件。

Windows 原生环境的 Xray 本地节点进程管理尚未适配；当前会拒绝启用该节点，避免使用不兼容的 Unix PID 信号。更新前备份数据，停止服务后在没有本地代码改动的项目目录执行 `git pull --ff-only`，再用虚拟环境的 Python 重新安装依赖。远程订阅访问和 HTTPS 入口需要单独设计，不要直接将管理界面暴露到公网。

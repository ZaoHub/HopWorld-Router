# HopWorld-Router 在线安装

## GitHub Pages 网页安装

服务运行需要 Python 3.10+。使用 `deploy/hopworld-page-installer.service` 时，将 `installer.py` 和 `requirements.txt` 放在 `/opt/hopworld-page-installer`，在该目录创建 `.venv` 并安装依赖。为服务创建 `hopworld-installer` 系统账号。

创建权限为 `0600` 的 `/etc/hopworld-page-installer.env`，写入 `INSTALL_OPERATOR_TOKEN=<令牌>`。通过 `openssl rand -hex 32` 生成令牌，并通过受信渠道分发。启动安装服务，在对应域名的 Nginx HTTPS `server` 块包含 `deploy/nginx-location.conf`。

在 `dist/install-config.json` 的 `apiOrigin` 中设置网关的 HTTPS 根地址。将修改合并到 `main` 后，GitHub Pages 会发布配置。更换令牌时，更新服务环境中的 `INSTALL_OPERATOR_TOKEN` 并重启安装服务。网页显示 SSH 表单，资料齐全时点击安装即可查看进度。缺少访问令牌或主机指纹时，表单会显示相应的补填项。访问令牌仅留在当前打开的页面中，成功安装后的主机指纹按 IP 和端口保存在浏览器中。安装网关只连接公网单播 IP，先匹配指纹，再进行 SSH 身份验证。网页支持密码或 OpenSSH、PEM 私钥。私钥支持 RSA、ECDSA 和 Ed25519，可输入私钥口令。网页和后端均不保存 SSH 凭据。网页上可以查看输出和安装退出状态。

## 本机独立安装器

在运行安装器的电脑上安装 Python 3.10+，解压后执行：

```sh
bash start.sh
```

打开 http://127.0.0.1:18731/。输入服务器地址、SSH 端口、账号和从可信途径获得的 SHA256 主机指纹。SSH 密码只由本机进程用于登录。使用 SSH Agent 时，先把密钥加载到当前终端连接的 Agent。使用非 root 账号时，服务器需要允许该账号以 `sudo -n` 执行安装。目标服务器会运行 V1.0.2 官方 GitHub 安装脚本。

安装完成后按页面给出的 SSH 隧道命令访问后台。已有部署建议先按项目文档备份。按 Ctrl+C 关闭本机安装器。

## 终端管理

解压目录包含 `manage.py`，通过已配置的回环管理入口执行下列操作：

```sh
python3 manage.py password --url http://127.0.0.1:8080
python3 manage.py username --url http://127.0.0.1:8080
python3 manage.py port --url http://127.0.0.1:8080 --port 18081
```

CentOS 7 若无系统 Python 3，请用 `/opt/hopworld-router-legacy/current/python/bin/python3` 代替 `python3`。修改端口须在 Router 服务器本机执行，以便直接确认新入口。修改账号或密码可通过 SSH 隧道，URL 使用隧道的本地地址。其他 systemd、Docker、清理和排障命令见落地页的「命令大全」。

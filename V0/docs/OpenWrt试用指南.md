# OpenWrt：原生订阅管理试用准备

此方案为实验性准备，**尚未在 OpenWrt 或具体路由器上实测**，也没有安装包。它使用 procd 运行订阅管理，服务以 root 身份启动；不配置透明代理、DNS 接管、防火墙或系统 VPN。不要在生产路由器上直接依赖本方案。

## 先确认设备条件

1. 记录设备型号、OpenWrt 版本、CPU 架构、内存和可用持久存储。Python、依赖、项目、Xray 和数据都占用空间；不要假设路由器内置闪存足够。存储不足时应使用可靠的持久存储，并自行调整下方路径。
2. 准备可运行的 Python 3.10 或更高版本、虚拟环境和 `requirements.txt` 指定的 PyYAML。OpenWrt 各版本的软件源、包管理器和架构不同，Python 及 PyYAML 是否可安装需逐设备确认；当前没有经过验证的统一安装命令。
3. 确认可以通过 SSH 登录，且有可用的恢复途径。使用 `df -h` 检查目标路径，确认 `/opt/hopworld-router` 和 `/etc/hopworld-router` 位于重启后仍保留的数据分区。不要将数据放在 OpenWrt 的临时文件系统中。

## 准备代码与依赖

将项目代码放入路由器的 `/opt/hopworld-router`，从可信的 [GitHub 仓库](https://github.com/ZaoHub/HopWorld-Router)获取；不要将其他机器的 `.venv`、`data/`、`router.env` 或私钥复制过去。在路由器上进入该目录后，使用该设备自己的 Python 创建 `.venv` 并安装依赖：

```sh
cd /opt/hopworld-router
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'import yaml; print(yaml.__version__)'
```

如果 Python 构建不支持 `venv` / `pip`，或 PyYAML 没有适用于该架构的安装方式，请停止并针对系统版本解决依赖；不能假定上述命令对所有固件有效。

## 配置服务

以 root 创建 `/etc/hopworld-router.env`，使用仅 root 可修改和读取的权限，并写入一行：

```sh
( set -C; umask 077; cat > /etc/hopworld-router.env <<'EOF'
HOPWORLD_ADMIN_PASSWORD='请替换为至少16字符的随机密码'
EOF
)
```

创建时若文件已存在，命令会拒绝覆盖；请先检查现有配置。将示例替换成至少 16 个随机英文字母和数字；不能包含冒号。配置文件会被 root 的 shell 读取执行，**不能使用不受信任用户提供的内容**。不要分享文件、订阅地址或数据。服务固定只监听路由器本机的 `127.0.0.1:8080`，数据放在 `/etc/hopworld-router/data`。敏感数据需要可靠备份；系统升级或恢复出厂设置可能删除它们，应事先确认 OpenWrt 的备份与升级策略。

确认目标文件尚不存在后，以 root 安装项目提供的两个模板：

```sh
test ! -e /etc/init.d/hopworld-router && test ! -e /usr/libexec/hopworld-router || { echo '目标文件已存在，请先检查'; exit 1; }
mkdir -p /usr/libexec
cp /opt/hopworld-router/deploy/openwrt/hopworld-router-run /usr/libexec/hopworld-router
cp /opt/hopworld-router/deploy/openwrt/hopworld-router.init /etc/init.d/hopworld-router
chmod 755 /usr/libexec/hopworld-router /etc/init.d/hopworld-router
/etc/init.d/hopworld-router enable
/etc/init.d/hopworld-router start
```

从电脑使用 `ssh -L 8080:127.0.0.1:8080 root@路由器地址` 建立隧道，再打开本机 `http://127.0.0.1:8080`，用户名为 `admin`。不要在路由器防火墙中直接开放管理端口。日志可通过系统日志查看，但其中可能含敏感内容，分享前应检查。

## 停止与限制

停止并禁用启动：

```sh
/etc/init.d/hopworld-router stop
/etc/init.d/hopworld-router disable
```

停止后可先备份 `/etc/hopworld-router.env` 和 `/etc/hopworld-router/data`，再根据需要移除模板与代码；停止服务不会删除数据。设备升级、重启恢复及卸载仍需在目标固件验证。

节点测试需要兼容设备架构的 Xray 与 curl，本地网络节点还涉及端口和入站网络；二者均未在本方案中验证。IKEv2、证书自动签发、动态 DNS 和透明网关不在当前试用范围。请按[运行环境适配路线图](运行环境适配路线图.md)记录每项结果。

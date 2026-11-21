# 绿联与飞牛 NAS：容器试用指南

此指南为绿联 NAS（UGOS Pro）和飞牛私有云（fnOS）准备，**尚未在这些设备上实测**。只适用于能使用 SSH、Git、Docker Engine 和 Docker Compose 插件，且允许容器使用 Linux host 网络的环境；不同型号、系统版本和权限设置可能有所不同。其他 Linux NAS 也可以参考，但需自行确认兼容性。

建议先确认设备的 CPU 架构、可用存储空间、容器功能和 SSH 登录方式。下面的 `docker` 命令需要当前账号有相应权限；若平台要求 `sudo`，请按平台的权限配置执行。此容器会以 root 运行并使用宿主机网络，需确认这是你愿意采用的部署方式。

绿联 DXP4800 Plus 是首个计划实测的型号。根据[绿联官方产品资料](https://ai-eu.ugreen.com/products/ugreen-nasync-dxp4800-plus-nas-storage)，它采用 Intel x86 处理器，UGOS Pro 应用中心提供 Docker。Docker 功能可用并不意味着本项目已经通过该型号验证；系统版本、SSH 权限、Compose 和 host 网络仍需在实际设备上核对。

## 1. 连接并检查环境

从自己的电脑连接 NAS，替换用户名和地址：

```sh
ssh 用户名@NAS地址
uname -m
git --version
docker version
docker compose version
```

若命令不存在或 Docker 服务不可用，先按设备对应版本的官方说明启用或安装；不要继续假设其已支持。镜像中 Xray 的架构需要与设备匹配。

## 2. 下载并配置

在 NAS 上选择可持久保存的目录执行：

```sh
git clone https://github.com/ZaoHub/HopWorld-Router.git
cd HopWorld-Router
umask 077
mkdir -p data
chmod 700 data
printf 'HOPWORLD_ADMIN_PASSWORD=%s\n' '请替换为随机密码' > router.env
chmod 600 router.env
```

在启动前，将 `router.env` 中的密码替换为至少 16 个字符、不含冒号的随机密码。`router.env`、`data/` 和订阅地址包含敏感信息，不要提交或分享；备份时也应妥善保护。`data/` 存放数据库及运行配置，升级或删除容器前应先备份。

## 3. 构建、启动和访问

```sh
docker compose up -d --build
docker compose ps
docker compose exec hopworld-router xray version
```

管理服务默认只监听 NAS 本机 `127.0.0.1:8080`。在自己的电脑另开终端建立 SSH 隧道：

```sh
ssh -L 8080:127.0.0.1:8080 用户名@NAS地址
```

保持连接，打开电脑上的 `http://127.0.0.1:8080`，用户名为 `admin`，密码为刚配置的值。若电脑本地 8080 已占用，可把命令中的第一个 `8080` 改为其他端口，并访问该端口。不要把管理界面直接开放到公网。

容器默认包含 Xray 和 curl；节点测试与本地网络节点仍需实际验证。启用本地节点时，客户端需要能访问 NAS 对应入站端口；局域网可达不代表公网可达。远程获取订阅需另行配置安全的 HTTPS 入口，仅转发订阅路径。

## 4. 更新、停止和备份

更新前备份 `router.env` 与 `data/`。从仓库安装且没有本地代码改动时，可在项目目录执行：

```sh
git pull --ff-only
docker compose up -d --build
docker compose logs --tail=100 hopworld-router
```

停止服务使用 `docker compose down`；该命令不会删除 `data/`。还原备份前应停止服务，避免覆盖正在使用的数据库。不要将日志、配置或备份中的订阅链接和凭据公开。

IKEv2、证书自动签发和 DNS Webhook 需要额外的系统依赖与配置，当前 Compose 文件没有提供这些功能的完整部署。设备重启、容器恢复、升级、外网访问及各项功能均需在目标设备上实测；请参照[运行环境适配路线图](运行环境适配路线图.md)记录结果。

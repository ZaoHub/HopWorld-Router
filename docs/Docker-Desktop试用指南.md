# macOS / Windows：Docker Desktop 试用指南

本指南提供 Docker Desktop 的独立 Compose 配置，**尚未在 macOS 或 Windows 的 Docker Desktop 实测**。它使用 Linux 容器与端口映射，不要求启用 Docker Desktop 的 host 网络。需要已安装并启动 Docker Desktop、启用 Linux 容器，以及能够使用 Git 和 Docker Compose；Windows 不使用 Windows 容器模式。

## 1. 获取项目

打开 macOS 的终端或 Windows 的 PowerShell，选择一个可持久保存的目录：

```sh
git clone https://github.com/ZaoHub/HopWorld-Router.git
cd HopWorld-Router
docker version
docker compose version
```

## 2. 设置密码和数据目录

在项目目录创建名为 `router.env` 的纯文本文件，使用 UTF-8 编码，写入以下一行，并把示例文字替换成至少 16 个随机英文字母和数字组成的密码：

```text
HOPWORLD_ADMIN_PASSWORD=请替换为至少16字符的随机密码
```

文件内容不要加引号或空格。限制其他账号访问这个文件；它不会随项目提交，但密码、`data/` 和订阅链接都应妥善保管。配置文件会在首次启动时挂载项目目录下的 `data/`，请确认 Docker Desktop 允许共享该目录。

## 3. 构建和启动

在项目目录执行：

```sh
docker compose -f compose.desktop.yaml up -d --build
docker compose -f compose.desktop.yaml ps
docker compose -f compose.desktop.yaml exec hopworld-router xray version
```

用本机浏览器打开 `http://127.0.0.1:8080`，用户名为 `admin`，密码为刚才设置的值。若 8080 已被占用，可修改 `compose.desktop.yaml` 中端口映射的第一个 `8080`，然后重新启动，浏览器也使用修改后的端口。

容器内部管理服务监听容器网络的 `0.0.0.0:8080`，Compose 将宿主机端口绑定到 `127.0.0.1`。不要把管理端口改为公网绑定，也不要让不受信任的容器加入同一网络。端口绑定和网络隔离仍需在实际设备上验证。

## 4. 更新与停止

更新前先停止容器并备份 `data/` 和 `router.env`；备份中包含订阅和节点凭据。项目没有本地代码改动时可执行：

```sh
docker compose -f compose.desktop.yaml down
```

备份完成后：

```sh
git pull --ff-only
docker compose -f compose.desktop.yaml up -d --build
docker compose -f compose.desktop.yaml logs --tail=100 hopworld-router
```

停止服务可使用 `docker compose -f compose.desktop.yaml down`；它不会删除 `data/`。电脑关机、休眠或 Docker Desktop 未运行时服务不可用；恢复后应检查容器和节点状态。

## 功能范围

镜像包含 Xray 和 curl，可供订阅管理与节点测试使用，但在 Docker Desktop 上尚未完成实际验证。默认配置只映射管理端口，本地网络节点端口没有映射。

### 可选：映射本地节点端口

仓库提供可选的 `compose.desktop.node.yaml`，将容器的 TCP 8443 映射到宿主机的 `127.0.0.1:8443`，仅供本机试用。保留自己的配置副本，便于以后更新项目：

```sh
cp compose.desktop.node.yaml compose.node.local.yaml
docker compose -f compose.desktop.yaml -f compose.node.local.yaml up -d --build
```

管理页中的本地节点端口需与容器端口一致；更改端口时同时修改副本中映射的两端。使用此附加配置后，后续 `up`、更新和查看配置也要同时指定这两个 `-f` 文件；只使用基础配置重新创建容器会移除该映射。停止服务可以执行 `docker compose -f compose.desktop.yaml -f compose.node.local.yaml down`。

默认 `127.0.0.1` 不允许其他设备通过此映射连接。如果确需其他设备连接，必须自行调整副本中的宿主机绑定地址，并验证 Docker Desktop 端口映射、系统防火墙、路由和访问控制；绑定到所有地址可能同时暴露局域网或公网。不要修改管理端口的本机绑定。外部连接还需要可达的节点地址，管理页能打开不等于节点可连接。端口映射、本地节点和客户端连接均尚未实测。

IKEv2、证书自动签发与 DNS Webhook 不在此 Compose 配置中完整部署。订阅远程访问也需要另行配置受保护的 HTTPS 入口。各项功能与重启恢复的验证计划见[运行环境适配路线图](运行环境适配路线图.md)。

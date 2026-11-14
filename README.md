# HopWorld Router

HopWorld Router 是独立于 [HopWorld macOS 应用](https://github.com/ZaoHub/HopWorld) 的服务端项目。

## 当前可用

HopWorld Router 同时做两件事：**把运行本服务的机器网络做成可订阅的节点**，以及**汇总多条订阅与手动节点**。

### 订阅汇总

通过网页添加多个 HTTPS 上游订阅或手动导入节点，服务每小时更新一次，也可以手动更新；将选中的节点合并、精确去重后，生成带随机访问密钥的 Base64 订阅地址。客户端使用新订阅时**直接连接原节点**，代理流量不经过本服务。更新失败保留上次成功的结果，可以更换订阅密钥使旧地址失效。节点选择按完整链接识别，上游改变链接或名称后可能需要重新选择。

支持纯文本、Base64 格式节点链接，以及部分 Clash YAML 节点的转换。暂不支持的协议或参数会跳过并显示数量，不会悄悄丢弃参数；例如 XHTTP 的非 auto 模式暂不转换。暂不支持订阅重定向或完整协议参数验证。上游订阅地址由管理员提供，请勿为不受信任的用户开放管理界面。

订阅和节点列表默认收起，可批量更新全部订阅；节点测试可勾选一个或多个订阅组、手动导入组，或逐个测试。所选组内的节点无需逐个勾选，多个所选组里相同的链接只测试一次；测试结束后，可用节点按延迟从低到高排列。批量任务最多并发测试四个节点，可请求停止，当前运行的请求完成后退出。测试通过独立 Xray 进程请求选定的 Google、YouTube 或 TikTok 测试地址，显示该请求的延迟，不等于下载带宽，也不证明其他网站、UDP 或长期连接可用；测试失败也不能单独证明节点失效。需安装 Xray 和 curl，或通过 `HOPWORLD_XRAY` 指定 Xray 路径。当前仅接入 VLESS、Trojan、VMess、现代 Shadowsocks、SOCKS5、HTTP/HTTPS 的部分参数；其他协议或未识别参数会标为暂不支持，尚未达到 HopWorld 的完整协议范围。

### 本地网络节点（VLESS Reality）

在 Linux 等服务端（需有**可从外网连入的公网地址或域名**，并放行对应 TCP 端口）启用后，本机会常驻 Xray 入站，生成 VLESS Reality 分享链接；**启用且未在节点列表中禁用时，该链接会自动写入对外订阅**，与上游、手动节点一并去重合并。出口流量走**本机网络**，不经过 Router 的 HTTP 管理进程转发。可通过环境变量 `HOPWORLD_PUBLIC_HOST` 预填公网地址。管理页仅监听回环地址时，客户端仍直连 Reality 入站，不经过管理端口。

这与「手动导入一条 HTTP/SOCKS 节点」不同：本地网络节点由本服务生成并维护 Xray 配置，协议为 VLESS Reality（默认端口见管理页），不是把本机已有 HTTP 代理进程一键注册为本地节点。

**公网地址变化与订阅（两种模式，二选一）：**

1. **固定域名 + 自动 DNS（推荐，类似 Cloudflare 稳定入口、无需 Cloudflare）**
   公网地址填 **域名**，开启 **固定域名 + 自动 DNS**。Reality 与订阅里的 `vless://` **始终是该域名**；Router 实时探测出口并更新你在 DNSPod 或自建 Webhook 上的 A 记录。客户端与订阅内容中的节点主机名不变，仅 DNS 指向新 IP。需在 `/etc/hopworld-router.env` 配置 `HOPWORLD_DDNS_PROVIDER=dnspod` 或 `webhook` 及对应密钥（见 `router/ddns.py`）。**非 DNSPod** 可用 `deploy/dns-webhook/` 侧车对接 **Cloudflare / 阿里云 / Namesilo**（见该目录 `README.md`）。**要求公网能连入 Reality 端口**（VPS 或已放行入站）；纯「无入站云电脑」仍需自有穿透，DDNS 不能代替隧道。

2. **出口 IP 写进订阅**
   开启 **出口 IP 写进订阅**（原「自动跟随出口」）。探测到的 IP 直接写入 `vless://`；**订阅 `/s/…` token 不变**，但节点地址会变，客户端需自动更新订阅。固定 IP 的 VPS 请关闭上述两项，只填 IP 即可。

**Let's Encrypt（90 天）自动证书：** 勾选 **Let's Encrypt**，填写邮箱与 **校验方式**：**DNS-01**（无需开放 80，需 DNSPod `HOPWORLD_DDNS_*` 或 Webhook `HOPWORLD_ACME_DNS_WEBHOOK_URL`）、**HTTP-01（80）**（webroot：`HOPWORLD_ACME_WEBROOT`）、**TLS-ALPN-01（443）**（`HOPWORLD_ACME_TLS_ALPN=1`）、或 **自动**（有 DNS API 则优先 DNS，否则 webroot）。VPS 可执行 `deploy/hopworld-install-acme-webroot.sh`；证书在 `HOPWORLD_DATA/certs/<域名>/`，**≤3 天** 自动续期。HTTPS 反代 nginx 时执行 `deploy/hopworld-install-certbot-renew-hook.sh`，续期后自动 `reload nginx` 并同步证书副本。

若运行环境**不能直连公网**、必须走平台提供的 `HTTP_PROXY` / `HTTPS_PROXY`（如云电脑 hatch 出口），启用或保存本地节点时只要进程中带有这些环境变量，Xray 会把客户端流量经该 HTTP 中转出站；与 `~/HopWorld/http_proxy.py` 类似，**不要**用 `env -u` 清空代理变量后启动 Router。

#### 在云电脑上用本地节点（与 HopWorld 代理说明配套）

目标：手机 / HopWorld 客户端只装**一条订阅**，其中包含「云电脑出口」的 VLESS Reality 节点（可同时汇总其它上游）。

1. **在同一台云电脑**安装 Xray 与本项目，正常登录 shell 下启动（须能读到 `HTTP_PROXY` / `HTTPS_PROXY`），设置 `HOPWORLD_ADMIN_PASSWORD`，运行 `python -m router.server` 或通过 systemd（`EnvironmentFile` 需包含平台出口变量，或从 `/etc/profile.d` 继承）。
2. **入站与配置单里的公网 v4/v6**：`HopWorld-代理说明.md` / `云电脑配置单.md` 实测有**会变的**公网出口 IPv4（共享 NAT）与 IPv6，但这表示**出站**经 NAT 出去时对外显示的地址，不等于有**稳定入站**。同一份说明还写明：当前**不能从外网直接连进来**、不宜用 A/AAAA 绑域名。订阅里「公网地址」填的是**客户端连入**用的地址：HopWorld Router 支持填**域名、IPv4 或 IPv6**（与 `validate_public_host` 一致）。若平台始终不允许外网入站，仍须 Cloudflare Tunnel 等，把 **Reality 入站**（默认 8443）暴露成稳定域名；若今后实测在 `0.0.0.0:8443` 上可从外网拨通，也可暂时用当次测得的公网 IP 填入（换机或 NAT 变化后要在管理页改掉并**更新订阅**）。
3. 管理页 **启用「本地网络节点」**；复制 **订阅地址**（`/s/…`）。若管理页只监听 `127.0.0.1`，可在本机用 `ssh -L 8080:127.0.0.1:8080` 打开管理页，订阅链接本身需通过你另行配置的 HTTPS 反代暴露 `/s/`，或在隧道内增加仅转发订阅路径的入口。
4. 客户端更新订阅后使用名为「本地网络」（或你自定义名称）的节点；流量路径：**客户端 → 隧道域名 Reality 入站 → 云电脑 Xray →（有 `HTTP_PROXY` 时）出口中转 → 外网**。
5. **`~/HopWorld/http_proxy.py` 与 Router 本地节点是两套服务**：不必为了订阅去开 8080 HTTP 代理；换机后除 `~/HopWorld/start-http.sh` 外，还需重新启动 Router / 本地 Xray，并确认隧道仍指向当前机器。

有固定公网 IP 的 VPS 或家宽可省略隧道步骤，仅需放行 TCP 入站端口。

### 系统 VPN（IKEv2）

可选启用 strongSwan IKEv2（PSK），供 iPhone 等通过系统 VPN 连入本机；提供描述文件下载链接，并可在管理页监视在线设备与上网诊断。IKEv2 **不**作为 Clash 等客户端订阅里的一条代理链接，可与本地 Reality 节点同时使用。

### 尚未实现

- 将本机已有 **HTTP/SOCKS 代理服务**（例如仅监听 `127.0.0.1:8080` 的进程）自动发现、注册为「本地网络节点」
- 节点**自动换线**（`router/ranking.py` 等仅为模块雏形，未接入管理页与订阅输出）
- OpenWrt、Windows 与透明网关等场景的完整部署与验证

## 本机试用

需要 Python 3.10 或更高版本，并安装 `requirements.txt` 中的 PyYAML 依赖。

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export HOPWORLD_ADMIN_PASSWORD='请替换为至少16字符的随机密码'
.venv/bin/python -m router.server
```

打开 `http://127.0.0.1:8080`，用户名为 `admin`，密码为上面设置的值。程序只监听 `127.0.0.1`；配置保存在 `./data/router.db`。可用 `HOPWORLD_DATA` 指定数据目录，`HOPWORLD_PORT` 指定监听端口。停止服务按 `Ctrl+C`。

生成的地址默认仅能从运行服务的机器访问。访问远程服务器的管理界面时，可通过 SSH 本地转发并让两端使用相同端口，例如 `ssh -L 8080:127.0.0.1:8080 user@server`，然后访问本机 `127.0.0.1:8080`。不要直接将 HTTP 管理界面或未保护的数据库暴露到公网。若需其他设备远程获取订阅，须另行部署受信任的 HTTPS 反向代理，仅转发 `/s/` 路径，并避免在访问日志中记录订阅地址；管理接口仍应限于本机或 SSH 隧道。

订阅链接、上游地址、节点凭据和访问密钥保存在数据目录中，订阅地址本身可用于访问节点内容；请妥善保护数据目录和备份，避免分享或提交到 Git。Base64 不是加密。该初版尚未经过安全审计或生产环境验证。

## 全新 Linux 服务器

下面是一台新机器从空目录到能打开管理页的顺序。命令都在服务器上以 root 执行。适用 Debian 12、Ubuntu 22.04 及更新版本，或 RHEL / Rocky / Alma 9 及更新版本。`python3 --version` 须为 3.10 或更高。

有公网入站的 VPS 按本节安装。没有入站的云电脑仍要自备隧道，本节不包含隧道开通。`deploy/push-to-server.sh` 和 `deploy/hopworld-emergency-repair.sh` 写的是某一台现网机器，不要拿来装新服务器。

### 1. 系统包

Debian / Ubuntu：

```sh
apt update
apt install -y python3 python3-venv python3-pip curl ca-certificates
```

RHEL / Rocky / Alma：

```sh
dnf install -y python3 python3-pip curl ca-certificates
```

### 2. Xray

本地网络节点和节点延迟测试都要 Xray。只做订阅汇总可以跳过，以后补装再重启服务即可。

```sh
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
xray version
```

装好后二进制在 `/usr/local/bin/xray`。服务用户能直接执行，不必再设 `HOPWORLD_XRAY`。

### 3. 代码、用户、虚拟环境

```sh
id hopworld-router >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/hopworld-router --shell "$(command -v nologin)" hopworld-router
install -d -o hopworld-router -g hopworld-router -m 750 /var/lib/hopworld-router

git clone https://github.com/ZaoHub/HopWorld-Router.git /opt/hopworld-router
cd /opt/hopworld-router
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

还没有 Git 远程时，在你自己的电脑上把目录拷过去（排除本机虚拟环境和数据库）：

```sh
rsync -a --exclude .venv --exclude data --exclude __pycache__ ./ root@服务器:/opt/hopworld-router/
```

然后在服务器上进入 `/opt/hopworld-router`，再执行上面的 `python3 -m venv` 和 `pip install`。

### 4. 环境文件

密码至少 16 字符，且不能包含冒号。下面生成的字符串满足这两点。

```sh
PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
cat > /etc/hopworld-router.env << EOF
HOPWORLD_ADMIN_PASSWORD=${PW}
HOPWORLD_HOST=127.0.0.1
HOPWORLD_PORT=8080
HOPWORLD_DATA=/var/lib/hopworld-router
EOF
chmod 600 /etc/hopworld-router.env
echo "管理密码（只显示这一次）：${PW}"
```

公网地址已知时，可再加一行 `HOPWORLD_PUBLIC_HOST=你的域名或公网IP`。管理页里仍可修改。

### 5. 启动

```sh
cp /opt/hopworld-router/deploy/hopworld-router.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hopworld-router
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/
```

最后一行应打印 `401`。这表示管理页只在本机监听，且未登录会拒绝。`systemctl status hopworld-router` 应为 active。

### 6. 打开管理页

管理端口不要写入云厂商安全组，也不要在本机防火墙放行。在你自己的电脑上：

```sh
ssh -L 8080:127.0.0.1:8080 root@服务器
```

浏览器打开 `http://127.0.0.1:8080`。用户名 `admin`，密码是第 4 步打印的那一串。

到这里，订阅汇总已经可用：在管理页添加上游订阅或手动节点，复制订阅地址。该地址默认只有服务器本机能打开。手机要远程拉取订阅，继续做第 8 步。

### 7. 本地网络节点

在管理页启用「本地网络节点」。默认入站是 TCP **8443**，Xray 监听 `0.0.0.0`。云厂商安全组放行 TCP 8443。本机若已经启用防火墙，再执行对应的一条：

```sh
ufw allow 8443/tcp
firewall-cmd --permanent --add-port=8443/tcp && firewall-cmd --reload
```

不要为了这份说明去新开一道防火墙，以免把 SSH 拦在外面。启用后在服务器上确认：

```sh
ss -lntp | grep 8443
```

固定公网 IP 时，管理页公网地址填这个 IP，并关闭「固定域名 + 自动 DNS」和「出口 IP 写进订阅」。用域名时，先把 A 记录指到这台机器，公网地址填域名。

### 8. 手机用 HTTPS 拉取订阅

只反代 `/s/`，管理页仍走 SSH 隧道。先把域名 A 记录指到本机，并在安全组放行 TCP 80 和 TCP 443。证书签发完成后，80 可以继续留着给续期用。

Debian / Ubuntu：

```sh
apt install -y nginx certbot
install -d -o hopworld-router -g hopworld-router -m 755 /var/www/acme
rm -f /etc/nginx/sites-enabled/default
install -m 644 /opt/hopworld-router/deploy/hopworld-acme-webroot.conf /etc/nginx/conf.d/hopworld-acme-webroot.conf
printf '%s\n' 'hopworld-router ALL=(root) NOPASSWD: /usr/bin/certbot' > /etc/sudoers.d/hopworld-router-acme
chmod 440 /etc/sudoers.d/hopworld-router-acme
visudo -cf /etc/sudoers.d/hopworld-router-acme
systemctl enable --now nginx
```

RHEL / Rocky / Alma 可以直接：

```sh
bash /opt/hopworld-router/deploy/hopworld-install-acme-webroot.sh
```

Debian / Ubuntu 把下面四行追加到 `/etc/hopworld-router.env`。RHEL 脚本已经写过前两行，只追加后两行：

```
HOPWORLD_ACME_WEBROOT=/var/www/acme
HOPWORLD_ACME_WRAPPER=sudo /usr/bin/certbot
HOPWORLD_PUBLIC_HOST=你的域名
HOPWORLD_SUBSCRIPTION_BASE=https://你的域名
```

`systemctl restart hopworld-router` 后，在管理页填写邮箱，校验方式选 HTTP-01，勾选 Let's Encrypt。证书出现在 `/etc/letsencrypt/live/你的域名/` 之后，再挂上 443：

```sh
sed 's/example.com/你的域名/g' /opt/hopworld-router/deploy/hopworld-nginx-https-subscription.conf > /etc/nginx/conf.d/hopworld-https-subscription.conf
nginx -t && systemctl reload nginx
bash /opt/hopworld-router/deploy/hopworld-install-certbot-renew-hook.sh
```

手机打开管理页里的 `https://你的域名/s/…`。`https://你的域名/` 本身应是 404，管理界面不会从公网打开。

已有 DNS API、不想开 80 时，用第 9 步的 Webhook 或 DNSPod，管理页校验方式选 DNS-01。DNS-01 不需要放行 80。

### 9. 公网 IP 会变化

固定 IP 跳过本节。域名解析交给 DNSPod 时，在 `/etc/hopworld-router.env` 增加 `HOPWORLD_DDNS_PROVIDER=dnspod`、`HOPWORLD_DDNS_ID`、`HOPWORLD_DDNS_TOKEN`，重启服务，管理页开启「固定域名 + 自动 DNS」。

Cloudflare、阿里云或 Namesilo：先按 `deploy/dns-webhook/README.md` 写好 `/etc/hopworld-dns-webhook.env`，再执行：

```sh
bash /opt/hopworld-router/deploy/hopworld-install-dns-webhook.sh
```

并在 `/etc/hopworld-router.env` 增加：

```
HOPWORLD_DDNS_PROVIDER=webhook
HOPWORLD_DDNS_WEBHOOK_URL=http://127.0.0.1:9876/
```

然后 `systemctl restart hopworld-router`。9876 只监听本机，不要放行。

### 10. 系统 VPN（IKEv2，可选）

```sh
# Debian / Ubuntu
apt install -y strongswan strongswan-swanctl
# RHEL / Rocky / Alma（需已启用 EPEL 或系统源里有 strongswan）
dnf install -y strongswan

install -m 755 /opt/hopworld-router/deploy/hopworld-ikev2-apply /usr/local/sbin/hopworld-ikev2-apply
install -m 755 /opt/hopworld-router/deploy/hopworld-ikev2-updown /usr/local/sbin/hopworld-ikev2-updown
printf '%s\n' 'hopworld-router ALL=(root) NOPASSWD: /usr/local/sbin/hopworld-ikev2-apply' > /etc/sudoers.d/hopworld-router-ikev2
chmod 440 /etc/sudoers.d/hopworld-router-ikev2
visudo -cf /etc/sudoers.d/hopworld-router-ikev2
bash /opt/hopworld-router/deploy/hopworld-install-boot.sh
```

安全组放行 UDP 500 和 UDP 4500。本机防火墙若已启用，同样放行这两个 UDP 端口。然后在管理页启用 IKEv2。

`deploy/hopworld-ikev2-apply` 里给 VPN 客户端回访本机预留的 TCP 端口是 8080 和 40300。SSH 不是 40300 时，安装前把这两处 `40300` 改成你的 SSH 端口。

### 装完核对

- `systemctl is-active hopworld-router` 为 `active`
- 本机 `curl` 访问 `127.0.0.1:8080` 返回 401
- 安全组没有放行 TCP 8080
- 启用本地节点后，`ss` 能看到 8443，外网能连上该 TCP 端口
- 做了第 8 步时，公网能打开订阅 URL，打开 `https://域名/` 得到 404

## Docker（Linux）

Linux Docker 可使用主机网络，使仅监听回环地址的管理服务可通过宿主机本地或 SSH 隧道访问：

```sh
docker build -t hopworld-router .
# 在权限为 600 的 router.env 中设置 HOPWORLD_ADMIN_PASSWORD
mkdir -p data
docker run -d --name hopworld-router --restart unless-stopped --network host --env-file router.env -v "$(pwd)/data:/data" hopworld-router
```

`router.env` 不应提交或分享。此方式依赖 Linux 的主机网络，Docker Desktop、NAS 等环境可能不同，需要单独验证；请勿将容器改成公网监听后直接暴露管理界面。

## 测试

```sh
.venv/bin/python -m unittest discover -s tests -v
```

当前测试覆盖订阅合并、认证、更新失败保留旧结果、密钥轮换等基础行为；未验证真实上游订阅、Linux systemd 或 Docker 的完整部署。

## 后续部署目标

Linux x86_64 / ARM64、Linux 虚拟机和 Docker/NAS 优先；OpenWrt、Windows 和透明网关需分别设计、适配并在真实环境验证。macOS 保留原有 HopWorld 应用。

## 许可证

源码采用 [MIT License](LICENSE)。代理内核及其他第三方组件遵循各自的许可证，分发时需保留相应版权和许可文件。

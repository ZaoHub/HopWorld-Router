# HopWorld Router

当前分支为 V1 开发版。功能范围、启动方式和待完成验收见 [V1 开发与验收](docs/V1开发与验收.md)。

HopWorld Router 是独立于 [HopWorld macOS 应用](https://github.com/ZaoHub/HopWorld) 的服务端项目。

功能完成情况与限制见 [功能实现清单](docs/功能实现清单.md)。

旧版源码归档见 [V0](V0/README.md)（V1 开发前的基线 `e9f1d7a`）。 加密 DNS 配置、隔离出口实验与验收进度见 [V1 网络安全与验收](docs/V1网络安全与验收.md)。

[查看 V1 放行端口表](#v1-部署端口与安全组)

## 当前可用

HopWorld Router 同时做两件事：**把运行本服务的机器网络做成可订阅的节点**，以及**汇总多条订阅与手动节点**。

### 订阅汇总

通过网页添加多个 HTTPS 上游订阅或手动导入节点，服务每小时更新一次，也可以手动更新；将选中的节点合并、精确去重后，生成带随机访问密钥的 Base64 订阅地址。客户端使用新订阅时**直接连接原节点**，代理流量不经过本服务。更新失败保留上次成功的结果，可以更换订阅密钥使旧地址失效。节点选择按完整链接识别，上游改变链接或名称后可能需要重新选择。

支持纯文本、Base64 格式节点链接，以及部分 Clash YAML 节点的转换。暂不支持的协议或参数会跳过并显示数量，不会悄悄丢弃参数；例如 XHTTP 的非 auto 模式暂不转换。暂不支持订阅重定向或完整协议参数验证。上游订阅地址由管理员提供，请勿为不受信任的用户开放管理界面。

在「公网接入 → 公网订阅」可以发布带访问密钥的 HTTPS 地址并复制或扫码。要通过这台机器的网络出站，点击「配置本地网络节点」，选择已安装的内核，再设置公网可达的节点地址和出口。订阅下载与节点连接使用独立端口，具体传输协议由所选内核决定。

订阅和节点列表默认收起，可批量更新全部订阅；节点测试可勾选一个或多个订阅组、手动导入组，或逐个测试。所选组内的节点无需逐个勾选，多个所选组里相同的链接只测试一次；测试结束后，可用节点按延迟从低到高排列。批量任务最多并发测试四个节点，可请求停止，当前运行的请求完成后退出。测试通过独立 Xray 进程请求选定的 Google、YouTube 或 TikTok 测试地址，显示该请求的延迟，不等于下载带宽，也不证明其他网站、UDP 或长期连接可用；测试失败也不能单独证明节点失效。需有 Xray 和 curl，或通过 `HOPWORLD_XRAY` 指定 Xray 路径。当前仅接入 VLESS、Trojan、VMess、现代 Shadowsocks、SOCKS5、HTTP/HTTPS 的部分参数；其他协议或未识别参数会标为暂不支持，尚未达到 HopWorld 的完整协议范围。

### 本地网络节点（VLESS Reality）

在 Linux 等服务端（需有**可从外网连入的公网地址或域名**，并放行对应 TCP 端口）启用后，本机会常驻 Xray 入站，生成 VLESS Reality 分享链接；**启用且未在节点列表中禁用时，该链接会自动写入对外订阅**，与上游、手动节点一并去重合并。业务流量经所选出口转发，管理进程仅负责配置。可通过环境变量 `HOPWORLD_PUBLIC_HOST` 预填公网地址。管理页仅监听回环地址时，客户端仍直连 Reality 入站，不经过管理端口。

这与「手动导入一条 HTTP/SOCKS 节点」不同：本地网络节点由本服务生成并维护 Xray 配置，协议为 VLESS Reality（默认端口见管理页）；已有本机代理可扫描后选作该节点的出口。

在「本地网络转节点 → 订阅地址类型」可选择自动获取公网 IPv4 或 IPv6、手动填写域名或 IP，或者使用域名加 DDNS。IPv4 对应 A 记录，IPv6 对应 AAAA 记录，双栈域名可同时使用两类记录。同一局域网内使用时可手动填写 Router 主机的局域网 IP。

启用后，管理页显示本地网络节点的上下行速度和累计流量，覆盖 Xray 管理的各出口模式。上行指客户端发往节点，下行指节点发往客户端；统计包含入站连接的协议开销，不包含订阅中客户端直连的其他节点或 IKEv2。累计值从当前 Xray 进程启动开始，重启后清零；监控数据无法读取时会提示不可用。

本地网络节点支持转发 UDP：客户端需要支持并启用 VLESS Reality 的 UDP 转发，服务器需要能够向目标发送和接收 UDP。客户端连接 Reality 入站使用 TCP，不需要为该入站端口额外开放同号 UDP 端口。选择本机网络且没有环境 HTTP 代理时，UDP 通过服务器直接出站；如使用环境 HTTP 代理、本机代理或上游自动换线，UDP 是否可用取决于所选出口，普通 HTTP 代理不支持 UDP。节点的网页测速成功不代表 UDP 已验证。

**出口方式**可在本地节点设置中选择：

- **绑定指定上游**（新安装默认）：发布节点仅使用选定的上游。该上游被停用、删除或参数无法转换时，阻断业务流量。HTTP 上游只接收 TCP。
- **本机网络**：使用本机出口；如已设置 `HTTP_PROXY` / `HTTPS_PROXY`，仍沿用原有环境代理规则。
- **本机已有代理**：扫描本服务网络空间内 `127.0.0.1` 的常用端口或指定端口，选择发现的免认证 HTTP CONNECT / SOCKS5 代理作为 Reality 节点的出口。扫描以随机数据检查本机回环 TCP，SOCKS5 还会检查 UDP 往返；失败时 UDP 显示待验证，HTTP 不支持 UDP。扫描不访问互联网，代理须允许连接本机临时监听地址；回环成功不代表公网流量一定可用。容器内的本机地址不一定是宿主机。
- **上游自动换线**：从节点列表中所有已启用、可解析的上游节点构建候选池，跳过本地节点和回环地址；使用 Xray 观测所选测试目标（Google、YouTube 或 Cloudflare），为新连接选择延迟较低的可用出口。可选择 30 秒、1 分钟（默认）、2 分钟或 5 分钟探测间隔；实际发现故障的时间还取决于探测耗时。没有候选或候选全部不可用时阻断，不回退到直连。修改节点列表或探测配置会重建 Xray 配置并可能中断现有连接；线路本身故障时已有连接也可能中断，需要客户端重试。候选越多、探测越频繁，资源和流量开销越大。

自动选择依赖测试目标、Xray 支持的节点参数和网络环境；探测成功不代表全部网站、UDP 或长期连接可用。管理页可查看各候选最近的探测状态和延迟，但不显示每条连接实际使用的出口；探测开始前可能暂时无法连接。观测结果通过权限受限的本机套接字读取，不另开公网监控端口；若无法读取会显示不可用，不影响换线规则。请勿把指向本入口的其他域名别名加入候选，避免形成代理循环。

**公网地址变化与订阅（两种模式，二选一）：**

1. **固定域名 + 自动 DNS（推荐，类似 Cloudflare 稳定入口、无需 Cloudflare）**
   公网地址填 **域名**，开启 **固定域名 + 自动 DNS**。Reality 与订阅里的 `vless://` **始终是该域名**；Router 实时探测出口并更新你在 DNSPod 或自建 Webhook 上的 A 记录。客户端与订阅内容中的节点主机名不变，仅 DNS 指向新 IP。需在 `/etc/hopworld-router.env` 配置 `HOPWORLD_DDNS_PROVIDER=dnspod` 或 `webhook` 及对应密钥（见 `router/ddns.py`）。**非 DNSPod** 可用 `deploy/dns-webhook/` 侧车对接 **Cloudflare / 阿里云 / Namesilo**（见该目录 `README.md`）。**要求公网能连入 Reality 端口**（VPS 或已放行入站）；纯「无入站云电脑」仍需自有穿透，DDNS 不能代替隧道。

2. **出口 IP 写进订阅**
   开启 **出口 IP 写进订阅**（原「自动跟随出口」）。探测到的 IP 直接写入 `vless://`；**订阅 `/s/…` token 不变**，但节点地址会变，客户端需自动更新订阅。固定 IP 的 VPS 请关闭上述两项，只填 IP 即可。

**Let's Encrypt（90 天）自动证书：** 勾选 **Let's Encrypt**，填写邮箱与 **校验方式**：**DNS-01**（无需开放 80，需 DNSPod `HOPWORLD_DDNS_*` 或 Webhook `HOPWORLD_ACME_DNS_WEBHOOK_URL`）、**HTTP-01（80）**（webroot：`HOPWORLD_ACME_WEBROOT`）、**TLS-ALPN-01（443）**（`HOPWORLD_ACME_TLS_ALPN=1`）、或 **自动**（有 DNS API 则优先 DNS，否则 webroot）。做过 root 授权时，管理页申请 HTTP-01 会安装 nginx 与 certbot 并放行本机 TCP 80；也可以执行 `deploy/hopworld-install-acme-webroot.sh`。证书在 `HOPWORLD_DATA/certs/<域名>/`，**≤3 天** 自动续期。HTTPS 反代 nginx 时执行 `deploy/hopworld-install-certbot-renew-hook.sh`，续期后自动 `reload nginx` 并同步证书副本。订阅路径的 443 站点不会自动挂上。

**公网 IP 证书：** 在「域名与证书 → 申请证书」可以选择 IPv4 或 IPv6，留空自动检测，或填写本机公网地址。点「验证公网可达性」，通过 Let’s Encrypt 测试环境后再正式申请。HTTP-01 使用公网 TCP 80，Certbot standalone 至少需要 5.3，webroot 至少需要 5.4。TLS-ALPN-01 用公网 TCP 443，由 lego 5 处理，可用 `HOPWORLD_ACME_LEGO` 指定路径。若网站已使用 TCP 443，现有入口需按 `acme-tls/1` 将挑战交给 lego，并通过 `HOPWORLD_ACME_TLS_PORT` 指定后端验证端口。

公网 IP 证书使用 160 小时的短期配置。剩余约 80 小时进入续签窗口，失败后退避重试，剩余不足 48 小时会在证书页提醒。公网订阅与 Hysteria 2 可以复用同一公网 IP 证书。自动检测地址的绑定会在新地址证书签发成功后更新。固定手动地址继续使用原地址。旧 IP 地址会随网络变动失去可达性，客户端需使用更新后的地址。

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

可选启用 strongSwan IKEv2（PSK），供 iPhone 等通过系统 VPN 连入本机；做过 root 授权后，管理页启用时会安装缺少的 strongSwan。提供描述文件下载链接，并可在管理页监视在线设备与上网诊断。IKEv2 **不**作为 Clash 等客户端订阅里的一条代理链接，可与本地 Reality 节点同时使用。

### 尚未完整实现

- 本机代理扫描暂不支持认证代理、非回环地址、代理禁止访问本机地址或跨容器自动发现。
- 自动换线暂不显示实时选中出口、不可配置多个测试目标的优先级，也不保证已建立连接无中断。
- OpenWrt、Windows 与透明网关等场景的完整部署与验证

## 安装教程

### V1 部署端口与安全组

只用管理后台时，入站只需实际的 SSH 端口（通常为 TCP 22）。后台默认监听 `127.0.0.1:8080`，通过 SSH 隧道访问。按实际启用的功能设置云安全组、主机防火墙及 NAT 转发。

#### 入站端口

| 功能或软件 | 协议 | 默认或示例端口 | 放行位置与条件 |
| --- | --- | --- | --- |
| SSH 管理 | TCP | 22 或服务器实际端口 | VPS 入站，可将来源限制为管理员 IP |
| Router 后台 | TCP | 8080 | 服务器回环地址，通过 SSH 隧道访问 |
| Router 独立 HTTPS 订阅 | TCP | 9443 | 直接发布时对外放行。通过反代发布时按后端地址限制访问 |
| Nginx 或其他 HTTPS 反代 | TCP | 443 | 在 HTTPS 公网入口放行 |
| Certbot / lego，HTTP-01 | TCP | 80 | 在 ACME 公网入口放行，首签和续期都需要 |
| lego，TLS-ALPN-01 | TCP | 443 | 在 ACME 公网入口放行。DNS-01 通过 DNS 记录验证，无额外入站要求 |
| Xray-core / VLESS Reality | TCP | 8443 | 节点直连时，用实际监听端口放行 |
| Hysteria 2 独立内核 | UDP | 8442 | 节点直连时，用实际监听端口放行 |
| sing-box / Hysteria 2、TUIC | UDP | 分别为 8444、8445 | 只放行已启用的节点协议对应端口 |
| TUIC Server 独立内核 | UDP | 8446 | 节点直连时，用实际监听端口放行 |
| Mihomo / Shadowsocks | TCP、UDP | 8388 | 节点直连时，用实际监听端口放行 |
| shadowsocks-rust / Shadowsocks | TCP、UDP | 8389 | 节点直连时，用实际监听端口放行 |
| FRP 服务端 `frps` 控制连接 | TCP | 7000（示例） | 在中转 VPS 放行，供 `frpc` 连入 |
| FRP 节点映射 | TCP 或 UDP | 以 VPS 映射配置为准 | 客户端连中转 VPS 的对外映射端口 |
| strongSwan / IKEv2 | UDP | 500、4500 | 启用系统 VPN 后，在接入主机放行 |
| DNS Webhook、TLS-ALPN 分流后端 | TCP | 分别为 9876、10443（后者为配置示例） | 保持回环或内部访问 |
| 旧版组合节点内部 SOCKS | TCP、UDP | 37891 | Router 同机内核使用，保持回环访问 |

独立节点直连需在对应节点设置中勾选「允许其他设备连接」。旧版组合节点默认通过 TCP 8443 提供 Reality，启用旧版 QUIC 后通常使用 UDP 8444、8445。更改端口后，以管理页显示的监听或 FRP 映射为准。IPv4 在 NAT 后时，同步转发对应端口和协议。启用 IPv6 时，同时配置 IPv6 入站规则。

#### 出站端口

| 功能或软件 | 协议 | 目的端口 | 目标 |
| --- | --- | --- | --- |
| 安装和更新 Router、内核、独立 Python 及依赖 | TCP | 443；使用 HTTP 镜像时为 80 | GitHub、PyPI、APT/YUM 软件源或配置的镜像 |
| 系统 DNS | UDP、TCP | 53 | 服务器配置的递归解析器 |
| 加密 DNS | TCP | DoH 443、DoT 853，或所配置的端口 | 已选解析器。经固定 SOCKS5 出口时，主机外层按实际 SOCKS5 端点放行 |
| 公网证书、DDNS / DNS-01 API | TCP | 通常为 443 | ACME 服务及所选 DNS 服务商的 HTTPS API |
| 上游订阅、节点测速、Reality 目标站 | TCP | 通常为 443，按 URL 或目标配置 | 上游订阅站、测速站和配置的目标站 |
| FRP 客户端 `frpc` | TCP | 7000（示例）或实际控制端口 | 中转 VPS 的 `frps` |
| 本地节点直出或固定上游 | TCP、UDP | 按目标或上游的实际端口 | 业务目的地址或已选代理。使用 QUIC 上游时按其配置开放 UDP |

根据运行环境展开对应教程：

- **Linux 服务器 / VPS**：Debian、Ubuntu、RHEL、Rocky Linux、AlmaLinux。
- **Linux 虚拟机 / ARM 小主机**：使用 Linux 原生安装教程，并核对设备与架构要求。
- **Linux Docker**：使用 Docker 在 Linux 主机上运行。
- **macOS / Windows Docker Desktop**：使用桌面容器试用。
- **macOS**：使用 Python 在本机试用。
- **Windows + WSL 2**：在 Linux 子系统中试用。
- **Windows 原生**：使用 Python 在本机试用订阅管理。
- **OpenWrt**：查看原生试用准备和限制。
- **NAS**：查看当前支持状态。

<details>
<summary><strong>macOS：本机试用</strong></summary>

### macOS：本机试用

需要 Git、Python 3.10 或更高版本。打开“终端”，依次执行：

```sh
git clone https://github.com/ZaoHub/HopWorld-Router.git
cd HopWorld-Router
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m router.server
```

打开 `http://127.0.0.1:8080`。新安装的初始账号和密码均为 `admin`。首次打开管理台时，页面会要求设置至少 6 个字符的新密码，字符类型不限。服务器的新安装可通过 SSH 隧道完成这一步，再使用新密码登录公网 HTTPS 管理台。已设置的 `HOPWORLD_ADMIN_PASSWORD` 继续生效。程序默认监听 `127.0.0.1`，配置存放在 `./data/router.db`。可以用 `HOPWORLD_DATA` 指定数据目录，`HOPWORLD_PORT` 指定初始端口。停止服务按 `Ctrl+C`。后台运行可参考 [macOS 后台服务试用指南](docs/macOS后台服务试用指南.md)。节点测试和本地网络节点需另外安装 Xray，节点测试还需 curl。

访问远程服务器的管理界面时，可通过 SSH 本地转发并让两端使用相同端口，例如 `ssh -L 8080:127.0.0.1:8080 user@server`，然后访问本机 `127.0.0.1:8080`。不要直接将 HTTP 管理界面或未保护的数据库暴露到公网。外网订阅使用下方的 HTTPS 订阅入口，管理接口限于本机或 SSH 隧道。

订阅链接、上游地址、节点凭据和访问密钥保存在数据目录中，订阅地址本身可用于访问节点内容；请妥善保护数据目录和备份，避免分享或提交到 Git。Base64 不是加密。该初版尚未经过安全审计或生产环境验证。

</details>

<details>
<summary><strong>Linux 服务器 / VPS：原生安装</strong></summary>

Ubuntu／Debian 测试环境可从源码构建带 SHA-256 校验文件的 `.deb`，由 APT 安装并注册 systemd 服务。见 [V1 原生测试包](docs/V1开发与验收.md#linux-原生测试包)。

### 从 GitHub 一键安装 V1

在使用 systemd 的 VPS 上，以 root 执行。服务器需有 `curl` 并能访问 GitHub：

```bash
( set -o pipefail; curl -fsSL https://raw.githubusercontent.com/ZaoHub/HopWorld-Router/v1.0.3/deploy/install.sh | bash )
```

- **Ubuntu 22.04+、Debian 12+**：校验 Release 的 `.deb`，由 APT 安装并启动后台。
- **Ubuntu 18.04、CentOS 7**：在 x86_64 或 ARM64 上安装后台与独立 Python 3.11 环境，保留系统 Python，由 systemd 启动后台。内核、证书和透明网关功能依赖各自的系统工具及内核能力。
- 按[端口与安全组](#v1-部署端口与安全组)配置入站，再按下方「打开管理页」使用 SSH 隧道。首次用 `admin / admin` 登录，进入后台后在弹窗里设置新密码。只有 FTP 的共享主机需要换用有 root 终端的服务器。

旧系统安装的程序位于 `/opt/hopworld-router-legacy`，数据位于 `/var/lib/hopworld-router`。旧系统后续升级时，以 root 执行目标 GitHub Release 公布的一键命令。升级切换前备份数据库。已有其他方式安装时，先备份并完成迁移。版本同步流程见 [V1 版本发布与更新](docs/V1版本发布与更新.md)。

### 全新 Linux 服务器

以 root 执行下面这一段，安装并启动管理服务。所需代理内核由服务器管理员安装。适用 Debian 12、Ubuntu 22.04 及更新版本，或 RHEL / Rocky / Alma 9 及更新版本。`python3` 须为 3.10 或更高。Ubuntu 18.04 和 CentOS 7 使用上面的一键安装。

Linux 虚拟机、树莓派和其他 ARM 小主机可在系统版本符合上述要求时参考本节。先用 `uname -m` 核对架构。虚拟机和家庭网络的入站、端口转发及开机恢复需要各自配置；ARM 和虚拟机部署尚未完成实机验证。有公网入站的 VPS 按本节安装。没有入站的云电脑仍要自备隧道，本节不包含隧道开通。紧急修复脚本不适合作为安装程序。

```sh
set -euo pipefail
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y git python3 python3-venv python3-pip curl ca-certificates sudo
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y git python3 python3-pip curl ca-certificates sudo
elif command -v yum >/dev/null 2>&1; then
  yum install -y git python3 python3-pip curl ca-certificates sudo
else
  echo '需要 apt、dnf 或 yum' >&2
  exit 1
fi
python3 -c 'import sys; raise SystemExit("python3 须为 3.10 或更高，当前是 %s" % sys.version.split()[0]) if sys.version_info < (3, 10) else None'
id hopworld-router >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/hopworld-router --shell "$(command -v nologin)" hopworld-router
install -d -o hopworld-router -g hopworld-router -m 750 /var/lib/hopworld-router
if [ ! -d /opt/hopworld-router/.git ]; then
  git clone https://github.com/ZaoHub/HopWorld-Router.git /opt/hopworld-router
fi
cd /opt/hopworld-router
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
if [ ! -f /etc/hopworld-router.env ]; then
  cat > /etc/hopworld-router.env << EOF
HOPWORLD_ADMIN_PASSWORD=admin
HOPWORLD_HOST=127.0.0.1
HOPWORLD_PORT=8080
HOPWORLD_DATA=/var/lib/hopworld-router
EOF
  chmod 600 /etc/hopworld-router.env
  echo '初始账号和密码均为 admin。请通过本机地址或 SSH 隧道登录并设置新密码。'
else
  echo '已有 /etc/hopworld-router.env，不覆盖密码'
fi
cp deploy/hopworld-router.service /etc/systemd/system/
systemctl daemon-reload
bash deploy/hopworld-install-autostart.sh
systemctl enable --now hopworld-router.service || {
  bash deploy/hopworld-router-check.sh --instructions
  exit 1
}
bash deploy/hopworld-router-check.sh
```

最后一行会打印管理地址、SSH 转发、服务状态、日志与恢复命令，并检查本机服务是否就绪。以后网页打不开时，可再次执行 `sudo bash /opt/hopworld-router/deploy/hopworld-router-check.sh`。请在「内核管理」安装本地发布所需的内核。服务也会检测 `PATH` 中的程序。手工指定绝对路径时，可按内核设置 `HOPWORLD_XRAY`、`HOPWORLD_SINGBOX`、`HOPWORLD_MIHOMO`、`HOPWORLD_HYSTERIA`、`HOPWORLD_SSSERVER` 或 `HOPWORLD_TUIC_SERVER`。节点测速按协议使用所需的探测内核。

还没有 Git 远程时，在你自己的电脑上把目录拷过去（排除本机虚拟环境和数据库），再在服务器上从 `python3 -m venv` 接着做：

```sh
rsync -a --exclude .venv --exclude data --exclude __pycache__ ./ root@服务器:/opt/hopworld-router/
```

公网地址已知时，可在 `/etc/hopworld-router.env` 加一行 `HOPWORLD_PUBLIC_HOST=你的域名或公网IP`，然后重启服务。管理页里仍可修改。

### 打开管理页

安装提示会区分服务器本机地址、远程 SSH 入口和已配置的公网 HTTPS 管理地址。若 SSH 经过公网端口映射，可在运行检查命令时用 `HOPWORLD_SSH_HOST` 和 `HOPWORLD_SSH_PORT` 指定实际的公网入口。

管理端口不要写入云厂商安全组，也不要在本机防火墙放行。在你自己的电脑上：

```sh
ssh -L 8080:127.0.0.1:8080 root@服务器
```

浏览器打开 `http://127.0.0.1:8080`。新安装以 `admin / admin` 登录，后台会弹窗提示设置新密码。

到这里，订阅汇总已经可用：在管理页添加上游订阅或手动节点，复制订阅地址。该地址默认只有服务器本机能打开。手机要远程拉取订阅，做下面的 HTTPS 步骤。

### 本地网络节点

使用旧版组合式节点时，在管理页启用「本地网络节点」。默认 VLESS Reality 入站是 TCP **8443**。机器已安装 sing-box 且有与公网域名匹配的有效证书时，Hysteria 2 和 TUIC 可通过 UDP **8444** 和 **8445** 启动，默认端口按节点端口计算。管理员按需配置实际使用的入站防火墙和云安全组。QUIC 依赖本机 Xray 回环 SOCKS 转发，该接口仅供同机服务使用，部署在受信任的主机账户环境中。启用后在服务器上确认：

```sh
ss -lntp | grep 8443
```

固定公网 IP 时，管理页公网地址填这个 IP，并关闭「固定域名 + 自动 DNS」和「出口 IP 写进订阅」。用域名时，先把 A 记录指到这台机器，公网地址填域名。

### 手机用 HTTPS 拉取订阅

1. 在「域名与证书」签发或导入与订阅域名匹配、客户端信任的证书。已有域名证书或 Let’s Encrypt 公网 IP 证书可直接复用。Hysteria 2 自动生成的专用自签证书用于节点指纹校验，公网订阅需要受信任的 HTTPS 证书。
2. 在「公网接入 → 公网订阅 → 发布设置」启用 HTTPS 订阅监听，默认端口为 TCP 9443。放行该端口，有 NAT 时将同一个 TCP 端口转发到 Router 主机。容器部署也需要发布此 TCP 端口。TCP 443 可在端口空闲且 Router 具有绑定权限时使用。
3. 在手机关闭 Wi‑Fi 后拉取页面中的地址，确认外网可达。此端口只提供 `/s/` 下的带密钥下载。若要使用本机网络节点，也要单独放行或转发节点自己的入站端口（默认 TCP 8443）。没有可用的公网入站时，使用一个能连回 Router 的公网转发入口。

从独立外网客户端的项目目录运行下列命令，可分别检查解析到的 IPv4 和 IPv6、TCP 80、TCP 443、订阅端口以及 TLS 地址匹配。去掉 `--https-port` 时按 443 验证 HTTPS。Hysteria 2 需要实际的 QUIC 握手，可在探测端准备 Hysteria 二进制，并加上 `--hysteria-link-file /path/to/private-node.txt`。该文件只需保存在探测端。端口超时无法单独确定是主机防火墙、上级路由器、NAT 映射还是运营商拦截。

```sh
python3 -m router.direct_diagnostics router.example.com --https-port 9443
```

双栈域名使用 A 与 AAAA。IPv6-only 入口仅直接服务有 IPv6 连通性的客户端，IPv6 通常需要放行路由器和主机的入站防火墙。网站与 Hysteria 2 可分别使用 TCP 443 和 UDP 443。

同机 Nginx 可以用 [TLS-ALPN 分流示例](deploy/hopworld-nginx-tls-alpn-stream.conf) 统一监听 TCP 443。示例将 TLS 保持到后端，标记为 `acme-tls/1` 的握手交给 TCP 10443，普通 HTTPS 交给 TCP 9443。使用前需确认 Nginx 支持 `stream` 与 `ssl_preread`，将现有 HTTPS 站点移到后端端口并运行 `nginx -t`。若现有站点由 Caddy 或其他服务终结 TLS，也可以让该服务独立管理公开证书。

#### 使用已有 HTTPS 反代

只反代 `/s/`，管理页仍走 SSH 隧道。先把域名 A 记录指到本机，并在安全组放行 TCP 80 和 TCP 443。在管理页填写邮箱，校验方式选 HTTP-01，勾选 Let's Encrypt。请先按服务器的 HTTPS 部署流程配置 nginx、ACME 客户端和校验方式所需端口。证书签发完成后，80 可以继续留着给续期用。

把下面两行追加到 `/etc/hopworld-router.env`，然后 `systemctl restart hopworld-router`：

```
HOPWORLD_PUBLIC_HOST=你的域名
HOPWORLD_SUBSCRIPTION_BASE=https://你的域名
```

后台优先使用 `HOPWORLD_SUBSCRIPTION_BASE` 中配置的 HTTPS 入口生成订阅链接。Router 的独立订阅监听仍使用「发布设置」里的端口。

使用 nginx 时，证书出现在 `/etc/letsencrypt/live/你的域名/` 后，再配置 nginx 的 443 站点：

```sh
sed 's/example.com/你的域名/g' /opt/hopworld-router/deploy/hopworld-nginx-https-subscription.conf > /etc/nginx/conf.d/hopworld-https-subscription.conf
nginx -t && systemctl reload nginx
bash /opt/hopworld-router/deploy/hopworld-install-certbot-renew-hook.sh
```

手机打开管理页里的 `https://你的域名/s/…`。`https://你的域名/` 本身应是 404，管理界面不会从公网打开。

已有 DNS API、不想开 80 时，用下一节的 Webhook 或 DNSPod，管理页校验方式选 DNS-01。DNS-01 不需要放行 80。

### 公网 IP 会变化

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

然后 `systemctl restart hopworld-router`。9876 只监听本机，不要放行。DNS 密钥不会由管理页写入。

### 系统 VPN（IKEv2，可选）

云安全组放行 UDP 500 和 UDP 4500。SSH 不是 40300 时，先改 `/opt/hopworld-router/deploy/hopworld-ikev2-apply` 里两处 `40300`，再在管理页启用。请先由管理员安装 strongSwan，并配置现有 IKEv2 辅助服务和防火墙。

### 更新已有的服务器安装

在「系统设置 → 系统维护」配置 Router 更新和各内核的自动或手动策略。Debian 安装在同一处提供 Router 软件包策略。页面右上角的铃铛可查看更新发现、执行、完成和失败记录。[正式发布、升级和扩展约定](docs/V1版本发布与更新.md)

以下方法适用于上述 `/opt/hopworld-router` Git 安装和 `hopworld-router` systemd 服务。更新前请备份数据目录（默认 `/var/lib/hopworld-router`）和 `/etc/hopworld-router.env`，备份包含节点和订阅凭据，应限制访问；数据库正在使用时应使用 SQLite 在线备份或先停止服务后复制。备份完成并确认服务运行后，以 root 执行：

```sh
bash /opt/hopworld-router/deploy/hopworld-update.sh
```

脚本从 GitHub 远程 `origin` 的 `main` 拉取并仅允许快进；目录有未提交或未跟踪文件时会停止。依赖变化时先建立独立虚拟环境，安装成功后再更新；原虚拟环境保留在 `/var/lib/hopworld-router-updates/previous/`。更新会重启服务，现有连接可能短暂中断。服务启动失败时脚本会报告原版本号，请检查 `journalctl -u hopworld-router` 并使用备份恢复代码和数据。该脚本更新 Router 项目代码及其 Python 依赖。内核安装包由「内核管理」更新，systemd 服务文件及系统软件由各自的部署方式管理。非 Git 安装、Docker 和自定义部署请按各自方式更新，不要直接覆盖现有数据。曾启用旧的通用 root 助手的机器在迁移时应由管理员移除旧授权。

### 从页面管理后台入口、自启动与重启

在「系统设置 → 基本设置」填写空闲端口后点击「验证新管理入口」。使用新入口登录并在三分钟内确认后，管理页面才会切换。原始监听端口继续提供订阅和健康检查。

后台 HTTPS 需要「域名与证书」中的有效证书以及另一个空闲端口。远程使用时，为该端口建立 SSH 本地转发，用证书域名访问，并让该域名在客户端解析到本地转发地址。若重启后无法从已设置的入口登录，可临时设置 `HOPWORLD_MANAGEMENT_RECOVERY=1` 后重启，通过原始端口重新验证并确认访问设置。

已有 Linux systemd 安装可由部署管理员运行 `bash /opt/hopworld-router/deploy/hopworld-install-autostart.sh`，授予页面启用和禁用 Router 服务的权限。该操作管理后续开机行为，当前服务继续运行。macOS LaunchAgent 在页面提供「登录后启动」，容器的重启策略由容器管理器控制。

在「系统设置 → 安全与权限」可以修改管理员账号或密码。保存需要验证当前密码，已登录的管理会话会失效。网页保存的新账号优先于部署时预设的凭据。

标准 `/opt/hopworld-router` 安装可用 `cd /opt/hopworld-router && sudo .venv/bin/python -m router.uninstall` 完整卸载。命令验证当前 Router 管理员密码，并要求输入 `UNINSTALL` 确认永久删除程序、数据库、证书缓存和应用配置。独立配置的 Nginx、Certbot 与云安全组由管理员另行管理。

在 Linux 或 macOS 的管理后台打开「系统设置 → 系统维护 → 重启 Router」。确认后会重新加载当前 Router 程序，发布节点和订阅下载可能短暂中断。全屏等待页在 Router 就绪后自动打开登录页。

### 装完核对

- `systemctl is-active hopworld-router` 为 `active`
- 本机 `curl` 访问 `127.0.0.1:8080` 返回 401
- 安全组没有放行 TCP 8080
- 启用本地节点后，`ss` 能看到 8443，外网能连上该 TCP 端口
- 做了 HTTPS 订阅时，公网能打开订阅 URL，打开 `https://域名/` 得到 404

</details>

<details>
<summary><strong>Linux Docker：容器安装</strong></summary>

### Docker（Linux）

Linux Docker 可使用主机网络，使仅监听回环地址的管理服务可通过宿主机本地或 SSH 隧道访问：

```sh
git clone https://github.com/ZaoHub/HopWorld-Router.git
cd HopWorld-Router
docker build -t hopworld-router .
umask 077
printf '%s\n' 'HOPWORLD_ADMIN_PASSWORD=admin' > router.env
mkdir -p data
docker run -d --name hopworld-router --restart unless-stopped --network host --env-file router.env -v "$(pwd)/data:/data" hopworld-router
docker exec hopworld-router xray version
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/
```

使用初始账号和密码 `admin` 从本机登录并设置新密码。最后一条命令返回 `401` 表示接口已启动且需要认证。镜像包含 Xray 和 curl，供节点测试及本地网络节点使用。IKEv2、证书自动签发和 DNS Webhook 的宿主机部署步骤不适用于此容器，需另行配置。此方式依赖 Linux 的主机网络。Docker Desktop 和 NAS 的端口映射及凭据配置见各自的部署指南。

</details>

<details>
<summary><strong>macOS / Windows：Docker Desktop 试用</strong></summary>

### Docker Desktop 试用

可参考 [Docker Desktop 试用指南](docs/Docker-Desktop试用指南.md)，使用独立的 Compose 配置将管理端口映射到本机。镜像自带 Xray；不要在容器里做 Linux 的 root 授权。该方案尚未在 Docker Desktop 实测。

</details>

<details>
<summary><strong>Windows：WSL 2 试用</strong></summary>

### Windows：WSL 2 试用

可在 Windows 的 Ubuntu WSL 2 环境中参考 [WSL 2 试用指南](docs/Windows-WSL2试用指南.md)，手动启动或试用用户级 systemd 服务。该方式以当前用户运行，管理页不会自动安装系统组件。它运行的是 Linux 版本，不是 Windows 原生安装包；尚未在 WSL 2 实测。

</details>

<details>
<summary><strong>Windows 原生：订阅管理试用</strong></summary>

### Windows 原生试用

可参考 [Windows 原生试用指南](docs/Windows原生试用指南.md)，使用 Python 手动启动订阅管理。此方案尚未在 Windows 实测；管理页不会安装 Xray，本地网络节点、后台服务及系统集成尚未完成。

</details>

<details>
<summary><strong>OpenWrt：原生试用准备</strong></summary>

### OpenWrt 原生试用

已准备实验性的 procd 服务模板及 [OpenWrt 试用指南](docs/OpenWrt试用指南.md)，仅覆盖订阅管理。Linux 的组件授权不适用于 OpenWrt。尚无安装包，且未在 OpenWrt 设备上验证；透明网关、IKEv2 和其他系统集成不在此方案范围内。

</details>

<details>
<summary><strong>NAS：支持状态</strong></summary>

NAS 适配计划包括群晖、威联通、极空间、绿联 NAS（UGOS Pro）和飞牛私有云（fnOS）；不同设备的容器网络和系统环境各不相同，Linux Docker 教程不能保证直接适用，尚需在对应设备上验证。Docker Desktop 试用方案也需要单独验证。

绿联和飞牛可参考 [NAS 容器试用指南](docs/NAS容器试用指南.md)，该指南尚未在对应设备上实测。容器镜像自带 Xray；不要在 NAS 上执行 Linux 的 root 授权。

</details>

## 测试

```sh
.venv/bin/python -m unittest discover -s tests -v
```

当前测试覆盖订阅合并、认证、更新失败保留旧结果、密钥轮换等基础行为；未验证真实上游订阅、Linux systemd 或 Docker 的完整部署。

## 后续部署目标

后续按 Linux 基础验证、NAS 与 ARM 设备、Windows WSL 2 和桌面容器、原生系统服务、OpenWrt 的顺序逐步适配。各平台的工作步骤、功能范围和验收条件见 [运行环境适配路线图](docs/运行环境适配路线图.md)。路线图是开发计划，不代表相应环境已支持或通过测试。

## 许可证

源码采用 [MIT License](LICENSE)。代理内核及其他第三方组件遵循各自的许可证，分发时需保留相应版权和许可文件。

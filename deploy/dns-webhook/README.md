# DNS Webhook（Cloudflare / 阿里云 / Namesilo / …）

HopWorld Router 在 **`HOPWORLD_DDNS_PROVIDER=webhook`** 时，会向你的 URL `POST` JSON，由你在 Webhook 里调用各厂商 API 改解析记录。本目录提供 **本机侧车服务**，已对接常用厂商。

## Router 发来的 JSON

### 固定域名 + 自动 DNS（A/AAAA）

```json
{
  "hostname": "node.example.com",
  "ipv4": "8.8.8.8",
  "ipv6": null
}
```

### Let's Encrypt DNS-01（TXT）

```json
{
  "action": "acme_txt",
  "phase": "publish",
  "hostname": "jd.example.com",
  "sub_domain": "_acme-challenge.jd",
  "zone": "example.com",
  "txt": "xxxxxxxx"
}
```

清理阶段 `phase` 为 `clear`，无 `txt`。

同一 URL 可同时处理 DDNS 与 ACME（本服务按 `action` 分支）。

## 安装（VPS 本机）

```bash
# 1. 编辑 /etc/hopworld-dns-webhook.env（示例见下）
# 2. 安装并启动
sudo bash deploy/hopworld-install-dns-webhook.sh
```

Router 环境（`/etc/hopworld-router.env`）：

```bash
HOPWORLD_DDNS_PROVIDER=webhook
HOPWORLD_DDNS_WEBHOOK_URL=http://127.0.0.1:9876/
# 证书也用同一 Webhook 时可不设；单独 URL 时用：
# HOPWORLD_ACME_DNS_WEBHOOK_URL=http://127.0.0.1:9876/
```

管理页勾选 **固定域名 + 自动 DNS**，公网地址填完整主机名（与解析记录一致）。

## 厂商环境变量

### Cloudflare（推荐）

```bash
HOPWORLD_DNS_BACKEND=cloudflare
CF_API_TOKEN=你的_API_Token   # 权限：Zone - DNS - Edit
# 可选，省略则按 zone 名自动查：
# CF_ZONE_ID=xxxxxxxx
```

Token 在 Cloudflare 控制台 → 我的个人资料 → API 令牌。

### 阿里云云解析（Alidns）

```bash
HOPWORLD_DNS_BACKEND=aliyun
ALIYUN_ACCESS_KEY_ID=...
ALIYUN_ACCESS_KEY_SECRET=...
# ALIYUN_DNS_ENDPOINT=https://alidns.aliyuncs.com/
```

RAM 用户需 `AliyunDNSFullAccess` 或最小化 `AddDomainRecord` / `UpdateDomainRecord` / `DeleteDomainRecord` / `DescribeDomainRecords`。

### Namesilo

```bash
HOPWORLD_DNS_BACKEND=namesilo
NAMESILO_API_KEY=...
```

### 华为云

华为云 DNS 各区域 endpoint 与签名与阿里云不同，**本仓库未内置完整实现**。可选：

1. 使用 **Cloudflare / 阿里云** 托管该域名；或
2. 自建一小段 **函数 / 中间层**，接收上面 JSON，调[华为云 DNS API](https://console.huaweicloud.com/apiexplorer/#/openapi/DNS/doc)；或
3. 设置 `HOPWORLD_DNS_BACKEND=huawei` 仅作占位（会返回说明性错误，便于你发现需自建）。

## 安全

- 默认只监听 **127.0.0.1:9876**，仅本机 Router 可访问。
- 若经 Nginx 反代到公网，务必设置：

```bash
HOPWORLD_DNS_WEBHOOK_SECRET=随机长字符串
```

并在 Nginx 或 Router 侧附加 Header `X-HopWorld-Secret`（Router 当前未带 Header，**不要**把 Webhook 暴露公网；仅本机即可）。

## 手动测试

```bash
curl -sS -X POST http://127.0.0.1:9876/ \
  -H 'Content-Type: application/json' \
  -d '{"hostname":"node.example.com","ipv4":"8.8.8.8"}'
```

```bash
curl -sS http://127.0.0.1:9876/health
```

## 与 DNSPod 的关系

| 方式 | 配置 |
|------|------|
| DNSPod 原生 | `HOPWORLD_DDNS_PROVIDER=dnspod` + `HOPWORLD_DDNS_ID` / `TOKEN` |
| 其它厂商 | `webhook` + 本服务或自建接口 |

固定 IP、手动 A 记录时，**无需** Webhook，也不要勾选「固定域名 + 自动 DNS」。

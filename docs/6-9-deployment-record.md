# 6-9 服务器部署记录

> 将舆情分析 Web 服务通过 Docker 部署到 Azure 服务器，以 `opinion.sytssmys.top` 二级域名对外暴露。全流程从零到公网可用约 **5 分钟**。

---

## 环境信息

| 项目 | 值 |
|------|-----|
| 服务器 | Azure VM (Ubuntu 22.04) |
| Docker | v28.2.2 |
| compose | v2.24.1 (docker-compose 独立版本) |
| 隧道 | Cloudflare Tunnel (`my-memos-tunnel`) |
| 反代 | Caddy (监听 :80) |
| 外部域名 | `opinion.sytssmys.top` |
| 宿主机端口 | `8502` (Docker 映射 `8502:8501`) |

---

## 部署流程

### Phase 0：前置检查

```bash
$ docker --version
Docker version 28.2.2, build 28.2.2-0ubuntu1~22.04.1

$ docker-compose --version
Docker Compose version v2.24.1

$ systemctl is-active cloudflared && systemctl is-active caddy
active
active

$ ss -tlnp | grep :8502 || echo "8502 可用"
8502 可用
```

所有前置条件就绪。

### Phase 1：准备

#### 1a. DNS 解析

```bash
$ cloudflared tunnel route dns my-memos-tunnel opinion.sytssmys.top
2026-06-09T07:07:51Z INF Added CNAME opinion.sytssmys.top which will route to this tunnel tunnelID=7fb29dfb-ce9d-401b-bc4c-f16e7505a86c
```

Cloudflare 上自动创建 `opinion.sytssmys.top → my-memos-tunnel` CNAME 记录。

#### 1b. 构建 Docker 镜像

```bash
$ docker-compose build
```

Azure 国外网络直连 Docker Hub / PyPI，依赖下载飞快：

| 阶段 | 耗时 |
|------|------|
| 拉取 python:3.12-slim | 2.5s |
| pip install uv | 5.1s |
| uv sync (56 个包) | 5.1s |
| COPY . . | 0.2s |
| **总计** | **~27s** |

关键对比：本地 WSL + 代理网络构建同样镜像因网络瓶颈需数分钟甚至失败。

### Phase 2：配置入口链路

#### 2a. Cloudflare Tunnel ingress

编辑 `/etc/cloudflared/config.yml`，在 `rag.sytssmys.top` 后插入第 4 条规则：

```yaml
  - hostname: opinion.sytssmys.top
    service: http://localhost:80
```

```bash
$ sudo systemctl restart cloudflared
$ journalctl -u cloudflared -n 5 --no-pager
Jun 09 07:09:35 my-memos-server systemd[1]: Started cloudflared.
```

#### 2b. Caddy 反代

编辑 `/etc/caddy/Caddyfile`，新增：

```
# For Public-Opinion-Analysis
http://opinion.sytssmys.top {
    reverse_proxy 127.0.0.1:8502
}
```

```bash
$ sudo caddy validate --config /etc/caddy/Caddyfile
Valid configuration

$ sudo systemctl reload caddy
Caddy reloaded OK
```

### Phase 3：启动服务

```bash
$ docker-compose up -d
 Network public-opinion-analysis_default  Created
 Container public-opinion-analysis-web  Started

$ docker-compose ps
NAME                          IMAGE                                STATUS        PORTS
public-opinion-analysis-web   public-opinion-analysis-web:latest   Up 4 seconds  0.0.0.0:8502->8501/tcp

$ docker-compose logs --tail 5
public-opinion-analysis-web  | Uvicorn server started on 0.0.0.0:8501
public-opinion-analysis-web  | Local URL: http://localhost:8501
public-opinion-analysis-web  | Network URL: http://172.19.0.2:8501
public-opinion-analysis-web  | External URL: http://20.27.222.98:8501
```

### Phase 4：验证

#### 本地验证

```bash
$ curl -s -H "Host: opinion.sytssmys.top" http://localhost:80 | head -3
<!-- Copyright (c) Streamlit Inc. (2018-2022) Snowflake Inc. ...
<!-- Licensed under the Apache License, Version 2.0... -->

$ curl -s http://127.0.0.1:8502 | head -3
<!-- Copyright (c) Streamlit Inc. ... -->
```

Caddy 反代链路和 Docker 直连均正常。

#### 公网验证

```bash
$ curl -s -o /dev/null -w "HTTP %{http_code}" https://opinion.sytssmys.top
HTTP 200

$ nslookup opinion.sytssmys.top
Name:   opinion.sytssmys.top
Address: 104.21.71.178
Address: 172.67.147.245
```

DNS 解析到 Cloudflare 边缘节点，HTTPS 返回 200，服务公网可用。

---

## 最终架构

```
用户 → Cloudflare (HTTPS) → Cloudflare Tunnel → localhost:80 (Caddy) → 127.0.0.1:8502 (Docker) → Streamlit (:8501)
```

## 对比：本地 vs 服务器 Docker 构建

| 环节 | 本地 (WSL + 代理) | Azure 服务器 |
|------|-------------------|--------------|
| Docker daemon 外网访问 | 需代理配置 + 重启 | 直连 |
| 拉取 python:3.12-slim | 超时风险 | 2.5s |
| uv sync 下载大包 | 分钟级 | 4.3s |
| 总构建时长 | 不稳定 (数分钟或失败) | **27s** |
| Streamlit 端口 | 8501 (无冲突时) | 8502 (避让已有服务) |

## 涉及配置文件

| 文件 | 变更 |
|------|------|
| `/etc/cloudflared/config.yml` | ingress 新增 `opinion.sytssmys.top → localhost:80` |
| `/etc/caddy/Caddyfile` | 新增 `http://opinion.sytssmys.top → 127.0.0.1:8502` |
| `public-opinion-analysis/.env` | 新增，填入 `LLM_API_KEY` 等环境变量 |

## 回滚方式

```bash
# 停止容器
docker-compose down

# 移除 DNS 记录
cloudflared tunnel route dns --overwrite-dns my-memos-tunnel opinion.sytssmys.top <new-target>

# 恢复 cloudflared 配置
# → 删除 ingrss 中的 opinion 行，systemctl restart cloudflared

# 恢复 Caddy 配置
# → 删除 Caddyfile 中的 opinion 块，systemctl reload caddy
```

---

本部署全程由 [opencode](https://opencode.ai) 协作完成。

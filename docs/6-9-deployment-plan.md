# 6-9 服务器部署计划

> 将舆情分析 Web 服务（Docker）部署到现有服务器，通过 Cloudflare Tunnel + Caddy 反代对外暴露。

---

## 当前架构

```
用户 → Cloudflare (HTTPS) → Cloudflare Tunnel → localhost:80 (Caddy) → 后端服务
```

### 现有服务一览

| 域名 | 途径 | 目标端口 | 服务 |
|------|------|----------|------|
| `sytssmys.top` | Tunnel → Caddy | `5230` | Memos |
| `memos.sytssmys.top` | Tunnel → Caddy | `5230` | Memos |
| `rag.sytssmys.top` | Tunnel → Caddy | `8501` | RAG |
| `ssh.sytssmys.top` | Tunnel 直通 | `22` | SSH |
| **`opinion.sytssmys.top`** **(新增)** | Tunnel → Caddy | **`8502`** | **舆情分析 (Docker)** |

---

## 部署流程

### Phase 0：前置检查

```bash
# 确认 Docker 可用
docker --version
docker compose version

# 确认 cloudflared 和 caddy 运行中
systemctl is-active cloudflared
systemctl is-active caddy

# 确认 8502 端口未被占用
ss -tlnp | grep :8502 || echo "8502 可用"
```

### Phase 1：准备（可并行）

#### 1a. 创建 .env

```bash
cp .env.example .env
```

编辑 `.env`，填入真实的 `LLM_API_KEY`，其余沿用默认：

```env
LLM_API_KEY=sk-xxx...
LLM_BASE_URL=https://opencode.ai/zen/go/v1
LLM_MODEL=deepseek-v4-flash
```

#### 1b. 新增 Cloudflare DNS 解析

```bash
cloudflared tunnel route dns my-memos-tunnel opinion.sytssmys.top
```

自动在 Cloudflare 上创建 CNAME 记录 `opinion.sytssmys.top → my-memos-tunnel`。

#### 1c. 构建 Docker 镜像

```bash
docker compose build
```

构建约需 1-3 分钟（主要耗时在下载 pyarrow、pandas、streamlit 等大包）。

---

### Phase 2：配置入口链路

#### 2a. Cloudflare Tunnel 添加 ingress 规则

编辑 `/etc/cloudflared/config.yml`，在 `rag.sytssmys.top` 之后插入：

```yaml
  # 4. 舆情分析
  - hostname: opinion.sytssmys.top
    service: http://localhost:80
```

完整文件（末尾 `http_status:404` 不变）：

```yaml
tunnel: 7fb29dfb-ce9d-401b-bc4c-f16e7505a86c
credentials-file: /etc/cloudflared/7fb29dfb-ce9d-401b-bc4c-f16e7505a86c.json

ingress:
  - hostname: memos.sytssmys.top
    service: http://localhost:80
  - hostname: ssh.sytssmys.top
    service: ssh://localhost:22
  - hostname: rag.sytssmys.top
    service: http://localhost:80
  - hostname: opinion.sytssmys.top
    service: http://localhost:80
  - service: http_status:404
```

重启 cloudflared：

```bash
systemctl restart cloudflared
journalctl -u cloudflared -n 20 --no-pager   # 确认无报错
```

#### 2b. Caddy 添加反向代理

编辑 `/etc/caddy/Caddyfile`，在 `rag.sytssmys.top` 块之后追加：

```
# For Public-Opinion-Analysis
http://opinion.sytssmys.top {
    reverse_proxy 127.0.0.1:8502
}
```

重载 Caddy：

```bash
systemctl reload caddy
caddy validate --config /etc/caddy/Caddyfile   # 语法验证
```

---

### Phase 3：启动服务

```bash
docker compose up -d
docker compose ps                       # 确认状态
docker compose logs -f --tail 50        # 查看启动日志
```

本地验证：

```bash
curl -s -H "Host: opinion.sytssmys.top" http://localhost:80 | head -20
curl -s http://127.0.0.1:8502 | head -20
```

两条命令均应返回 Streamlit 页面 HTML。

---

### Phase 4：公网验证

浏览器访问 `https://opinion.sytssmys.top`：
1. 显示 Streamlit 页面
2. CSV 上传 / 手动输入功能可用
3. 下载 CSV / JSONL / XLSX 正常
4. `docker compose logs` 确认 LLM API 调用成功

---

## 完整操作速查

```bash
# Phase 1
cp .env.example .env && vim .env
cloudflared tunnel route dns my-memos-tunnel opinion.sytssmys.top
docker compose build

# Phase 2 — 编辑 /etc/cloudflared/config.yml 和 /etc/caddy/Caddyfile
systemctl restart cloudflared
systemctl reload caddy

# Phase 3
docker compose up -d
```

---

## 回滚方案

| 步骤 | 回滚操作 |
|------|----------|
| DNS 记录 | `cloudflared tunnel route dns --overwrite-dns my-memos-tunnel opinion.sytssmys.top <其他目标>` 或 Cloudflare Dashboard 删除 |
| cloudflared ingress | 注释/删除配置中对应行 → `systemctl restart cloudflared` |
| Caddy 反代 | 注释/删除 Caddyfile 对应块 → `systemctl reload caddy` |
| Docker 容器 | `docker compose down` 或 `docker compose stop` |

---

## 注意事项

1. **端口 8502**：确认未被占用；Caddy 通过 localhost 反代，安全组无需对外开放此端口
2. **`.env` 安全**：密钥文件已受 `.gitignore` 和 `.dockerignore` 保护
3. **构建超时**：如国外源下载慢，使用国内 PyPI 镜像：
   ```bash
   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
   UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
   docker compose build
   ```
4. **重载 vs 重启**：Caddy 用 `reload`（热更新不断连接），cloudflared 用 `restart`

# 6-9 Docker 实现记录与本地启动问题

## 一、当前 Docker 实现

已新增 3 个 Docker 相关文件：

- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

当前目标是支持后续在服务器上从 GitHub 拉代码后构建并启动 Streamlit Web 服务。

### 端口策略

容器内部仍使用 Streamlit 默认服务端口 `8501`：

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

宿主机映射到 `8502`：

```yaml
ports:
  - "8502:8501"
```

原因：服务器已有服务占用宿主机 `8501`，使用 `8502:8501` 可以避免冲突，同时不需要修改应用内部端口。

### 环境变量

`docker-compose.yml` 使用：

```yaml
env_file:
  - .env
```

`.env` 不会被复制进镜像，而是在容器启动时注入环境变量。这样可以避免把真实 `LLM_API_KEY` 打进镜像或提交到 GitHub。

注意：`docker compose config` 会展开并显示 `.env` 中的真实值，不应把该命令输出贴到公开环境。

### 依赖安装

`Dockerfile` 使用官方基础镜像：

```dockerfile
FROM python:3.12-slim
```

依赖安装流程：

```dockerfile
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
```

`uv sync` 会在镜像内创建项目虚拟环境 `.venv`。因此启动命令使用：

```dockerfile
CMD [".venv/bin/streamlit", "run", "app.py", ...]
```

### 可选 PyPI 源

`Dockerfile` 支持构建参数：

```dockerfile
ARG PIP_INDEX_URL
ARG UV_INDEX_URL
```

`docker-compose.yml` 中透传：

```yaml
args:
  PIP_INDEX_URL: ${PIP_INDEX_URL:-}
  UV_INDEX_URL: ${UV_INDEX_URL:-}
```

默认不设置源，适配国外服务器。国内本地构建可临时使用：

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
docker compose build
```

## 二、COPY 与挂载的取舍

当前采用 `COPY . .`，即构建时把项目代码复制进镜像。

生产/服务器部署建议使用 COPY：

- 镜像自包含，服务器上只需构建并启动
- 代码版本和镜像绑定，便于复现和回滚
- 不依赖宿主机目录结构
- 避免运行时挂载导致的路径、权限和版本混乱

本地开发调试可以考虑单独增加 `docker-compose.dev.yml` 做挂载：

```yaml
volumes:
  - .:/app
```

但这只适合开发热更新，不建议作为服务器部署方案。

## 三、data 目录处理

Web 应用本身不依赖本地 `data/`：

- CSV 上传由浏览器传入
- 手动输入由用户在页面填写
- LLM 抽取只依赖输入内容和 `.env` 中的 API 配置

当前 `.dockerignore` 已排除大原始数据：

```dockerignore
data/test_news_0603.csv
```

这样构建上下文从约 `156MB` 降到约 `669KB`。

后续如果确认容器内不需要任何测试样例，可进一步改成：

```dockerignore
data/
```

这样三个 `upload_sample_*.csv` 也不会进入镜像。

## 四、本地构建遇到的问题

### 1. shell 代理不等于 Docker daemon 代理

本地 shell 中：

```bash
curl -I https://www.google.com
curl -I https://registry-1.docker.io/v2/
```

在提权网络下可以成功，说明 WSL shell 代理可用。

但 Docker 拉基础镜像由 Docker daemon 发起。最初检查到：

```text
HTTPProxy=
HTTPSProxy=
```

因此 `docker compose build` 拉取官方 Python 镜像时报错：

```text
failed to resolve source metadata for docker.io/library/python:3.12-slim
i/o timeout
```

后来通过 systemd drop-in 给 Docker daemon 配置代理：

```ini
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7897"
Environment="HTTPS_PROXY=http://127.0.0.1:7897"
Environment="NO_PROXY=localhost,127.0.0.1,::1,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16"
```

并执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart docker
```

之后 `docker info` 显示 Docker daemon 已读到代理，官方基础镜像元数据可以获取。

### 2. Docker build 内部下载依赖仍然很慢

即使 Docker daemon 代理可用，构建容器内执行：

```dockerfile
RUN pip install --no-cache-dir uv
RUN uv sync --frozen --no-dev
```

仍会从零下载依赖。它不会复用宿主机 `.venv` 中已安装的包。

本项目 Web 依赖中包含较大的 wheel：

- `pyarrow`
- `pandas`
- `numpy`
- `streamlit`
- `pydeck`

因此本地构建长时间卡在下载阶段。这个问题是本地 Docker 构建网络和包下载速度问题，不是 Streamlit 应用代码问题。

### 3. 国内镜像源也不一定稳定

尝试通过构建参数使用清华 PyPI 源后：

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
docker compose build
```

`pip install uv` 可以推进，但 `uv sync` 下载 `pyarrow/pandas/numpy/streamlit` 等大包时仍出现长时间等待/重复下载日志。

因此当前建议：服务器部署优先使用官方源；本地构建如果网络不稳定，不要把它误判为应用或 Dockerfile 设计错误。

## 五、当前建议

服务器部署建议：

```bash
git clone <repo-url>
cd public-opinion-analysis
cp .env.example .env
# 填写 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
docker compose build
docker compose up -d
```

访问：

```text
http://<server-host>:8502
```

本地调试建议：

- 继续直接用 `.venv/bin/streamlit run app.py ...` 调 Web 功能
- Docker 配置保留为服务器部署方案
- 若必须本地验证 Docker，优先确认 Docker daemon 代理和 Python 包下载速度

当前 Docker 配置方向：

- 生产使用 COPY，不挂载代码
- 不挂载 `data/`
- `.env` 仅运行时注入
- 宿主机端口固定为 `8502`

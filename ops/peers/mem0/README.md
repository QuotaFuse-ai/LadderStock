# Mem0 部署资产（qf-mem0，随包分发）

LadderStock 助手跨会话记忆的 mem0 实例：WSL docker compose，独立端口 9100（API）
/ 3200（dashboard）/ 8532（postgres）。与 Multica 自己的 mem0（9000）物理隔离，
绝不允许互相连接。

## 文件

- `docker-compose.yml`：compose 定义（project `qf-mem0`，容器 `qf-mem0-*`）。
- `init-db.sh`：postgres 初始化（建 `mem0_app` 应用库；pgvector 记忆存默认库）。
- `.env.example`：`.env` 模板；安装器生成 `.env` 时自动填随机 `POSTGRES_PASSWORD`
  与 `JWT_SECRET`。`.env` 不进 git、不进日志。
- `server/`：mem0 OSS server 源码 + `Dockerfile`（独立构建，不再依赖 mem0-dev
  基镜像）与历史 `Dockerfile.patch`（需要叠加 mem0-dev 产物时用）。

## 安装（控制面「一键安装」）

控制面 mem0 卡的安装动作即本目录的部署流程：

1. 预检：WSL 发行版、docker 可用、端口 9100/3200/8532 空闲、机器级
   `LDST_LLM_API_KEY` 已配置——任一缺失给中文指引，不静默跳过。
2. 落资产：把本目录复制到 peer 目录（`LDST_PEER_DIR_MEM0`，缺省
   `<peer 根>/Mem0`），基于 `.env.example` 生成 `.env`。
3. 构建/启动：`wsl docker compose -f <peer目录>/docker-compose.yml up -d -d`
   （本地 build `server/`；首次构建需下载模型，经 `HF_ENDPOINT=hf-mirror.com`）。
4. 验活：`curl 127.0.0.1:9100/configure` 200 且集合名 = `ladderstock_memories`。

## 已知坑（边界记录在案）

- 本机 WSL docker bridge DNS 故障：`docker build` / 临时容器需 `--network host`。
- embedder 必须是本地 huggingface `BAAI/bge-small-zh-v1.5`（512 维，main.py 硬编码）；
  LLM 走云骏 `deepseek-v4.1-flash-1017`，API key 只从机器级 `LDST_LLM_API_KEY` 透传
  （WSLENV），不进 yaml / .env / 日志 / git。

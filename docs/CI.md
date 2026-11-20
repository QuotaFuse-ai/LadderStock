# CI 门禁：实际命令、本地等价命令与 required checks

本文件描述 `.github/workflows/ci.yml` 的真实行为，以及在本机复现同一门禁的命令。
最后核对：**2026-10-31**（`main@128b414`，CI 绿）。

## 一、CI 实际跑什么

触发：`pull_request` + `push` 到 `main`。同一 ref 的新运行会取消旧运行（`concurrency.cancel-in-progress`）。

权限：`permissions: contents: read`。只使用仓库内置的 `GITHUB_TOKEN`，**不需要配置任何 secret**。

| Job（同时是 check 名） | Runner | 步骤 |
| --- | --- | --- |
| `backend (pytest / ruff / mypy)` | `ubuntu-latest`，Python 3.11 | `pip install -e ".[all,dev]"` → `pytest` → `ruff` → `mypy` |
| `frontend (tsc / vitest / build)` | `ubuntu-latest`，Node 20，工作目录 `frontend/` | `npm ci` → `tsc --noEmit` → `vitest run` → `vite build` |

后端三个步骤的原始命令（与 workflow 逐字一致）：

```bash
python -m pip install -e ".[all,dev]"
python -m pytest -o addopts=""
python -m ruff check .
python -m mypy
```

`-o addopts=""` 是刻意覆盖 `pyproject.toml` 里的 `addopts = "-q"`，让 CI 输出完整用例结果；本地复现时不要省略。

## 二、本地等价命令

前置：Python ≥ 3.11（本仓开发环境固定 3.12）；Node ≥ 22 仅前端构建需要。

### Windows（PowerShell / cmd）

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[all,dev]"

.\.venv\Scripts\python.exe -X utf8 -m pytest -o addopts=""
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
```

### macOS / Linux

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[all,dev]"

./.venv/bin/python -m pytest -o addopts=""
./.venv/bin/python -m ruff check .
./.venv/bin/python -m mypy
```

venv 解释器路径差异：Windows 是 `.venv\Scripts\python.exe`，POSIX 是 `.venv/bin/python`。上面直接用解释器全路径，避免 `activate` 后 `python` 指向错环境。

### 前端

```bash
cd frontend
npm ci
npx tsc --noEmit
npx vitest run
npx vite build
```

以上是 **CI 的写法**：runner 网络正常，`npx` 直接命中已安装的本地 bin。

**在本地跑请改用 `npm test` / `node_modules\.bin\*`，不要用 `npx`**。`npx` 会先联网做一次
npm registry 的包解析与版本检查，在慢网络（VPN 全局代理）下这一步可能长时间卡住，
表现就是命令停在 banner 之后没有任何输出，容易被误判成「进程挂了 / 端口采集超时」：

```powershell
cd frontend
$env:NO_PROXY="*"; $env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; $env:CI="1"   # 本地回环不走代理；CI=1 关掉交互式 watch
node_modules\.bin\tsc.cmd --noEmit
node_modules\.bin\vitest.cmd run          # 全量约 3 分钟；单文件 7–8 秒
node_modules\.bin\vite.cmd build
```

`$env:CI="1"` 同样必要：没有它 `vitest` 会进入交互式 watch，看起来像卡死。

CI 固定 Node 20；本机 Node 22.17.1 实测同样通过。`package-lock.json` 已提交，`npm ci` 不需要再生成锁文件。

### 本机实测（2026-10-31，Windows 10 / Python 3.12.10 / Node 22.17.1）

- `pytest`：1108 passed, 5 skipped, 4 xpassed
- `ruff check .`：All checks passed
- `mypy`：Success, no issues in 75 source files
- 前端：`tsc`、`vitest`（52 passed）、`vite build` 全部通过

测试不依赖网络与 API Key，全部走本地 fixture / offline provider。

## 三、required checks 现状

**当前未设为 required，仅为参考。**

核对方式（2026-10-31，公开 API）：

- `GET /repos/QuotaFuse-ai/LadderStock/branches/main` → `"protected": false`
- `GET /repos/QuotaFuse-ai/LadderStock/rulesets` → `[]`

即 main 既没有 branch protection，也没有 repository ruleset。CI 会跑并给出红绿信号，但**不会阻止合并**；要获得强制力，需要仓库管理员在 Settings → Branches（或 Rulesets）里配置。

若管理员启用保护，应把下面两个 check 设为 required，名称需逐字一致：

- `backend (pytest / ruff / mypy)`
- `frontend (tsc / vitest / build)`

配置后本文件需同步更新为实际值。

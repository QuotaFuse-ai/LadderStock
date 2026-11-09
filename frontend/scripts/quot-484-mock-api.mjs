#!/usr/bin/env node
/**
 * QUOT-484 截图取证用的本机替身 API（只用于 `docs/` 截图，不进产品构建）。
 *
 * 482-B（adapters 接线）未落地时，真实后端不会把停止清单喂给页面；这里按
 * `GET /api/supervisor/state` 的**真实响应形状**（`src/ladderstock/web/control.py::
 * build_supervisor_router` + `supervisor/selfheal.py::snapshot`）回放三种场景：
 *
 *   SCENARIO=dialog   本程序启动 + 外部接管同屏（弹层）
 *   SCENARIO=cards    只显示卡片：三档归属 + 自愈 gave_up（标红）+ healing（第 N/3 次）
 *   SCENARIO=empty    全部本程序启动（不弹冗余确认）
 *
 * 前端仍然走真实 `fetch('/api/supervisor/state')`，脚本不注入任何前端代码。
 *
 * 用法：
 *   node scripts/quot-484-mock-api.mjs [--port 5961] [--dump]
 *   --dump 只打印 supervisor JSON（取证用），不起服务。
 */

import { createReadStream, existsSync, statSync } from 'node:fs'
import { createServer } from 'node:http'
import { dirname, extname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const DIST = resolve(HERE, '..', 'dist')

const argv = process.argv.slice(2)
const portFlag = argv.indexOf('--port')
const PORT = portFlag >= 0 ? Number(argv[portFlag + 1]) : 5961
const SCENARIO = process.env.SCENARIO ?? 'dialog'

const BOARD = {
  ok: true,
  control_enabled: true,
  peers: [
    {
      id: 'tickhub',
      product: 'TickHub-CN',
      role: '数据底座 · A 股',
      directory: 'C:\\workplace\\TickHub-CN',
      directory_exists: true,
      registered: true,
      status: 'running',
      status_label: '运行中',
      detail: '端口已监听，健康检查通过',
      installed: true,
      version: '0.9.4',
      venv: 'C:\\workplace\\TickHub-CN\\.venv',
      ports: { mcp: 8000, web: 8787, web_label: '管理端' },
      listening: { mcp: [{ pid: 4500, process: 'python.exe' }], web: [{ pid: 8336, process: 'python.exe' }] },
      health: { ok: true, detail: 'HTTP 200' },
      dist: { wheel: null, editable: true, env_file: '' },
      port_conflicts: [],
      last_job: null,
      open_url: 'http://127.0.0.1:8787',
      actions: { can_install: false, can_start: false, can_stop: true, can_open: true, can_config: true },
    },
    {
      id: 'wire',
      product: 'QuotaFuse Wire',
      role: '资讯感知层',
      directory: 'C:\\workplace\\Wire\\quotafuse-wire',
      directory_exists: true,
      registered: true,
      status: 'running',
      status_label: '运行中',
      detail: '端口已监听，健康检查通过',
      installed: true,
      version: '0.9.1',
      venv: 'C:\\workplace\\Wire\\quotafuse-wire\\.venv',
      ports: { mcp: 8791, web: 8790, web_label: 'HTTP' },
      listening: { mcp: [{ pid: 6988, process: 'python.exe' }], web: [{ pid: 2708, process: 'python.exe' }] },
      health: { ok: true, detail: 'HTTP 200' },
      dist: { wheel: null, editable: true, env_file: '' },
      port_conflicts: [],
      last_job: null,
      open_url: 'http://127.0.0.1:8790',
      actions: { can_install: false, can_start: false, can_stop: true, can_open: true, can_config: true },
    },
    {
      id: 'senate',
      product: 'StockSenate',
      role: '合议层',
      directory: 'C:\\workplace\\StockSenate',
      directory_exists: true,
      registered: true,
      status: 'running',
      status_label: '运行中',
      detail: '端口已监听，健康检查通过',
      installed: true,
      version: '0.8.2',
      venv: 'C:\\workplace\\StockSenate\\.venv',
      ports: { mcp: 8765, web: 8786, web_label: 'WebUI' },
      listening: { mcp: [{ pid: 8312, process: 'python.exe' }], web: [{ pid: 8320, process: 'python.exe' }] },
      health: { ok: true, detail: 'HTTP 200' },
      dist: { wheel: null, editable: true, env_file: '' },
      port_conflicts: [],
      last_job: null,
      open_url: 'http://127.0.0.1:8786',
      actions: { can_install: false, can_start: false, can_stop: true, can_open: true, can_config: true },
    },
    {
      id: 'financecoffer',
      product: 'FinanceCoffer',
      role: '数据存储：宽表 / 公告检索 / 风险（WSL，只读观测）',
      directory: 'C:\\workplace\\FinanceCoffer',
      directory_exists: true,
      registered: true,
      status: 'failed',
      status_label: '异常',
      detail: '进程连续崩溃 3 次，自愈已停止',
      installed: true,
      version: '0.7.0',
      venv: null,
      ports: { mcp: 8100, web: null, web_label: 'HTTP 服务（WSL）' },
      listening: { mcp: [], web: [] },
      health: { ok: false, detail: '连接被拒绝' },
      dist: { wheel: null, editable: false, env_file: '' },
      port_conflicts: [],
      last_job: null,
      open_url: null,
      actions: { can_install: false, can_start: true, can_stop: false, can_open: false, can_config: true },
    },
  ],
  registry: [],
  components: [],
  port_conflicts: [],
  ports: { board: 5950, mcp: 5951 },
}

function module(overrides) {
  return {
    ports: [8000],
    pid: 101,
    health: 'ok',
    state: 'running',
    restarts_5min: 0,
    restart_count: 0,
    stopping_by_user: false,
    origin: 'spawned',
    cmdline_hash: 'a1b2c3d4',
    last_exit_reason: null,
    self_heal: { attempts: 0, max: 3, status: 'ok' },
    ownership: 'managed',
    evidence: 'ledger',
    listening: true,
    ...overrides,
  }
}

const MODULES = {
  dialog: [
    module({
      module: 'tickhub',
      pid: 4500,
      ports: [8000, 8787],
      origin: 'spawned',
      cmdline: 'C:\\workplace\\TickHub-CN\\.venv\\Scripts\\python.exe -m tickhub.server --port 8000 --admin-port 8787 --data-dir C:\\workplace\\TickHub-CN\\data',
    }),
    module({
      module: 'wire',
      pid: 2708,
      ports: [8790, 8791],
      origin: 'adopted',
      cmdline: 'C:\\workplace\\Wire\\quotafuse-wire\\.venv\\Scripts\\python.exe -m quotafuse_wire.serve --host 127.0.0.1 --port 8790 --mcp-port 8791 --config C:\\workplace\\Wire\\quotafuse-wire\\config\\sources.yaml',
    }),
    module({
      module: 'senate',
      pid: 8312,
      ports: [8765, 8786],
      origin: 'spawned-via-task',
      cmdline: 'C:\\workplace\\StockSenate\\.venv\\Scripts\\python.exe -m stocksenate.mcp --transport http --port 8765',
    }),
    module({
      module: 'financecoffer',
      pid: null,
      ports: [8100],
      state: 'needs_human',
      health: 'needs_human',
      origin: 'spawned',
      cmdline_hash: null,
      last_exit_reason: '连续崩溃 3 次（5 分钟窗口），已停止自愈',
      self_heal: { attempts: 3, max: 3, status: 'gave_up' },
      listening: false,
    }),
  ],
  cards: [
    module({ module: 'tickhub', pid: 4500, ports: [8000, 8787], origin: 'spawned' }),
    module({
      module: 'wire',
      pid: 2708,
      ports: [8790, 8791],
      origin: 'spawned',
      state: 'restarting',
      health: 'restarting',
      self_heal: { attempts: 2, max: 3, status: 'healing' },
      restart_count: 2,
      restarts_5min: 2,
    }),
    module({
      module: 'senate',
      pid: 8312,
      ports: [8765, 8786],
      origin: null,
      ownership: 'external',
      evidence: 'port_taken_by_unknown',
    }),
    module({
      module: 'financecoffer',
      pid: null,
      ports: [8100],
      state: 'needs_human',
      health: 'needs_human',
      origin: 'spawned',
      cmdline_hash: null,
      last_exit_reason: '连续崩溃 3 次（5 分钟窗口），已停止自愈',
      self_heal: { attempts: 3, max: 3, status: 'gave_up' },
      listening: false,
    }),
  ],
  empty: [
    module({ module: 'tickhub', pid: 4500, ports: [8000, 8787], origin: 'spawned' }),
    module({ module: 'wire', pid: 2708, ports: [8790, 8791], origin: 'spawned' }),
    module({ module: 'senate', pid: 8312, ports: [8765, 8786], origin: 'spawned' }),
  ],
}

const SUPERVISOR = {
  ok: true,
  engine: { running: true, window_seconds: 300, max_restarts: 3, backoff: [5, 15, 45] },
  modules: MODULES[SCENARIO] ?? MODULES.dialog,
  configured: true,
  supervisor_pid: 5950,
  order: ['tickhub', 'tickhub_us', 'wire', 'senate', 'financecoffer', 'proxyfleet'],
}

if (argv.includes('--dump')) {
  console.log(JSON.stringify(SUPERVISOR, null, 2))
  process.exit(0)
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
}

function json(response, status, body) {
  const text = JSON.stringify(body)
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
  })
  response.end(text)
}

function serveFile(response, file) {
  response.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' })
  createReadStream(file).pipe(response)
}

const server = createServer((request, response) => {
  const url = new URL(request.url, `http://127.0.0.1:${PORT}`)
  const path = url.pathname

  if (path === '/api/health') {
    return json(response, 200, { ok: true, version: '0.9.0-beta.10', data_dir: 'D:\\workplace\\LadderStock\\data' })
  }
  if (path === '/api/state/config') {
    return json(response, 200, {
      config_dir: 'D:\\workplace\\LadderStock\\config',
      data_dir: 'D:\\workplace\\LadderStock\\data',
      files: [],
    })
  }
  if (path === '/api/control/peers') {
    return json(response, 200, BOARD)
  }
  if (path === '/api/supervisor/state') {
    return json(response, 200, SUPERVISOR)
  }
  if (path.startsWith('/api/supervisor/state/') && path.endsWith('/reset') && request.method === 'POST') {
    return json(response, 200, { ok: true, module: path.split('/')[4] ?? 'unknown' })
  }
  if (path === '/api/supervisor/stop' && request.method === 'POST') {
    let body = ''
    request.on('data', (chunk) => {
      body += chunk
    })
    request.on('end', () => {
      let parsed = {}
      try {
        parsed = JSON.parse(body)
      } catch {
        return json(response, 400, { detail: '请求体不是 JSON' })
      }
      if (parsed.confirm !== true || !Array.isArray(parsed.modules)) {
        return json(response, 400, { detail: '缺少显式确认意图（confirm: true）或模块清单' })
      }
      return json(response, 200, { ok: true, stopped: parsed.modules, skipped: [] })
    })
    return
  }
  if (path.startsWith('/api/')) {
    return json(response, 404, { detail: '本替身未提供该端点' })
  }

  const candidate = join(DIST, path)
  if (existsSync(candidate) && statSync(candidate).isFile()) return serveFile(response, candidate)
  return serveFile(response, join(DIST, 'index.html'))
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`QUOT-484 mock api (${SCENARIO}) → http://127.0.0.1:${PORT}`)
})

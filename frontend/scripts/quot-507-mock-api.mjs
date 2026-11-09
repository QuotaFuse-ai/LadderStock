#!/usr/bin/env node
/**
 * QUOT-507 截图取证用的本机替身 API（只用于 `docs/` 截图与单测样例，不进产品构建）。
 *
 * 后端 proxypool 映射面（QUOT-490 / QUOT-506）尚未落地时，这里按 issue 给的响应形状
 * 回放代理池场景，前端仍走真实 `fetch('/api/supervisor/state')`，不注入任何前端代码：
 *
 *   SCENARIO=stopped    整机重启后的静息态：全部 mapping = stopped（重启不自动起）
 *   SCENARIO=running    混合态：两条人工运行中（12ms / 8ms）+ AI 三条折叠
 *   SCENARIO=conflict   冲突（端口 8790，无重试）+ 需人工处理（有重试）+ 人工运行中
 *   SCENARIO=probe      探活前 rtt 为空，点「探活」后接口返回真实毫秒数
 *
 * 用法：
 *   node scripts/quot-507-mock-api.mjs [--port 5962] [--dump]
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
const PORT = portFlag >= 0 ? Number(argv[portFlag + 1]) : 5962
const SCENARIO = process.env.SCENARIO ?? 'running'

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
      id: 'proxyfleet',
      product: 'ProxyFleet',
      role: '代理池：出口映射与网关',
      directory: 'C:\\workplace\\ProxyFleet',
      directory_exists: true,
      registered: true,
      status: 'running',
      status_label: '运行中',
      detail: '计划任务 ProxyFleet 已运行',
      installed: true,
      version: '0.6.0',
      venv: 'C:\\workplace\\ProxyFleet\\.venv',
      ports: { mcp: null, web: 8788, web_label: '控制台' },
      listening: { mcp: [], web: [{ pid: 9100, process: 'python.exe' }] },
      health: { ok: true, detail: 'HTTP 200' },
      dist: { wheel: null, editable: true, env_file: '' },
      port_conflicts: [],
      last_job: null,
      open_url: 'http://127.0.0.1:8788',
      actions: { can_install: false, can_start: false, can_stop: true, can_open: true, can_config: false },
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

function mapping(overrides) {
  return {
    listen: '127.0.0.1:8788',
    upstream_host: 'api.openai.com',
    upstream_port: 443,
    enabled: true,
    owner: 'human',
    state: 'running',
    rtt_ms: null,
    ...overrides,
  }
}

const AI_MAPPINGS = (
  running,
  rtt,
) => [
  mapping({
    id: 'ai-codex',
    listen: '127.0.0.1:8801',
    upstream_host: 'api.openai.com',
    upstream_port: 443,
    owner: 'ai',
    state: running ? 'running' : 'stopped',
    rtt_ms: running ? rtt : null,
  }),
  mapping({
    id: 'ai-claude',
    listen: '127.0.0.1:8802',
    upstream_host: 'api.anthropic.com',
    upstream_port: 443,
    owner: 'ai',
    state: running ? 'running' : 'stopped',
    rtt_ms: running ? rtt + 5 : null,
  }),
  mapping({
    id: 'ai-gemini',
    listen: '127.0.0.1:8803',
    upstream_host: 'generativelanguage.googleapis.com',
    upstream_port: 443,
    owner: 'ai',
    state: 'stopped',
    rtt_ms: null,
  }),
]

const POOLS = {
  stopped: [
    mapping({
      id: 'human-chat',
      listen: '127.0.0.1:8788',
      upstream_host: 'api.openai.com',
      upstream_port: 443,
      state: 'stopped',
    }),
    mapping({
      id: 'human-work',
      listen: '127.0.0.1:8789',
      upstream_host: 'api.deepseek.com',
      upstream_port: 443,
      state: 'stopped',
    }),
    ...AI_MAPPINGS(false, 0),
  ],
  running: [
    mapping({
      id: 'human-chat',
      listen: '127.0.0.1:8788',
      upstream_host: 'api.openai.com',
      upstream_port: 443,
      state: 'running',
      rtt_ms: 12,
    }),
    mapping({
      id: 'human-work',
      listen: '127.0.0.1:8789',
      upstream_host: 'api.deepseek.com',
      upstream_port: 443,
      state: 'running',
      rtt_ms: 8,
    }),
    mapping({
      id: 'human-batch',
      listen: '127.0.0.1:8790',
      upstream_host: 'api.moonshot.cn',
      upstream_port: 443,
      state: 'stopped',
    }),
    ...AI_MAPPINGS(true, 9),
  ],
  conflict: [
    mapping({
      id: 'human-chat',
      listen: '127.0.0.1:8788',
      upstream_host: 'api.openai.com',
      upstream_port: 443,
      state: 'running',
      rtt_ms: 12,
    }),
    mapping({
      id: 'human-batch',
      listen: '127.0.0.1:8790',
      upstream_host: 'api.moonshot.cn',
      upstream_port: 443,
      state: 'conflict',
      conflict_port: 8790,
    }),
    mapping({
      id: 'human-legacy',
      listen: '127.0.0.1:8791',
      upstream_host: 'api.anthropic.com',
      upstream_port: 443,
      state: 'needs_human',
    }),
    ...AI_MAPPINGS(true, 9),
  ],
  probe: [
    mapping({
      id: 'human-chat',
      listen: '127.0.0.1:8788',
      upstream_host: 'api.openai.com',
      upstream_port: 443,
      state: 'running',
      rtt_ms: null,
    }),
    mapping({
      id: 'human-work',
      listen: '127.0.0.1:8789',
      upstream_host: 'api.deepseek.com',
      upstream_port: 443,
      state: 'running',
      rtt_ms: null,
    }),
    ...AI_MAPPINGS(false, 0).map((item, index) =>
      index === 0 ? { ...item, state: 'running', rtt_ms: null } : item,
    ),
  ],
}

function poolModule() {
  const mappings = POOLS[SCENARIO] ?? POOLS.running
  const anyRunning = mappings.some((item) => item.state === 'running')
  return module({
    module: 'proxypool',
    pid: anyRunning ? 9100 : null,
    ports: [8788, 8789],
    state: anyRunning ? 'running' : 'stopped',
    listening: anyRunning,
    self_heal: { attempts: 0, max: 3, status: 'ok' },
    mappings,
  })
}

const MODULES = [
  module({
    module: 'tickhub',
    pid: 4500,
    ports: [8000, 8787],
    origin: 'spawned',
    cmdline:
      'C:\\workplace\\TickHub-CN\\.venv\\Scripts\\python.exe -m tickhub.server --port 8000 --admin-port 8787',
  }),
  module({
    module: 'wire',
    pid: 2708,
    ports: [8790, 8791],
    origin: 'adopted',
    cmdline:
      'C:\\workplace\\Wire\\quotafuse-wire\\.venv\\Scripts\\python.exe -m quotafuse_wire.serve --port 8790 --mcp-port 8791',
  }),
  poolModule(),
]

const SUPERVISOR = {
  ok: true,
  engine: { running: true, window_seconds: 300, max_restarts: 3, backoff: [5, 15, 45] },
  modules: MODULES,
  configured: true,
  supervisor_pid: 5950,
  order: ['tickhub', 'wire', 'proxyfleet'],
}

/** 探活结果：真实毫秒数（不是布尔）。 */
const PROBE_RTT = {
  'human-chat': 37,
  'human-work': 24,
  'ai-codex': 31,
  'ai-claude': 19,
  'ai-gemini': 41,
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
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
  })
  response.end(JSON.stringify(body))
}

function readBody(request) {
  return new Promise((resolveBody, rejectBody) => {
    let body = ''
    request.on('data', (chunk) => {
      body += chunk
    })
    request.on('end', () => {
      try {
        resolveBody(body ? JSON.parse(body) : {})
      } catch {
        rejectBody(new Error('请求体不是 JSON'))
      }
    })
  })
}

function serveFile(response, file) {
  response.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' })
  createReadStream(file).pipe(response)
}

const server = createServer(async (request, response) => {
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
  if (path === '/api/supervisor/pool/start' && request.method === 'POST') {
    const body = await readBody(request)
    return json(response, 200, { ok: true, started: body.mapping_ids ?? [] })
  }
  if (path === '/api/supervisor/pool/stop' && request.method === 'POST') {
    const body = await readBody(request)
    return json(response, 200, { ok: true, stopped: body.mapping_ids ?? [] })
  }
  if (path === '/api/supervisor/pool/probe' && request.method === 'POST') {
    const body = await readBody(request)
    const ids = Array.isArray(body.mapping_ids) ? body.mapping_ids : Object.keys(PROBE_RTT)
    return json(response, 200, {
      ok: true,
      results: ids.map((id) => ({ mapping_id: id, ok: true, rtt_ms: PROBE_RTT[id] ?? 20, error: null })),
    })
  }
  if (path === '/api/supervisor/pool/upstream' && request.method === 'PUT') {
    await readBody(request)
    return json(response, 200, { ok: true })
  }
  if (path.startsWith('/api/supervisor/state/') && path.endsWith('/reset') && request.method === 'POST') {
    return json(response, 200, { ok: true })
  }
  if (path === '/api/supervisor/stop' && request.method === 'POST') {
    const body = await readBody(request)
    return json(response, 200, { ok: true, stopped: body.modules ?? [], skipped: [] })
  }
  if (path.startsWith('/api/')) {
    return json(response, 404, { detail: '本替身未提供该端点' })
  }

  const candidate = join(DIST, path)
  if (existsSync(candidate) && statSync(candidate).isFile()) return serveFile(response, candidate)
  return serveFile(response, join(DIST, 'index.html'))
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`QUOT-507 mock api (${SCENARIO}) → http://127.0.0.1:${PORT}`)
})

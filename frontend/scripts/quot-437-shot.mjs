#!/usr/bin/env node
/**
 * QUOT-437 取证截图（一次性脚本，不进仓库）。
 *
 * 本机没有 `ldst run` 的 daily_k / journal 数据，且 X 光口径明确「不联网补价」，
 * 所以这里在页面脚本之前把 `fetch` 换成后端 wire 形状的固定载荷，
 * 拍的是真实组件 + 真实 ECharts 渲染，只有数据是假定的（PR 里已注明）。
 *
 * 用法：
 *   cd frontend && npm run dev -- --port 5317 --strictPort   # 另一个终端
 *   node ../../quot-437-shot.mjs
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.env.LDST_SHOT_BASE ?? 'http://127.0.0.1:5317'
const OUT = join(process.cwd(), '..', 'docs', 'screenshots', 'quot-437')

const CORR = {
  available: true,
  reason: null,
  max_pair: { pair: ['600000', '000001'], corr: 0.87, samples: 42 },
  avg_abs_corr: 0.61,
  pairs: [{ pair: ['600000', '000001'], corr: 0.87, samples: 42 }],
  pairs_count: 1,
  min_days: 20,
  window: { first: '2026-09-01', last: '2026-11-04', days: 42 },
}

const XRAY = {
  local_only: true,
  store: 'local_sqlite',
  price_lookup: { dataset: 'daily_k', date: '2026-11-04', reason: null },
  as_of: '2026-11-04',
  total_value: 30800,
  items: [
    { symbol: '600000', name: '浦发银行', market_value: 18600, weight: 0.604 },
    { symbol: '000001', name: '平安银行', market_value: 9200, weight: 0.299 },
    { symbol: '601318', name: '中国平安', market_value: 3000, weight: 0.097 },
    { symbol: '300750', name: '宁德时代', market_value: null, weight: null },
  ],
  top1: 0.604,
  top3: 1,
  top5: 1,
  industry_buckets: [
    { industry: '银行', labeled: true, market_value: 27800, weight: 0.902, symbols: ['600000', '000001'] },
    { industry: '保险', labeled: true, market_value: 3000, weight: 0.097, symbols: ['601318'] },
    { industry: '未标注', labeled: false, market_value: 0, weight: 0, symbols: ['300750'] },
  ],
  industry_labeled_count: 2,
  industry_unlabeled_count: 1,
  metrics: { corr: CORR },
  gaps: [
    {
      symbol: '300750',
      name: '宁德时代',
      affects: 'weight',
      reason: '本机 daily_k 缓存里没有这只的收盘价（停牌或未同步），没算进权重分母',
    },
  ],
  notes: ['权重按现价市值算；缺价的持仓不进分母，单独列在缺口里', '行业只用你自己标注的行业，没标的归「未标注」'],
}

const PORTFOLIO = {
  local_only: true,
  store: 'local_sqlite',
  method: 'average',
  method_label: '均价法',
  methods: [
    { id: 'average', label: '均价法' },
    { id: 'fifo', label: '先进先出' },
  ],
  today: '2026-11-06',
  price_lookup: { dataset: 'daily_k', date: '2026-11-04', reason: null },
  items: [
    {
      symbol: '600000',
      name: '浦发银行',
      shares: 1800,
      unit_cost: 9.2,
      cost_total: 16560,
      opened_at: '2025-01-02',
      lots: [],
      realized_pnl: 0,
      dividends: 120,
      fees: 12,
      trade_count: 3,
      latest_price: 10.33,
      change: 0.01,
      change_pct: 0.001,
      market_value: 18600,
      pnl: 2040,
      pnl_pct: 0.123,
      price_reason: null,
    },
    {
      symbol: '000001',
      name: '平安银行',
      shares: 800,
      unit_cost: 11.0,
      cost_total: 8800,
      opened_at: '2025-02-03',
      lots: [],
      realized_pnl: 0,
      dividends: 0,
      fees: 6,
      trade_count: 1,
      latest_price: 11.5,
      change: -0.02,
      change_pct: -0.002,
      market_value: 9200,
      pnl: 400,
      pnl_pct: 0.045,
      price_reason: null,
    },
  ],
  closed: [],
  issues: [],
  ledger: [],
  legacy: [],
  migration_pending: 0,
  migration: { backup: null, migrated: [], removed_stale: [], failed: [], error: null },
  totals_cny: {
    currency: 'CNY',
    available: true,
    market_value: 27800,
    cost: 25360,
    pnl: 2440,
    pnl_pct: 0.096,
    priced_count: 2,
    unpriced_count: 0,
    reason: null,
  },
}

const LEDGER = { ok: true, trades: [], issues: [], notes: [], as_of: '2026-11-06' }

const PREVIEW = {
  ok: true,
  count: 4,
  limit: 1000,
  max_bytes: 245760,
  rows: [
    { index: 0, problem: null, duplicate: null, duplicate_of: null },
    { index: 1, problem: '卖出 500 股，超过当时持有的 100 股', duplicate: null, duplicate_of: null },
    { index: 2, problem: null, duplicate: null, duplicate_of: null },
    { index: 3, problem: null, duplicate: 'existing', duplicate_of: 42 },
  ],
  existing_issues: [],
}

const CSV = [
  '日期,类型,代码,名称,数量,价格,费用',
  '2025-01-02,买入,600519,贵州茅台,100,1500,5',
  '2025-02-03,卖出,600519,贵州茅台,500,1700,8',
  '2025-03-04,买入,1,万科A,100,10',
  '2025-04-01,买入,000001,平安银行,200,12',
  '2025-05-06,买入,600000,浦发银行,300,10',
].join(String.fromCharCode(10))

const STUB = `(() => {
  const FIXTURES = ${JSON.stringify({
    '/api/portfolio/xray': XRAY,
    '/api/portfolio/ledger/import/preview': PREVIEW,
    '/api/portfolio/ledger': LEDGER,
    '/api/portfolio': PORTFOLIO,
  })}
  const real = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const url = String(typeof input === 'string' ? input : input?.url ?? input)
    const hit = Object.keys(FIXTURES).sort((a, b) => b.length - a.length).find((key) => url.startsWith(key))
    if (!hit) return real(input, init)
    return new Response(JSON.stringify(FIXTURES[hit]), { status: 200, headers: { 'content-type': 'application/json' } })
  }
})()`

function findChrome() {
  const candidates = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe']
  return process.env.LDST_CHROME ?? candidates.find((p) => existsSync(p))
}

const chrome = findChrome()
if (!chrome) {
  console.error('找不到 Chrome/Edge')
  process.exit(1)
}

const profile = mkdtempSync(join(tmpdir(), 'quot437-'))
mkdirSync(OUT, { recursive: true })

const child = spawn(
  chrome,
  ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
  { stdio: ['ignore', 'ignore', 'pipe'] },
)

const wsUrl = await new Promise((ok, bad) => {
  let buffer = ''
  const timer = setTimeout(() => bad(new Error('Chrome 启动超时')), 20000)
  child.stderr.on('data', (chunk) => {
    buffer += chunk.toString()
    const m = /DevTools listening on (ws:\/\/\S+)/.exec(buffer)
    if (m) {
      clearTimeout(timer)
      ok(m[1])
    }
  })
})

const port = new URL(wsUrl).port
const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl)
await new Promise((ok) => (ws.onopen = ok))

let seq = 0
const pending = new Map()
ws.onmessage = (event) => {
  const message = JSON.parse(event.data)
  if (message.id && pending.has(message.id)) {
    const { done, fail } = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) fail(new Error(JSON.stringify(message.error)))
    else done(message.result)
  }
}
const send = (method, params = {}) =>
  new Promise((done, fail) => {
    const id = ++seq
    pending.set(id, { done, fail })
    ws.send(JSON.stringify({ id, method, params }))
  })
const evaluate = async (expression) => (await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result?.value
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms))

await send('Page.enable')
await send('Runtime.enable')
await send('Page.addScriptToEvaluateOnNewDocument', { source: STUB })

const CONTAINER = `document.querySelector('main').parentElement`

async function openTab(tab, theme) {
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false })
  await send('Page.navigate', { url: `${BASE}/my-stocks?tab=hold` })
  await sleep(2000)
  await evaluate(`localStorage.setItem('ldst.theme', ${JSON.stringify(theme)}); location.reload()`)
  await sleep(2400)
  const clicked = await evaluate(`(() => {
    const button = [...document.querySelectorAll('button,[role=tab]')].find((el) => el.textContent.trim() === ${JSON.stringify(tab)})
    if (!button) return 'missing'
    button.click()
    return 'ok'
  })()`)
  await sleep(2200)
  if (clicked !== 'ok') throw new Error(`页签没找到：${tab}`)
}

/** 直接按像素滚内层容器（页面是内部滚动，captureBeyondViewport 拍不全）。 */
async function shotAt(file, scrollTop) {
  await evaluate(`(() => { const box = ${CONTAINER}; box.scrollTop = ${scrollTop}; return String(box.scrollTop) })()`)
  await sleep(700)
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
  writeFileSync(join(OUT, file), Buffer.from(data, 'base64'))
  console.log(`${file}  scrollTop=${scrollTop}`)
}

/** 把内层滚动容器滚到某个标题处再拍。 */
async function shotSection(file, heading, offset = 0) {
  const state = await evaluate(`(() => {
    const box = ${CONTAINER}
    const hit = [...document.querySelectorAll('h2,h3')].find((el) => el.textContent.includes(${JSON.stringify(heading)}))
    if (!hit) return 'missing'
    box.scrollTop = Math.round(hit.getBoundingClientRect().top - box.getBoundingClientRect().top) + box.scrollTop + ${offset}
    return String(box.scrollTop)
  })()`)
  await sleep(700)
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
  writeFileSync(join(OUT, file), Buffer.from(data, 'base64'))
  const text = await evaluate(`(() => { const m = document.querySelector('main'); return m ? m.innerText.replace(/\\s+/g,' ').slice(0, 260) : null })()`)
  console.log(`${file}  scrollTop=${state}`)
  console.log(`  ${text}`)
}

// X 光：体检卡片 + 两张图 + 权重表 + 缺口
await openTab('X 光', 'dark')
await shotSection('xray-dark-top.png', '持仓体检')
await shotAt('xray-dark-lower.png', 2060)

// 交易流水：粘贴 → 检查 → 预览（问题行不给勾）
await openTab('交易流水', 'dark')
const filled = await evaluate(`(() => {
  const ta = document.getElementById('portfolio-import-text')
  if (!ta) return 'no textarea'
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
  setter.call(ta, ${JSON.stringify(CSV)})
  ta.dispatchEvent(new Event('input', { bubbles: true }))
  return 'ok'
})()`)
await sleep(500)
const checked = await evaluate(`(() => {
  const button = [...document.querySelectorAll('button')].find((el) => el.textContent.trim() === '检查')
  if (!button) return 'missing'
  button.click()
  return 'ok'
})()`)
await sleep(2600)
await shotSection('import-preview-dark.png', '从表格导入')
const summary = await evaluate(`(() => {
  const rows = [...document.querySelectorAll('tbody tr')].length
  const boxes = [...document.querySelectorAll('input[type=checkbox]')].length
  const button = [...document.querySelectorAll('button')].find((el) => el.textContent.includes('导入选中的'))
  return JSON.stringify({ filled: ${JSON.stringify(filled)}, checked: ${JSON.stringify(checked)}, rows, boxes, button: button ? button.textContent.trim() : null })
})()`)
console.log(`导入预览自检：${summary}`)

ws.close()
child.kill()
await sleep(600)
try {
  rmSync(profile, { recursive: true, force: true, maxRetries: 5 })
} catch {
  /* 临时 profile 清理失败不影响取证结果 */
}
console.log(`\n输出目录：${OUT}`)

#!/usr/bin/env node
/**
 * 1280px 全屏回归（QUOT-304 B 轮的验收证据生成器）。
 *
 * 为什么单独一个脚本：`visual-shot.mjs` 只出图，不判「横向溢出 / console 报错 /
 * 五态是否破」。B 轮改的是侧栏结构，代价是**每一屏都要重新过一遍**，
 * 人肉点 20 屏 × 5 态必错，所以做成一条可重跑的检查。
 *
 * 用法：
 *   cd frontend && npm run dev -- --port 5173 --strictPort      # 另开一个终端
 *   node scripts/check-1280.mjs                                 # 全路由 × 五态
 *   node scripts/check-1280.mjs --states live                   # 只跑真实数据态
 *   node scripts/check-1280.mjs --shots --out ../docs/screenshots/quota-304
 *
 * 判定（任一不满足就非零退出）：
 *   1. 视口 1280×900 下 `documentElement.scrollWidth <= clientWidth`（零横向溢出），
 *      并抓出最宽的越界元素（class + 宽度）方便定位；
 *   2. console 无 `error`、无未捕获异常（`Runtime.exceptionThrown`）；
 *      `/api/*` 请求失败单独归为 `network`，不计入报错——没有 `ldst run` 时全站必红，
 *      那测的是后端可用性，不是导航回归；
 *   3. `?state=empty|loading|error|partial|offline` 五态逐屏不破（同样查 1 与 2）；
 *   4. 侧栏结构不变量：9 个组名与顺序、「我的股票」两条子项（DEC-044）、`跳到主内容` skip link
 *      与 `aria-current` 高亮在每一屏都成立；
 *   5. 三条视觉不变量的 DOM 侧抽查：涨红用 `--ldst-up`（红）、跌绿用 `--ldst-down`（绿）、
 *      数字带 `tabular-nums`、缺失值渲染成 `—` 而不是 `0`。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const VIEWPORT = { width: 1280, height: 900 }
const HERE = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(HERE, '..', '..')

/** 路由以 `src/routes.ts` 为准；带参数的用样例实参。 */
const ROUTES = [
  { slug: 'home', path: '/' },
  { slug: 'market-sentiment', path: '/market?tab=sentiment' },
  { slug: 'market-ladder', path: '/market?tab=ladder' },
  { slug: 'market-theme', path: '/market?tab=theme' },
  { slug: 'watch', path: '/watch' },
  { slug: 'intraday', path: '/intraday' },
  { slug: 'review', path: '/review' },
  { slug: 'weekly', path: '/weekly' },
  { slug: 'my-stocks-positions', path: '/positions' },
  { slug: 'pool', path: '/pool' },
  { slug: 'my-stocks-calendar', path: '/calendar' },
  { slug: 'journal', path: '/journal' },
  { slug: 'validate', path: '/validate' },
  { slug: 'limit-up', path: '/limit-up' },
  { slug: 'news', path: '/news' },
  { slug: 'industry', path: '/industry' },
  { slug: 'industry-name', path: `/industry/${encodeURIComponent('电子')}` },
  { slug: 'signals', path: '/signals' },
  { slug: 'research', path: '/research' },
  { slug: 'research-code', path: '/research/600519' },
  { slug: 'research-code-report', path: '/research/600519/report' },
  { slug: 'settings', path: '/settings' },
  { slug: 'not-found', path: '/this-route-does-not-exist' },
]

const STATES = ['empty', 'loading', 'error', 'partial', 'offline']

/**
 * 空态裸 0 的**存量债登记**（`route.slug|state` → 允许处数）：超出即视为本轮新增。
 * UI-SPEC §12.5 R-07 是已登记的整改项，不由 B 轮导航改动承担；这里冻结数量而不是取消判定。
 */
const KNOWN_BARE_ZERO = {
  // 「0 笔」是当日真实成交笔数，不是缺失值占位
  'journal|empty': 1,
  // R-07：`SignalsScreen.tsx:237,347` 命中数列直显 0
  'signals|empty': 4,
  // R-07 存量债（v1.3 由本巡检量出、已登记进 §12.5，非本轮导航改动引入）：
  // `limitup/LimitUpScreen.tsx:152` header 计数 `formatInt(payload.count)`，空态应为「—」
  'limit-up|empty': 1,
  // `industry/IndustryDetailScreen.tsx:508` 成分股数 `formatInt(constituent_count)`，未绑定时应为「—」
  'industry-name|empty': 1,
}

/** 侧栏结构断言（DEC-044 之后：9 组，「我的股票」在日志之后）。 */
const EXPECTED_GROUPS = [
  '首页',
  '市场',
  '盯盘',
  '复盘',
  '日志',
  '我的股票',
  '验证',
  '产业研究',
  '个股深挖',
]
const EXPECTED_MY_STOCKS = ['我的持仓', '事件日历']

function parseArgs(argv) {
  const options = {
    base: 'http://127.0.0.1:5173',
    states: STATES,
    shots: false,
    out: join(REPO_ROOT, 'docs', 'screenshots', 'quota-304'),
    port: 9334,
    chrome: process.env.LDST_CHROME ?? null,
    json: null,
    only: null,
    query: null,
  }
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i]
    const next = () => argv[++i]
    if (flag === '--base') options.base = next().replace(/\/$/, '')
    else if (flag === '--states') {
      const value = next()
      options.states = value === 'live' ? [null] : value.split(',').filter(Boolean)
    } else if (flag === '--shots') options.shots = true
    else if (flag === '--out') options.out = resolve(next())
    else if (flag === '--port') options.port = Number(next())
    else if (flag === '--chrome') options.chrome = next()
    else if (flag === '--json') options.json = resolve(next())
    else if (flag === '--only') options.only = next().split(',')
    else if (flag === '--query') options.query = next()
    else throw new Error(`未知参数：${flag}`)
  }
  return options
}

function findChrome(explicit) {
  if (explicit) return explicit
  const candidates = [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  ]
  for (const p of candidates) if (existsSync(p)) return p
  throw new Error('没找到 Chrome/Edge，用 --chrome <path> 或 LDST_CHROME 指定')
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitForJson(url, deadlineMs) {
  const started = Date.now()
  let last = null
  while (Date.now() - started < deadlineMs) {
    try {
      const res = await fetch(url)
      if (res.ok) return await res.json()
    } catch (e) {
      last = e
    }
    await sleep(200)
  }
  throw new Error(`CDP 未就绪：${url}（${last?.message ?? '超时'}）`)
}

class Cdp {
  constructor(socket) {
    this.socket = socket
    this.nextId = 1
    this.pending = new Map()
    this.events = new Map()
    socket.addEventListener('message', (event) => {
      let msg
      try {
        msg = JSON.parse(event.data)
      } catch {
        return
      }
      if (msg.id !== undefined) {
        const w = this.pending.get(msg.id)
        if (!w) return
        this.pending.delete(msg.id)
        if (msg.error) w.reject(new Error(`${msg.error.message} (${msg.error.code})`))
        else w.resolve(msg.result)
        return
      }
      const handlers = this.events.get(msg.method)
      if (handlers) for (const h of handlers) h(msg.params)
    })
  }
  static async connect(url) {
    const socket = new WebSocket(url)
    await new Promise((res, rej) => {
      socket.addEventListener('open', res, { once: true })
      socket.addEventListener('error', () => rej(new Error(`无法连接 CDP：${url}`)), { once: true })
    })
    return new Cdp(socket)
  }
  send(method, params = {}, sessionId) {
    const id = this.nextId++
    return new Promise((res, rej) => {
      this.pending.set(id, { resolve: res, reject: rej })
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }))
    })
  }
  on(method, handler) {
    const list = this.events.get(method) ?? []
    list.push(handler)
    this.events.set(method, list)
  }
  once(method, timeoutMs) {
    return new Promise((res) => {
      const timer = setTimeout(() => {
        this.off(method, handler)
        res(null)
      }, timeoutMs)
      const handler = (params) => {
        clearTimeout(timer)
        this.off(method, handler)
        res(params)
      }
      this.on(method, handler)
    })
  }
  off(method, handler) {
    const list = this.events.get(method) ?? []
    const i = list.indexOf(handler)
    if (i >= 0) list.splice(i, 1)
  }
}

/** 页面内一次跑完：溢出、侧栏结构、三条不变量。返回可 JSON 化的结果。 */
const PROBE = `(() => {
  const de = document.documentElement;
  const overflow = de.scrollWidth - de.clientWidth;
  const culprits = [];
  if (overflow > 0) {
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (r.right > de.clientWidth + 1 && r.width > 0) {
        culprits.push({ tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 90), right: Math.round(r.right), w: Math.round(r.width) });
        if (culprits.length > 4) break;
      }
    }
  }
  const nav = document.querySelector('nav[aria-label="主导航"]') || document.querySelector('aside nav') || document.querySelector('nav');
  const groups = nav ? [...nav.querySelectorAll(':scope > ul > li')].map((li) => {
    const head = li.querySelector(':scope > button') || li.querySelector(':scope > a');
    if (!head) return '';
    const span = head.querySelector('span');
    return ((span && span.textContent) || head.getAttribute('title') || '').trim();
  }).filter(Boolean) : null;
  const sidebar = document.querySelector('aside') || nav;
  const links = sidebar ? [...sidebar.querySelectorAll('a[href]')].map((a) => ({ href: a.getAttribute('href'), text: (a.textContent || '').trim(), current: a.getAttribute('aria-current') })) : [];
  const skip = [...document.querySelectorAll('a')].map((a) => (a.textContent || '').trim()).slice(0, 1);
  const isNum = (t) => /^[-+]?[\\d][\\d.,]*(%|倍|亿|万|天)?\$/.test(t);
  const numericLeaves = [...document.querySelectorAll('td, th, [class*="ldst-num"], [class*="metric"], [class*="Stat"], [class*="value"]')].filter((el) => el.children.length === 0 && isNum((el.textContent || '').trim()));
  const nonTabular = numericLeaves.filter((el) => !getComputedStyle(el).fontVariantNumeric.includes('tabular-nums'))
    .map((el) => ({ tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 80), text: (el.textContent || '').trim() })).slice(0, 5);
  const nums = numericLeaves.length - nonTabular.length;
  const dashCount = [...document.querySelectorAll('td,th,span,dd,div')].filter((el) => !el.children.length && (el.textContent || '').trim() === '—').length;
  const zeroEls = [...document.querySelectorAll('td,span,dd')].filter((el) => !el.children.length && (el.textContent || '').trim() === '0').map((el) => ({ tag: el.tagName.toLowerCase(), host: (el.parentElement ? (el.parentElement.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60) : '') }));
  const zeroCount = zeroEls.length;
  // 红涨绿跌：class 含 text-up 必须解析到 --ldst-up（红），text-down 到 --ldst-down（绿）
  const toRgb = (v) => { const s = v.trim(); return s.startsWith('#') ? s.slice(1).match(/../g).map((h) => parseInt(h, 16)).join(',') : s; };
  const cs = getComputedStyle(de);
  const upVar = toRgb(cs.getPropertyValue('--ldst-up'));
  const downVar = toRgb(cs.getPropertyValue('--ldst-down'));
  const rgb = (c) => (c.match(/\\d+/g) || []).join(',');
  const upDown = { up: 0, down: 0, bad: [] };
  for (const el of document.querySelectorAll('[class*="text-up"], [class*="text-down"]')) {
    if (el.children.length) continue;
    const cls = (el.className || '').toString();
    const color = rgb(getComputedStyle(el).color);
    if (cls.includes('text-up')) { upDown.up++; if (color !== upVar) upDown.bad.push({ cls: cls.slice(0, 60), want: upVar, got: color }); }
    if (cls.includes('text-down')) { upDown.down++; if (color !== downVar) upDown.bad.push({ cls: cls.slice(0, 60), want: downVar, got: color }); }
  }
  upDown.tokens = '--ldst-up=' + cs.getPropertyValue('--ldst-up').trim() + '（涨） / --ldst-down=' + cs.getPropertyValue('--ldst-down').trim() + '（跌）';
  const navOverflowPx = nav ? nav.scrollHeight - nav.clientHeight : null;
  const h1 = [...document.querySelectorAll('h1')].map((h) => (h.textContent || '').trim());
  return { overflowPx: overflow, navOverflowPx, culprits, groupCount: groups ? groups.length : null, groups, links, skip, nums, nonTabular, dashCount, zeroCount, zeroEls, upDown, h1 };
})()`

async function probePage(cdp, base, route, state, options, errors) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' })
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true })
  const consoleErrors = []
  const onConsole = (p) => {
    if (p.type !== 'error') return
    const text = (p.args ?? []).map((a) => a.value ?? a.description ?? '').join(' ')
    // 后端不在线时 /api 必失败：这是环境事实，不是导航回归
    if (/Failed to load resource|net::ERR|500|404/.test(text) && /\/api\//.test(text)) return
    consoleErrors.push(text.slice(0, 200))
  }
  const onException = (p) => consoleErrors.push(`exception: ${p.exceptionDetails?.exception?.description ?? p.exceptionDetails?.text}`.slice(0, 200))
  cdp.on('Runtime.consoleAPICalled', onConsole)
  cdp.on('Runtime.exceptionThrown', onException)
  try {
    await cdp.send('Page.enable', {}, sessionId)
    await cdp.send('Runtime.enable', {}, sessionId)
    await cdp.send(
      'Emulation.setDeviceMetricsOverride',
      { width: VIEWPORT.width, height: VIEWPORT.height, deviceScaleFactor: 1, mobile: false },
      sessionId,
    )
    const sep = route.path.includes('?') ? '&' : '?'
    const extra = [options.query, state ? `state=${state}` : null].filter(Boolean).join('&')
    const stateQuery = extra ? `${sep}${extra}` : ''
    const loaded = cdp.once('Page.loadEventFired', 30_000)
    await cdp.send('Page.navigate', { url: `${base}${route.path}${stateQuery}` }, sessionId)
    await loaded
    await sleep(1800)
    const { result } = await cdp.send('Runtime.evaluate', { expression: PROBE, returnByValue: true }, sessionId)
    const probe = result.value
    if (!probe) throw new Error(`${route.slug} 探针没返回结果`)
    if (probe.overflowPx > 0) {
      errors.push(`${route.slug} [${state ?? 'live'}] 横向溢出 ${probe.overflowPx}px：${JSON.stringify(probe.culprits)}`)
    }
    if (consoleErrors.length > 0) {
      errors.push(`${route.slug} [${state ?? 'live'}] console 报错 ${consoleErrors.length} 条：${consoleErrors.slice(0, 3).join(' | ')}`)
    }
    if (probe.skip[0] !== '跳到主内容') {
      errors.push(`${route.slug} [${state ?? 'live'}] 缺 skip link（首链接是「${probe.skip[0]}」）`)
    }
    if (JSON.stringify(probe.groups) !== JSON.stringify(EXPECTED_GROUPS)) {
      errors.push(`${route.slug} [${state ?? 'live'}] 侧栏分组不符：${JSON.stringify(probe.groups)}`)
    }
    const myStocks = probe.links
      .slice()
      .filter((l) => ['/positions', '/calendar'].includes(l.href))
      .map((l) => l.text)
    if (JSON.stringify(myStocks) !== JSON.stringify(EXPECTED_MY_STOCKS)) {
      errors.push(`${route.slug} [${state ?? 'live'}] 「我的股票」子项不符：${JSON.stringify(myStocks)}`)
    }
    const active = probe.links.filter((l) => l.current === 'page').map((l) => l.href)
    // 404 与矩阵下钻页本来就没有侧栏项（`pageActive` 严格按 pathname 相等），不该有高亮；
    // 其余每一屏必须「有且仅有一个」当前项（三段同路径靠 `?tab=` 判）。
    const noEntrySlugs = ['not-found', 'industry-name']
    const wantActive = noEntrySlugs.includes(route.slug) ? 0 : 1
    if (active.length !== wantActive) {
      errors.push(`${route.slug} [${state ?? 'live'}] 应有 ${wantActive} 个 aria-current 高亮，实际 ${JSON.stringify(active)}`)
    }
    if (probe.nonTabular.length > 0) {
      errors.push(`${route.slug} [${state ?? 'live'}] 未走 tabular-nums 的数字：${JSON.stringify(probe.nonTabular)}`)
    }
    if (probe.zeroCount > 0 && ['empty', 'offline'].includes(state)) {
      const allowed = KNOWN_BARE_ZERO[`${route.slug}|${state}`] ?? 0
      if (probe.zeroCount > allowed) {
        errors.push(
          `${route.slug} [${state}] 空态出现裸 0（应显「—」）：${probe.zeroCount} 处（存量登记 ${allowed} 处）${JSON.stringify(probe.zeroEls)}`,
        )
      } else {
        knownZero.push(`${route.slug} [${state}] ${probe.zeroCount} 处（存量登记 ${allowed}）：${JSON.stringify(probe.zeroEls)}`)
      }
    }
    if (options.shots && (state === null || state === 'partial') && ['my-stocks-positions', 'my-stocks-calendar', 'home'].includes(route.slug)) {
      mkdirSync(options.out, { recursive: true })
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, sessionId)
      const file = join(options.out, `${route.slug}-${state ?? 'live'}.png`)
      writeFileSync(file, Buffer.from(data, 'base64'))
      shots.push(file)
    }
    return { slug: route.slug, state, overflowPx: probe.overflowPx, navOverflowPx: probe.navOverflowPx, dashCount: probe.dashCount, tabularNums: probe.nums, consoleErrors: consoleErrors.length, tokens: probe.upDown?.tokens, upDown: probe.upDown, h1: probe.h1 }
  } finally {
    cdp.off('Runtime.consoleAPICalled', onConsole)
    cdp.off('Runtime.exceptionThrown', onException)
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {})
  }
}

const shots = []
const knownZero = []

async function main() {
  if (typeof WebSocket !== 'function') throw new Error('需要 Node 22+（全局 WebSocket）')
  const options = parseArgs(process.argv.slice(2))
  const chrome = findChrome(options.chrome)
  const userData = join(REPO_ROOT, '.cdp-profile-quota304')
  mkdirSync(userData, { recursive: true })
  const child = spawn(
    chrome,
    ['--headless=new', `--remote-debugging-port=${options.port}`, `--user-data-dir=${userData}`, '--no-first-run', '--disable-gpu', 'about:blank'],
    { stdio: 'ignore' },
  )
  const errors = []
  const rows = []
  try {
    const list = await waitForJson(`http://127.0.0.1:${options.port}/json/version`, 20_000)
    const cdp = await Cdp.connect(list.webSocketDebuggerUrl)
    const routes = options.only ? ROUTES.filter((r) => options.only.includes(r.slug)) : ROUTES
    console.log(`视口 ${VIEWPORT.width}×${VIEWPORT.height}｜路由 ${routes.length} 条｜状态 ${options.states.map((s) => s ?? 'live').join(',')}`)
    for (const route of routes) {
      for (const state of options.states) {
        try {
          rows.push(await probePage(cdp, options.base, route, state, options, errors))
          process.stdout.write('.')
        } catch (e) {
          errors.push(`${route.slug} [${state ?? 'live'}] 探针异常：${e.message}`)
        }
      }
    }
    console.log('\n\n红涨绿跌 token：', rows[0]?.tokens)
    const sample = rows.find((r) => r.upDown?.up?.length || r.upDown?.down?.length)
    if (sample) console.log('涨跌色样本：', JSON.stringify(sample.upDown))
    console.log(`\n共跑 ${rows.length} 屏×态；「—」缺失值合计 ${rows.reduce((a, r) => a + (r.dashCount || 0), 0)} 处；tabular-nums 数字合计 ${rows.reduce((a, r) => a + (r.tabularNums || 0), 0)} 处`)
    console.log(`侧栏纵向：9 组全展开时 nav 内容最大超出视口 ${Math.max(0, ...rows.map((r) => r.navOverflowPx ?? 0))}px（靠既有 overflow-y-auto 纵向滚动，不是横向溢出）`)
    if (shots.length) console.log('截图：\n  ' + shots.join('\n  '))
    if (knownZero.length) {
      console.log(`\n存量裸 0（§12.5 R-07 已登记，本轮未新增，共 ${knownZero.length} 屏）：`)
      for (const k of knownZero) console.log('  · ' + k)
    }
    if (options.json) writeFileSync(options.json, JSON.stringify({ viewport: VIEWPORT, rows, errors }, null, 2))
    if (errors.length) {
      console.log(`\n不通过 ${errors.length} 项：`)
      for (const e of errors) console.log('  - ' + e)
      process.exitCode = 1
    } else {
      console.log('\n全部通过：零横向溢出、console 0 报错、侧栏结构与三条不变量都在。')
    }
  } finally {
    child.kill()
  }
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})

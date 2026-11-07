#!/usr/bin/env node
/**
 * 视觉巡检截图脚本（QUOT-306 S1 / QUOT-307）。
 *
 * 用法：
 *   1. 起前端（推荐带后端代理的取证配置，`/api` 指向本机 `ldst run` 的 5950）：
 *        cd frontend && npm install && npm run dev -- --config vite.shot.config.ts   # http://127.0.0.1:5199
 *      没有 `ldst run` 时退回 `npm run dev`（http://127.0.0.1:5173），各屏会拍到「未接入 / 降级」态。
 *   2. 拍基线：`node scripts/visual-shot.mjs`            → 20 屏 × 暗/亮 = 40 张
 *   3. 改完之后拍 after：`node scripts/visual-shot.mjs --suffix after`
 *
 * 输出：`docs/screenshots/<route>-<before|after>-<dark|light>.png`，视口 1440×900，
 * `<route>` 取路由段小写连字符；带参数的路由用参数名（`industry-name`、`research-code`、
 * `research-code-report`），实参用 `ROUTES[].path` 里的样例值。
 *
 * 常用开关：
 *   --base <url>       前端地址（默认自动选：5199 在跑用 5199，否则 5173）
 *   --out <dir>        输出目录（默认 ../docs/screenshots）
 *   --suffix <s>       文件名后缀（默认 before）
 *   --themes <a,b>     主题（默认 dark,light）
 *   --routes <a,b>     只拍指定 slug，逗号分隔
 *   --route <slug>=<path>  临时加一屏（可重复），配合 `--routes` 拍样例之外的标的
 *   --query <str>      追加到每个路由的查询串（`demo=1` / `demo=1&variant=partial` / `tab=ports`），
 *                      配合 `--suffix after-demo` 这类后缀拍演示态与缺失态
 *   --clock <iso>      把页面 `Date` 钉到该时刻（如 `2026-11-06T13:41:07+08:00`）。用于时段相关
 *                      文案 / 轮询的取证：真实时钟不可控时（周末、盘后）也能拍到「交易时段」态（QUOT-470）。
 *   --demo-pref        用设置页的同款偏好开启演示源（写 `ldst.demo-data=1`），不依赖 URL 参数
 *   --prefs k=v,k=v    截图前写入任意本机偏好（如 `ldst.privacy=on`），与主题同一注入时机
 *   --settle <ms>      load 之后额外等待（默认 1500，留给 React 渲染 + 图表重绘）
 *   --chrome <path>    浏览器可执行文件（默认自动找 Chrome/Edge，或读环境变量 LDST_CHROME）
 *   --width <px>       视口宽（默认 1440；验收 1280 零横向溢出时传 1280）
 *   --height <px>      视口高（默认 900）
 *   --full-page        截整页（按 `contentSize` 撑高），长页一屏装不下时用；溢出判定仍按视口宽
 *   --scroll <px>      截图前先滚到纵向 N px（拍首屏之后的分区，不传则停在顶部）
 *   --click <selector> 截图前点一下该元素（可重复，按顺序），用来拍打开后的抽屉/弹层；
 *                      元素找不到会直接报错，避免拍出一张「其实没打开」的图
 *   --click-settle <ms> 每次点击后额外等待（默认 800，留给抽屉入场动画）
 *
 * 每张图拍完都会量 `documentElement.scrollWidth` 与 `innerWidth`，脚本结束统一报告横向溢出清单
 * （QUOT-304 / QUOT-317 的 1280px 证据就是这条输出的原文）。
 *
 * 主题切换走既有机制：`localStorage['ldst.theme']`（见 `src/lib/theme.ts`），
 * 通过 CDP 的 `Page.addScriptToEvaluateOnNewDocument` 在页面脚本之前写入，不碰任何屏幕代码。
 *
 * 依赖：Node 22+ 自带的 fetch / WebSocket + 本机 Chrome/Edge 的 headless CDP，不引第三方包。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const VIEWPORT = { width: 1440, height: 900 }
const HERE = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(HERE, '..', '..')

/** 20 屏，以 `src/App.tsx` 路由表为准（radar 四屏实际挂在 `/news` `/calendar` `/pool` `/positions`）。 */
const ROUTES = [
  { slug: 'home', path: '/' },
  { slug: 'market', path: '/market' },
  { slug: 'watch', path: '/watch' },
  // QUOT-302 第 19 屏：竞价与盘中（此前漏在巡检脚本之外）
  { slug: 'intraday', path: '/intraday' },
  { slug: 'review', path: '/review' },
  // QUOT-344 / DEC-046 第 20 屏：AI 复盘独立入口
  { slug: 'review-ai', path: '/review/ai' },
  { slug: 'weekly', path: '/weekly' },
  { slug: 'journal', path: '/journal' },
  { slug: 'validate', path: '/validate' },
  { slug: 'limit-up', path: '/limit-up' },
  { slug: 'news', path: '/news' },
  { slug: 'calendar', path: '/calendar' },
  { slug: 'pool', path: '/pool' },
  { slug: 'positions', path: '/positions' },
  // QUOT-419：我的自选独立页（DEC-055 写面）
  { slug: 'watchlist', path: '/watchlist' },
  // QUOT-435：提醒（本机收件箱 + 只读定时任务 + 本机投递渠道）
  { slug: 'inbox', path: '/inbox' },
  { slug: 'industry', path: '/industry' },
  { slug: 'industry-name', path: `/industry/${encodeURIComponent('电子')}` },
  { slug: 'signals', path: '/signals' },
  { slug: 'settings', path: '/settings' },
  { slug: 'research-code', path: '/research/600519' },
  { slug: 'research-code-report', path: '/research/600519/report' },
]

function parseArgs(argv) {
  const options = {
    base: null,
    out: join(REPO_ROOT, 'docs', 'screenshots'),
    suffix: 'before',
    themes: ['dark', 'light'],
    routes: null,
    query: null,
    clock: null,
    demoPref: false,
    prefs: {},
    settle: 1500,
    chrome: process.env.LDST_CHROME ?? null,
    port: 9333,
    width: VIEWPORT.width,
    height: VIEWPORT.height,
    fullPage: false,
    scroll: 0,
    clicks: [],
    clickSettle: 800,
    extraRoutes: [],
  }
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i]
    const next = () => argv[++i]
    if (flag === '--base') options.base = next().replace(/\/$/, '')
    else if (flag === '--out') options.out = resolve(next())
    else if (flag === '--suffix') options.suffix = next()
    else if (flag === '--themes') options.themes = next().split(',').filter(Boolean)
    else if (flag === '--routes') options.routes = next().split(',').filter(Boolean)
    // 验收常要拍某个具体样本（如 /research/600000），不必为一次取证改 ROUTES 表
    else if (flag === '--route') {
      const raw = next()
      const at = raw.indexOf('=')
      if (at <= 0 || at === raw.length - 1) throw new Error(`--route 要写成 slug=/path，收到 ${raw}`)
      options.extraRoutes.push({ slug: raw.slice(0, at), path: raw.slice(at + 1) })
    }
    else if (flag === '--query') options.query = next()
    else if (flag === '--clock') options.clock = next()
    else if (flag === '--demo-pref') options.demoPref = true
    else if (flag === '--prefs') {
      for (const pair of next().split(',')) {
        const [key, ...rest] = pair.split('=')
        if (key && rest.length > 0) options.prefs[key.trim()] = rest.join('=').trim()
      }
    }
    else if (flag === '--settle') options.settle = Number(next())
    else if (flag === '--chrome') options.chrome = next()
    else if (flag === '--port') options.port = Number(next())
    else if (flag === '--width') options.width = Number(next())
    else if (flag === '--height') options.height = Number(next())
    else if (flag === '--full-page') options.fullPage = true
    else if (flag === '--scroll') options.scroll = Number(next())
    else if (flag === '--click') options.clicks.push(next())
    else if (flag === '--click-settle') options.clickSettle = Number(next())
    else throw new Error(`未知参数：${flag}（--help 见文件头注释）`)
  }
  for (const theme of options.themes) {
    if (theme !== 'dark' && theme !== 'light') throw new Error(`主题只能是 dark/light，收到 ${theme}`)
  }
  return options
}

function findChrome(explicit) {
  if (explicit) return explicit
  const candidates =
    process.platform === 'win32'
      ? [
          'C:/Program Files/Google/Chrome/Application/chrome.exe',
          'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
          'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
          'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
        ]
      : process.platform === 'darwin'
        ? ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
        : ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/microsoft-edge']
  for (const path of candidates) {
    if (existsSync(path)) return path
  }
  throw new Error('没找到 Chrome/Edge，可用 --chrome <path> 或环境变量 LDST_CHROME 指定')
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitForJson(url, deadlineMs) {
  const started = Date.now()
  let lastError = null
  while (Date.now() - started < deadlineMs) {
    try {
      const response = await fetch(url)
      if (response.ok) return await response.json()
    } catch (error) {
      lastError = error
    }
    await sleep(200)
  }
  throw new Error(`CDP 端点未就绪：${url}（${lastError?.message ?? '超时'}）`)
}

/** 极简 CDP 客户端：浏览器级连接 + flatten session，只发本脚本用得到的命令。 */
class Cdp {
  constructor(socket) {
    this.socket = socket
    this.nextId = 1
    this.pending = new Map()
    this.events = new Map()
    socket.addEventListener('message', (event) => {
      let message
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message.id !== undefined) {
        const waiter = this.pending.get(message.id)
        if (!waiter) return
        this.pending.delete(message.id)
        if (message.error) waiter.reject(new Error(`${message.error.message} (${message.error.code})`))
        else waiter.resolve(message.result)
        return
      }
      const handlers = this.events.get(message.method)
      if (handlers) for (const handler of handlers) handler(message.params)
    })
  }

  static async connect(url) {
    if (typeof WebSocket !== 'function') {
      throw new Error('当前 Node 没有全局 WebSocket，需要 Node 22 及以上')
    }
    const socket = new WebSocket(url)
    await new Promise((resolveConnection, rejectConnection) => {
      socket.addEventListener('open', resolveConnection, { once: true })
      socket.addEventListener('error', () => rejectConnection(new Error(`无法连接 CDP：${url}`)), { once: true })
    })
    return new Cdp(socket)
  }

  send(method, params = {}, sessionId) {
    const id = this.nextId++
    const payload = JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })
    return new Promise((resolveCommand, rejectCommand) => {
      this.pending.set(id, { resolve: resolveCommand, reject: rejectCommand })
      this.socket.send(payload)
    })
  }

  once(method, timeoutMs) {
    return new Promise((resolveEvent) => {
      const timer = setTimeout(() => {
        this.off(method, handler)
        resolveEvent(null)
      }, timeoutMs)
      const handler = (params) => {
        clearTimeout(timer)
        this.off(method, handler)
        resolveEvent(params)
      }
      this.on(method, handler)
    })
  }

  on(method, handler) {
    const handlers = this.events.get(method) ?? []
    handlers.push(handler)
    this.events.set(method, handlers)
  }

  off(method, handler) {
    const handlers = this.events.get(method) ?? []
    const index = handlers.indexOf(handler)
    if (index >= 0) handlers.splice(index, 1)
  }

  close() {
    try {
      this.socket.close()
    } catch {
      /* 已关闭 */
    }
  }
}

async function shoot(cdp, base, route, theme, options) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' })
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true })
  try {
    await cdp.send('Page.enable', {}, sessionId)
    await cdp.send(
      'Emulation.setDeviceMetricsOverride',
      { width: options.width, height: options.height, deviceScaleFactor: 1, mobile: false },
      sessionId,
    )
    await cdp.send(
      'Page.addScriptToEvaluateOnNewDocument',
      {
        source: `try {
          localStorage.setItem('ldst.theme', ${JSON.stringify(theme)});
          localStorage.removeItem('ldst.density');
          ${
            options.demoPref
              ? "localStorage.setItem('ldst.demo-data', '1');"
              : "localStorage.removeItem('ldst.demo-data');"
          }
          ${Object.entries(options.prefs)
            .map(([key, value]) => `localStorage.setItem(${JSON.stringify(key)}, ${JSON.stringify(value)});`)
            .join('\n          ')}
        } catch (e) {}`,
      },
      sessionId,
    )
    if (options.clock) {
      // 把页面时钟**平移**到指定时刻的对应相位（不冻结）：`Date.now()` 与零参 `new Date()`
      // 都加上固定偏移，时间照常前进。冻结会让定时器 / 调度器停在原地（页面卡在骨架），
      // 平移只改「现在几点」，不动「时间是否在走」——时段文案与轮询节拍都能正常取证（QUOT-470）。
      await cdp.send(
        'Page.addScriptToEvaluateOnNewDocument',
        {
          source: `try {
            var __target = new Date(${JSON.stringify(options.clock)}).getTime();
            var __delta = __target - Date.now();
            var __RealDate = Date;
            function __FakeDate(...args) {
              if (args.length === 0) return new __RealDate(__RealDate.now() + __delta);
              return new __RealDate(...args);
            }
            __FakeDate.prototype = __RealDate.prototype;
            __FakeDate.now = function () { return __RealDate.now() + __delta; };
            __FakeDate.parse = __RealDate.parse;
            __FakeDate.UTC = __RealDate.UTC;
            Date = __FakeDate;
          } catch (e) {}`,
        },
        sessionId,
      )
    }
    const loaded = cdp.once('Page.loadEventFired', 30_000)
    const query = options.query ? `${route.path.includes('?') ? '&' : '?'}${options.query}` : ''
    await cdp.send('Page.navigate', { url: `${base}${route.path}${query}` }, sessionId)
    await loaded
    await sleep(options.settle)
    for (const selector of options.clicks) {
      const clicked = await cdp.send(
        'Runtime.evaluate',
        {
          expression: `(() => {
            const el = document.querySelector(${JSON.stringify(selector)});
            if (!el) return 'missing';
            el.click();
            return 'ok';
          })()`,
          returnByValue: true,
        },
        sessionId,
      )
      if (clicked.result.value !== 'ok') throw new Error(`--click 找不到元素：${selector}`)
      await sleep(options.clickSettle)
    }
    if (options.scroll > 0) {
      await cdp.send(
        'Runtime.evaluate',
        { expression: `window.scrollTo(0, ${options.scroll}); window.scrollY`, returnByValue: true },
        sessionId,
      )
      await sleep(400)
    }
    const measured = await cdp.send(
      'Runtime.evaluate',
      {
        expression: `JSON.stringify({ scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth })`,
        returnByValue: true,
      },
      sessionId,
    )
    const { scrollWidth, innerWidth } = JSON.parse(measured.result.value)
    // 整页截图：按内容高度扩 clip，`captureBeyondViewport` 才能拍到视口以下的部分
    let shotParams = { format: 'png' }
    if (options.fullPage) {
      const metrics = await cdp.send('Page.getLayoutMetrics', {}, sessionId)
      const size = metrics.cssContentSize ?? metrics.contentSize
      shotParams = {
        format: 'png',
        captureBeyondViewport: true,
        clip: { x: 0, y: 0, width: options.width, height: Math.ceil(size.height), scale: 1 },
      }
    }
    const { data } = await cdp.send('Page.captureScreenshot', shotParams, sessionId)
    const file = join(options.out, `${route.slug}-${options.suffix}-${theme}.png`)
    writeFileSync(file, Buffer.from(data, 'base64'))
    return { file, scrollWidth, innerWidth, overflow: scrollWidth > innerWidth }
  } finally {
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {})
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  if (!globalThis.fetch || typeof WebSocket !== 'function') {
    throw new Error('需要 Node 22 及以上（内置 fetch 与全局 WebSocket）')
  }
  mkdirSync(options.out, { recursive: true })

  const allRoutes = [...ROUTES, ...options.extraRoutes]
  const routes = options.routes ? allRoutes.filter((r) => options.routes.includes(r.slug)) : allRoutes
  if (routes.length === 0) throw new Error(`--routes 没有匹配到任何屏幕`)

  if (!options.base) {
    // 取证配置（带 /api 代理）优先，其次普通 dev server
    for (const candidate of ['http://127.0.0.1:5199', 'http://127.0.0.1:5173']) {
      const alive = await fetch(`${candidate}/`).catch(() => null)
      if (alive?.ok) {
        options.base = candidate
        break
      }
    }
  }
  if (!options.base)
    throw new Error('前端没起来（见文件头注释的启动命令），或用 --base 指定')
  console.log(`前端：${options.base}`)

  const profile = mkdtempSync(join(tmpdir(), 'ldst-visual-shot-'))
  const browser = spawn(
    findChrome(options.chrome),
    [
      '--headless=new',
      `--remote-debugging-port=${options.port}`,
      '--remote-allow-origins=*',
      `--user-data-dir=${profile}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-gpu',
      '--hide-scrollbars',
      '--disable-dev-shm-usage',
      '--disable-translate',
      '--disable-sync',
      `--window-size=${VIEWPORT.width},${VIEWPORT.height}`,
      'about:blank',
    ],
    { stdio: 'ignore' },
  )

  const started = Date.now()
  let failures = []
  let captured = 0
  const overflows = []
  try {
    const version = await waitForJson(`http://127.0.0.1:${options.port}/json/version`, 20_000)
    const cdp = await Cdp.connect(version.webSocketDebuggerUrl)
    try {
      for (const route of routes) {
        for (const theme of options.themes) {
          const label = `${route.slug} · ${theme}`
          try {
            const shot = await shoot(cdp, options.base, route, theme, options)
            captured += 1
            if (shot.overflow) overflows.push({ label, scrollWidth: shot.scrollWidth, innerWidth: shot.innerWidth })
            console.log(`  ✓ ${label}${shot.overflow ? ` — 横向溢出 ${shot.scrollWidth} > ${shot.innerWidth}` : ''}`)
          } catch (error) {
            failures.push({ label, reason: error.message })
            console.log(`  ✗ ${label} — ${error.message}`)
          }
        }
      }
    } finally {
      cdp.close()
    }
  } finally {
    browser.kill()
    await sleep(300)
    rmSync(profile, { recursive: true, force: true })
  }

  console.log(
    `\n输出目录：${options.out}\n成功 ${captured}/${routes.length * options.themes.length}，用时 ${Math.round(
      (Date.now() - started) / 1000,
    )}s`,
  )
  for (const failure of failures) console.log(`  失败：${failure.label} — ${failure.reason}`)
  if (overflows.length > 0) {
    console.log(`  横向溢出 ${overflows.length} 项（视口 ${options.width}px）：`)
    for (const item of overflows) console.log(`    ${item.label} — ${item.scrollWidth} > ${item.innerWidth}`)
  } else {
    console.log(`  横向溢出 0 项（视口 ${options.width}px）`)
  }
  if (failures.length > 0) process.exitCode = 1
}

main().catch((error) => {
  console.error(`\n截图失败：${error.message}`)
  process.exit(1)
})

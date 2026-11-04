#!/usr/bin/env node
/**
 * QUOT-397 取证截图（一次性脚本，随任务归档 `docs/screenshots/quot-397/`）。
 *
 * 拍壳层取证要的两类证据：
 * - 顶部首屏：1440×900 / 1920×1080 × 深/浅
 * - 滚到页面中部：同样的视口与主题，证明页头（sticky）仍在视口顶部
 *
 * 用法：先起 `npm run dev -- --config vite.shot.config.ts`（5199），再
 *   node scripts/quot-397-shot.mjs --suffix before
 *   node scripts/quot-397-shot.mjs --suffix after
 *
 * 实现与 `visual-shot.mjs` 同源：headless Chrome + CDP，不引第三方包。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(HERE, '..', '..', 'docs', 'screenshots', 'quot-397')
const BASE = process.env.LDST_SHOT_BASE ?? 'http://127.0.0.1:5199'
const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]
const THEMES = ['dark', 'light']
// 只拍会滚动的页：settings 首屏不滚，拍不出「滚动后页头仍在」的证据
const PAGES = [
  { slug: 'home', path: '/' },
  // 1920×1080 下市场各页首屏装得下、不产生滚动，滚动证据由 home / industry 两页承担（1440 下 market 仍可滚）
  { slug: 'market', path: '/market' },
  { slug: 'industry', path: '/industry/电子' },
]

const args = process.argv.slice(2)
const suffix = args.includes('--suffix') ? args[args.indexOf('--suffix') + 1] : 'before'

function findChrome() {
  if (process.env.LDST_CHROME) return process.env.LDST_CHROME
  const candidates = [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  ]
  return candidates.find((p) => existsSync(p))
}

const chrome = findChrome()
if (!chrome) {
  console.error('找不到 Chrome/Edge，设置 LDST_CHROME 后重试')
  process.exit(1)
}

const profile = mkdtempSync(join(tmpdir(), 'quot397-'))
mkdirSync(OUT, { recursive: true })

const child = spawn(
  chrome,
  [
    '--headless=new',
    '--disable-gpu',
    '--hide-scrollbars',
    '--no-first-run',
    '--no-default-browser-check',
    '--remote-debugging-port=0',
    `--user-data-dir=${profile}`,
    'about:blank',
  ],
  { stdio: ['ignore', 'ignore', 'pipe'] },
)

const wsUrl = await new Promise((resolveUrl, reject) => {
  let buffer = ''
  const timer = setTimeout(() => reject(new Error('Chrome 启动超时')), 20000)
  child.stderr.on('data', (chunk) => {
    buffer += chunk.toString()
    const match = /DevTools listening on (ws:\/\/\S+)/.exec(buffer)
    if (match) {
      clearTimeout(timer)
      resolveUrl(match[1])
    }
  })
})

const port = new URL(wsUrl).port
const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
const pageTarget = targets.find((t) => t.type === 'page')
const ws = new WebSocket(pageTarget.webSocketDebuggerUrl)
await new Promise((ok) => (ws.onopen = ok))

let seq = 0
const pending = new Map()
ws.onmessage = (event) => {
  const message = JSON.parse(event.data)
  if (message.id && pending.has(message.id)) {
    const { resolve: done, reject } = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) reject(new Error(JSON.stringify(message.error)))
    else done(message.result)
  }
}

const send = (method, params = {}) =>
  new Promise((done, reject) => {
    const id = ++seq
    pending.set(id, { resolve: done, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })

const evaluate = async (expression) => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  return result.result.value
}

const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms))

async function shot(file) {
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
  writeFileSync(join(OUT, file), Buffer.from(data, 'base64'))
}

await send('Page.enable')
await send('Runtime.enable')
await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false })

let count = 0
const measurements = []

for (const viewport of VIEWPORTS) {
  for (const theme of THEMES) {
    for (const page of PAGES) {
      await send('Emulation.setDeviceMetricsOverride', {
        width: viewport.width,
        height: viewport.height,
        deviceScaleFactor: 1,
        mobile: false,
      })
      await send('Page.navigate', { url: `${BASE}${encodeURI(page.path)}?demo=1` })
      await sleep(1200)
      await evaluate(`localStorage.setItem('ldst.theme', '${theme}'); location.reload()`)
      await sleep(2000)
      await evaluate(`(() => {
        const box = document.querySelector('main').parentElement
        box.scrollTop = 0
        box.scrollTop = 0
      })()`)

      const tag = `${page.slug}-${viewport.width}x${viewport.height}-${theme}-${suffix}`
      await shot(`${tag}-top.png`)
      // 滚动容器是 Layout 里 `overflow-y-auto` 那一列（main 的父节点）
      await evaluate(`document.querySelector('main').parentElement.scrollTop = 600`)
      await sleep(500)
      await shot(`${tag}-mid.png`)

      const layout = await evaluate(`(() => {
        const main = document.querySelector('main')
        const rect = main?.getBoundingClientRect()
        const header = document.querySelector('header')
        const headerRect = header?.getBoundingClientRect()
        const doc = document.documentElement
        return {
          scrollY: document.querySelector('main').parentElement.scrollTop,
          main: rect ? { left: Math.round(rect.left), right: Math.round(rect.right), width: Math.round(rect.width) } : null,
          headerTop: headerRect ? Math.round(headerRect.top) : null,
          headerH1: (() => { const h = document.querySelector('header h1'); return h ? Math.round(h.getBoundingClientRect().left) : null })(),
          headerScrollPos: getComputedStyle(header).position,
          viewportWidth: doc.clientWidth,
          overflowX: doc.scrollWidth - doc.clientWidth,
        }
      })()`)
      measurements.push({ tag, ...layout })
      count += 2
    }
  }
}

ws.close()
child.kill()
await sleep(800)
try {
  rmSync(profile, { recursive: true, force: true, maxRetries: 5 })
} catch {
  /* 临时 profile 清理失败不影响取证结果 */
}

console.log(JSON.stringify(measurements, null, 2))
console.log(`\n输出目录：${OUT}\n截图 ${count} 张`)

#!/usr/bin/env node
/**
 * QUOT-480 取证截图：设置页 FinanceCoffer 卡「编辑配置」→ 配置抽屉（一次性脚本，随任务归档）。
 *
 * 验收要的证据是「FC 配置抽屉不再是空抽屉：34 个白名单键 + 生效路径说明」，
 * 以及设置页总览。静态 visual-shot 不会点进 FC 的抽屉，这里补一段 CDP：
 * 导航 /settings → 找 FinanceCoffer 卡 → 点「编辑配置」→ 等抽屉滑入 → 截 1440×900 × 深/浅。
 *
 * 用法：先起前端（LDST_API_TARGET 指向带 QUOT-480 代码的后端），再
 *   node scripts/quot-480-shot.mjs
 * 输出：docs/screenshots/quot-480/settings-<board|config>-<dark|light>.png
 *
 * 实现与 quot-474-shot.mjs 同源：headless Chrome + CDP，不引第三方包。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(HERE, '..', '..', 'docs', 'screenshots', 'quot-480')
const VIEWPORT = { width: 1440, height: 900 }
const THEMES = ['dark', 'light']

function findChrome() {
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
  for (const path of candidates) if (existsSync(path)) return path
  throw new Error('没找到 Chrome/Edge')
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitForJson(url, deadlineMs) {
  const started = Date.now()
  let last = null
  while (Date.now() - started < deadlineMs) {
    try {
      const res = await fetch(url)
      if (res.ok) return await res.json()
    } catch (error) {
      last = error
    }
    await sleep(200)
  }
  throw new Error(`CDP 端点未就绪：${url}（${last?.message ?? '超时'}）`)
}

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
        if (message.error) waiter.reject(new Error(message.error.message))
        else waiter.resolve(message.result)
        return
      }
      const handlers = this.events.get(message.method)
      if (handlers) for (const h of handlers) h(message.params)
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
    const payload = JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })
    return new Promise((res, rej) => {
      this.pending.set(id, { resolve: res, reject: rej })
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
    const i = handlers.indexOf(handler)
    if (i >= 0) handlers.splice(i, 1)
  }
  close() {
    try {
      this.socket.close()
    } catch {
      /* 已关闭 */
    }
  }
}

/** 找 FinanceCoffer 卡片里的「编辑配置」按钮：先按卡片文本筛按钮，再点它。 */
const CLICK_FC_CONFIG = `(() => {
  const btns = Array.from(document.querySelectorAll('button')).filter((b) => b.textContent.trim() === '编辑配置')
  const btn = btns.find((b) => (b.closest('li')?.textContent ?? '').includes('FinanceCoffer'))
  if (!btn) {
    return btns.length === 0 ? 'no-config-button' : 'fc-card-not-found'
  }
  btn.click()
  return 'clicked'
})()`

/** 读抽屉证据：键输入框数量、抽屉文本摘要（生效路径 / 0600 提示 / 空白名单说明）。 */
const READ_DRAWER = `(() => {
  const dialog = document.querySelector('[role="dialog"]')
  if (!dialog) return JSON.stringify({ open: false })
  const inputs = dialog.querySelectorAll('input').length
  const text = dialog.textContent ?? ''
  return JSON.stringify({
    open: true,
    title: (dialog.querySelector('h2')?.textContent ?? '').trim(),
    inputs,
    hasEnvPath: text.includes('deploy/.env'),
    hasWhitelistNote: text.includes('白名单'),
    hasCreateNote: text.includes('保存后创建'),
    hasEmptyWhitelistNote: text.includes('没有可写配置'),
    head: text.replace(/\\s+/g, ' ').slice(0, 120),
  })
})()`

async function shoot(cdp, base, theme, scenario) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' })
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true })
  try {
    await cdp.send('Page.enable', {}, sessionId)
    await cdp.send(
      'Emulation.setDeviceMetricsOverride',
      { width: VIEWPORT.width, height: VIEWPORT.height, deviceScaleFactor: 1, mobile: false },
      sessionId,
    )
    await cdp.send(
      'Page.addScriptToEvaluateOnNewDocument',
      { source: `try { localStorage.setItem('ldst.theme', ${JSON.stringify(theme)}); } catch (e) {}` },
      sessionId,
    )
    const loaded = cdp.once('Page.loadEventFired', 30_000)
    await cdp.send('Page.navigate', { url: `${base}${scenario.path}` }, sessionId)
    await loaded
    await sleep(9000)

    let info = { open: false }
    if (scenario.click) {
      const clickResult = await cdp.send(
        'Runtime.evaluate',
        { expression: scenario.click, returnByValue: true },
        sessionId,
      )
      if (clickResult.result.value !== 'clicked') {
        throw new Error(`${scenario.name}：点不到入口（${clickResult.result.value}）`)
      }
      await sleep(900)
    }
    const read = await cdp.send('Runtime.evaluate', { expression: READ_DRAWER, returnByValue: true }, sessionId)
    info = JSON.parse(read.result.value)
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, sessionId)
    const file = join(OUT, `${scenario.name}-${theme}.png`)
    writeFileSync(file, Buffer.from(data, 'base64'))
    return { file, info }
  } finally {
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {})
  }
}

/** 两个场景：设置页总览；点开 FinanceCoffer 的配置抽屉。 */
const SCENARIOS = [
  { name: 'settings-board', path: '/settings', click: null },
  { name: 'settings-config', path: '/settings', click: CLICK_FC_CONFIG },
]

async function main() {
  mkdirSync(OUT, { recursive: true })
  let base = process.env.LDST_SHOT_BASE
  if (!base) {
    for (const candidate of ['http://127.0.0.1:5199', 'http://127.0.0.1:5173']) {
      const alive = await fetch(`${candidate}/`).catch(() => null)
      if (alive?.ok) {
        base = candidate
        break
      }
    }
  }
  if (!base) throw new Error('前端没起来（npm run dev），或用 LDST_SHOT_BASE 指定')

  const profile = mkdtempSync(join(tmpdir(), 'ldst-480-shot-'))
  const browser = spawn(
    findChrome(),
    [
      '--headless=new',
      '--remote-debugging-port=9347',
      '--remote-allow-origins=*',
      `--user-data-dir=${profile}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-gpu',
      '--hide-scrollbars',
      '--disable-dev-shm-usage',
      `--window-size=${VIEWPORT.width},${VIEWPORT.height}`,
      'about:blank',
    ],
    { stdio: 'ignore' },
  )
  try {
    const version = await waitForJson('http://127.0.0.1:9347/json/version', 20_000)
    const cdp = await Cdp.connect(version.webSocketDebuggerUrl)
    try {
      for (const scenario of SCENARIOS) {
        for (const theme of THEMES) {
          const shot = await shoot(cdp, base, theme, scenario)
          console.log(`  ✓ ${scenario.name} · ${theme} → ${shot.file}`)
          console.log(`     ${JSON.stringify(shot.info)}`)
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
}

main().catch((error) => {
  console.error(`\n截图失败���${error.message}`)
  process.exit(1)
})

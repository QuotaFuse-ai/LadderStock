#!/usr/bin/env node
/**
 * QUOT-602 取证探针：量对话态 / 研究台的左右留白带、composer 是否在视口内。
 *
 * 用法：node scripts/quot-602-probe.mjs --base http://127.0.0.1:5199 --mode chat [--select <sessionId>] [--width 1440 --height 900]
 * 输出：JSON —— 壳层 / main / shell / 列表 / 聊天区的 rect，以及 composer rect 与视口的关系。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

let serverGet = null
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function arg(name, fallback = null) {
  const index = process.argv.indexOf(`--${name}`)
  return index >= 0 ? process.argv[index + 1] : fallback
}

function findChrome() {
  for (const p of [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  ]) {
    if (existsSync(p)) return p
  }
  throw new Error('no chrome')
}

async function main() {
  const base = arg('base', 'http://127.0.0.1:5199')
  const mode = arg('mode', 'chat')
  const width = Number(arg('width', '1440'))
  const height = Number(arg('height', '900'))
  const select = arg('select', '')
  const port = 9333
  const profile = mkdtempSync(join(tmpdir(), 'ldst-602-probe-'))
  const browser = spawn(findChrome(), [
    '--headless=new',
    `--remote-debugging-port=${port}`,
    '--remote-allow-origins=*',
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--disable-gpu',
    '--hide-scrollbars',
    `--window-size=${width},${height}`,
    'about:blank',
  ])
  try {
    let version = null
    for (let i = 0; i < 100 && !version; i += 1) {
      version = await fetch(`http://127.0.0.1:${port}/json/version`).then((r) => r.json()).catch(() => null)
      if (!version) await sleep(200)
    }
    const ws = new WebSocket(version.webSocketDebuggerUrl)
    await new Promise((r) => ws.addEventListener('open', r, { once: true }))
    let nextId = 1
    const pending = new Map()
    const events = new Map()
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data)
      if (msg.id && pending.has(msg.id)) {
        const { resolve, reject } = pending.get(msg.id)
        pending.delete(msg.id)
        msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result)
      } else if (msg.method && events.has(msg.method)) {
        for (const h of events.get(msg.method)) h(msg.params)
      }
    })
    const send = (method, params = {}, sessionId) =>
      new Promise((resolve, reject) => {
        const id = nextId++
        pending.set(id, { resolve, reject })
        ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }))
      })
    const once = (method, timeoutMs) =>
      new Promise((resolve) => {
        const timer = setTimeout(() => resolve(null), timeoutMs)
        const h = (p) => {
          clearTimeout(timer)
          events.get(method).splice(events.get(method).indexOf(h), 1)
          resolve(p)
        }
        if (!events.has(method)) events.set(method, [])
        events.get(method).push(h)
      })

    const { targetId } = await send('Target.createTarget', { url: 'about:blank' })
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true })
    await send('Page.enable', {}, sessionId)
    await send('Runtime.enable', {}, sessionId)
    await send(
      'Emulation.setDeviceMetricsOverride',
      { width, height, deviceScaleFactor: 1, mobile: false },
      sessionId,
    )
    await send(
      'Page.addScriptToEvaluateOnNewDocument',
      {
        source: `try {
          localStorage.setItem('ldst.theme', 'dark');
          localStorage.setItem('home_mode', ${JSON.stringify(mode)});
          ${select ? `sessionStorage.setItem('quot602-select', ${JSON.stringify(select)});` : ''}
        } catch (e) {}`,
      },
      sessionId,
    )
    serverGet = async (path) => JSON.parse(await (await fetch(`${base}${path}`)).text())
    const loaded = once('Page.loadEventFired', 30000)
    await send('Page.navigate', { url: `${base}/` }, sessionId)
    await loaded
    await sleep(1800)
    const evaluate = async (expression) => {
      const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, sessionId)
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
      return result.result.value
    }
    if (select) {
      const list = await serverGet('/api/conversation/sessions')
      const target = list.sessions.find((s) => s.title.includes(select) || s.session_id === select) ?? list.sessions[0]
      if (!target) throw new Error('no session matches')
      console.log('selected:', target.session_id, target.title, 'turns', target.turns)
      const clicked = await evaluate(`(() => {
        const form = document.querySelector('[data-testid="composer"] textarea');
        if (!form) return 'no-composer';
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
        setter.call(form, '你好');
        form.dispatchEvent(new Event('input', { bubbles: true }));
        form.closest('form').requestSubmit();
        return 'submitted';
      })()`)
      console.log('list:', clicked)
      await sleep(4000)
    }
    const probe = await evaluate(`(() => {
      const rect = (el) => {
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height) };
      };
      const pick = (sel) => rect(document.querySelector(sel));
      const todos = document.querySelector('[data-testid="todo-movers-global"]') ?? null;
      const main = document.querySelector('#main');
      const mainStyle = main ? getComputedStyle(main) : null;
      const chatSection = document.querySelector('section[aria-label="首页对话"]');
      const header = chatSection?.querySelector('header');
      const scroller = chatSection?.querySelector('.overflow-y-auto') ?? null;
      const composer = document.querySelector('[data-testid="composer"]');
      const list = document.querySelector('nav[aria-label="对话会话"]');
      const info = {
        viewport: { width: innerWidth, height: innerHeight },
        sidebar: pick('aside'),
        main: pick('#main'),
        mainPadding: mainStyle ? { left: mainStyle.paddingLeft, right: mainStyle.paddingRight, maxWidth: mainStyle.maxWidth } : null,
        shell: rect(main?.firstElementChild?.nextElementSibling?.querySelector('div[class*="flex"]') ?? null),
        chatSection: rect(chatSection),
        chatHeader: rect(header),
        chatScroller: rect(scroller),
        composer: rect(composer),
        sessionList: rect(list),
        docScrollWidth: document.documentElement.scrollWidth,
        scrollerScrollHeight: scroller ? scroller.scrollHeight : null,
        scrollerClientHeight: scroller ? scroller.clientHeight : null,
        xBands: (() => {
          const sc = document.querySelector('div.flex.min-h-0.min-w-0.flex-1.flex-col.overflow-y-auto');
          return sc ? { scrollTop: Math.round(sc.scrollTop), scrollHeight: sc.scrollHeight, clientHeight: sc.clientHeight } : null;
        })(),
      };
      if (composer) {
        const r = composer.getBoundingClientRect();
        info.composerInViewport = r.top >= 0 && r.bottom <= innerHeight && r.height > 0;
        info.composerBelowViewport = r.top >= innerHeight;
      }
      return JSON.stringify(info, null, 2);
    })()`)
    console.log(probe)
  } finally {
    browser.kill()
    await sleep(300)
    rmSync(profile, { recursive: true, force: true })
  }
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})

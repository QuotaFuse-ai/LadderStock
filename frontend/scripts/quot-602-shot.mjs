#!/usr/bin/env node
/**
 * QUOT-602 取证截图：对话态 / 研究台 / composer 三态。
 *
 * 用法：node scripts/quot-602-shot.mjs --base http://127.0.0.1:5199 --out <dir>
 * 输出：<dir>/quot-602-home-chat-1440x900.png、quot-602-home-desk-1440x900.png、
 *       quot-602-composer-before.png / -after.png / -long.png
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

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
  const out = arg('out', '.')
  mkdirSync(out, { recursive: true })
  const port = 9334
  const profile = mkdtempSync(join(tmpdir(), 'ldst-602-shot-'))
  const browser = spawn(findChrome(), [
    '--headless=new',
    `--remote-debugging-port=${port}`,
    '--remote-allow-origins=*',
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--disable-gpu',
    '--hide-scrollbars',
    '--window-size=1440,900',
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
      { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false },
      sessionId,
    )

    const evaluate = async (expression) => {
      const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, sessionId)
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
      return result.result.value
    }

    const shot = async (name) => {
      const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId)
      writeFileSync(join(out, `${name}.png`), Buffer.from(data, 'base64'))
      console.log('shot', name)
    }

    const open = async (mode, script = '') => {
      await send(
        'Page.addScriptToEvaluateOnNewDocument',
        {
          source: `try {
            localStorage.setItem('ldst.theme', 'dark');
            localStorage.setItem('home_mode', ${JSON.stringify(mode)});
            ${script}
          } catch (e) {}`,
        },
        sessionId,
      )
      const loaded = once('Page.loadEventFired', 30000)
      await send('Page.navigate', { url: `${base}/` }, sessionId)
      await loaded
      await sleep(2000)
    }

    // 1. 对话态：先问一轮（空态 → 消息层，列表出现），再点回既有那条长会话读回历史
    await open('chat')
    const ask = async (text) => evaluate(`(() => {
      const input = document.querySelector('[data-testid="composer"] textarea');
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(input, ${JSON.stringify(text)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.closest('form').requestSubmit();
      return 'sent';
    })()`)
    await ask('你好')
    await sleep(4500)
    await evaluate(`(async () => {
      const list = await (await fetch('/api/conversation/sessions')).json();
      const target = list.sessions.find((s) => s.title.includes('推进周五的股票')) ?? list.sessions[0];
      localStorage.setItem('quot602-session', target.session_id);
      return target.title;
    })()`)
    // 首问成功后才写 `quot602-session`：同一页面里列表已经出现，直接点目标会话读回历史
    // （重载会把 `chatStarted` 重置成空态，列表不再渲染——别在这之前 navigate）
    const picked = await evaluate(`(() => {
      const sid = localStorage.getItem('quot602-session');
      const items = Array.from(document.querySelectorAll('[data-testid="session-item"]'));
      const target = items.find((el) => el.closest('li')?.dataset.sessionId === sid) ?? items[0];
      if (!target) return 'not-listed';
      target.click();
      return target.textContent;
    })()`)
    console.log('picked:', picked)
    await sleep(2000)
    await shot('quot-602-home-chat-1440x900')

    // 2. 研究台（同一个 1440 视口，回归：文章栏边距与对话态前一致）
    await open('desk')
    await sleep(1200)
    await shot('quot-602-home-desk-1440x900')

    // 3. composer 三态：首问前（居中）/ 首问后（落底）/ 长消息滚动到底
    await open('chat')
    await shot('quot-602-composer-before')
    await ask('你好')
    await sleep(4500)
    await shot('quot-602-composer-after')
    for (const text of ['再说说大盘', '今天板块怎么轮动', '有哪些风险要盯']) {
      await ask(text)
      await sleep(4200)
    }
    await evaluate(`(() => {
      const sc = document.querySelector('[data-testid="chat-scroll"]');
      if (sc) sc.scrollTop = sc.scrollHeight;
      return { scrollTop: sc?.scrollTop, scrollHeight: sc?.scrollHeight, clientHeight: sc?.clientHeight };
    })()`)
    await sleep(600)
    await shot('quot-602-composer-long')
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

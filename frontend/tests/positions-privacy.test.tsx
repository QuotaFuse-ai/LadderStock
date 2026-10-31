// @vitest-environment jsdom
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PositionsScreen from '../src/screens/radar/PositionsScreen'

/**
 * 持仓隐私硬约束（SPEC §6.3 / DEC-020）：
 * - 页面代码不碰 localStorage / sessionStorage / IndexedDB / cookie / URL 写入；
 * - 增删改与本地导入都不发请求（演示源），也不落任何浏览器存储；
 * - 自由文本按纯文本渲染，不注入 HTML。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url))
const RADAR_SCREENS = path.join(HERE, '../src/screens/radar')
const RADAR_API = path.join(HERE, '../src/api/radar.ts')

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? walk(path.join(dir, entry.name)) : [path.join(dir, entry.name)],
  )
}

const FORBIDDEN: Array<{ name: string; pattern: RegExp }> = [
  { name: '浏览器存储属性', pattern: /\b(localStorage|sessionStorage|indexedDB)\s*[.[]/ },
  { name: 'cookie 读写', pattern: /document\.cookie/ },
  { name: 'window 上的存储 / 跳转', pattern: /window\.(localStorage|sessionStorage|indexedDB|location)/ },
  { name: 'sendBeacon 外发', pattern: /navigator\.sendBeacon/ },
  { name: '持久化 / 全局 store 依赖', pattern: /from\s+['"][^'"]*(persist|stores)\// },
  { name: '存储读写调用', pattern: /\b(setItem|getItem|removeItem)\s*\(/ },
]

/** `api/radar.ts` 就是传输层，`fetch` 只允许出现在那里；页面组件不得直接发请求。 */
const SCREENS_ONLY: Array<{ name: string; pattern: RegExp }> = [
  { name: '直接发请求', pattern: /\bfetch\s*\(/ },
]

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('持仓页静态检查', () => {
  it('radar 页面与解析层不访问浏览器存储、不发请求', () => {
    const files = [...walk(RADAR_SCREENS), RADAR_API]
    expect(files.length).toBeGreaterThan(5)

    for (const file of files) {
      const source = readFileSync(file, 'utf8')
      const rules = file.startsWith(RADAR_SCREENS) ? [...FORBIDDEN, ...SCREENS_ONLY] : FORBIDDEN
      for (const rule of rules) {
        expect(
          rule.pattern.test(source),
          `${path.basename(file)} 命中「${rule.name}」：${rule.pattern}`,
        ).toBe(false)
      }
    }
  })

  it('持仓页不把数据写进 URL（没有任何 setParams / setSearchParams）', () => {
    const source = readFileSync(path.join(RADAR_SCREENS, 'PositionsScreen.tsx'), 'utf8')
    expect(source).not.toMatch(/setSearchParams|setParams\(/)
    expect(source).not.toMatch(/URLSearchParams/)
  })
})

function renderPositions() {
  return render(
    <MemoryRouter initialEntries={['/positions?demo=1']}>
      <Routes>
        <Route path="/positions" element={<PositionsScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('持仓页运行时隐私', () => {
  it('新增 / 删除 / 导入都不写存储、不发请求', async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError('持仓页不应发起请求')
    })
    vi.stubGlobal('fetch', fetchMock)
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => undefined)
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => undefined)
    const indexedDBRef = (globalThis as { indexedDB?: { open: unknown } }).indexedDB
    const openSpy =
      indexedDBRef && typeof indexedDBRef.open === 'function'
        ? vi.spyOn(indexedDBRef as { open: () => unknown }, 'open')
        : null

    renderPositions()
    expect(await screen.findByText('数据只在本机，不上传、不进 AI')).toBeTruthy()

    fireEvent.click(await screen.findByRole('button', { name: /新增持仓/ }))
    const form = await screen.findByRole('dialog')
    fireEvent.change(screen.getByLabelText(/^代码/), { target: { value: '600036' } })
    fireEvent.change(screen.getByLabelText(/^成本价/), { target: { value: '35' } })
    fireEvent.change(screen.getByLabelText(/^数量/), { target: { value: '1000' } })
    fireEvent.click(within(form).getByRole('button', { name: '新增持仓' }))
    expect((await screen.findAllByText('600036')).length).toBeGreaterThan(0)
    expect(form).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '删除 600036' }))
    const confirm = await screen.findByRole('dialog')
    fireEvent.click(within(confirm).getByRole('button', { name: '删除' }))
    expect(await screen.findByText('已删除 600036（本机 SQLite）')).toBeTruthy()
    expect(confirm).toBeTruthy()

    const file = new File(
      [
        JSON.stringify({
          items: [{ code: '000001', cost: 10, qty: 100, industry: '银行', note: '<img src=x onerror="window.__pwned=1">' }],
        }),
      ],
      'positions.json',
      { type: 'application/json' },
    )
    fireEvent.change(screen.getByLabelText('导入本地持仓 JSON'), { target: { files: [file] } })
    expect(await screen.findByText(/本地文件视图 · 1 条/)).toBeTruthy()

    expect(fetchMock).not.toHaveBeenCalled()
    expect(setItem).not.toHaveBeenCalled()
    expect(removeItem).not.toHaveBeenCalled()
    if (openSpy) expect(openSpy).not.toHaveBeenCalled()
    else expect(indexedDBRef).toBeUndefined()
  })

  it('导入文件的自由文本按纯文本渲染，不注入 HTML', async () => {
    renderPositions()
    await screen.findByText('数据只在本机，不上传、不进 AI')

    const payload = { items: [{ code: '000002', cost: 8, qty: 200, note: '<script>window.__pwned=1</script>' }] }
    const file = new File([JSON.stringify(payload)], 'positions.json', { type: 'application/json' })
    fireEvent.change(screen.getByLabelText('导入本地持仓 JSON'), { target: { files: [file] } })

    expect(await screen.findByText('<script>window.__pwned=1</script>')).toBeTruthy()
    expect(document.querySelector('script')).toBeNull()
    expect((window as unknown as { __pwned?: unknown }).__pwned).toBeUndefined()
  })
})

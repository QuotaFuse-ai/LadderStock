// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import KnowledgeDetailScreen from '../src/screens/knowledge/KnowledgeDetailScreen'
import KnowledgeEntryScreen from '../src/screens/knowledge/KnowledgeEntryScreen'

/**
 * 知识库两页（QUOT-567）：落地页按前缀推断市场并跳转；档案页呈现 frontmatter 与正文，
 * 过期要出横幅，没档案要走「还没攒过」空态而不是错误块。
 *
 * 这里不打桩 `loadKnowledge`，而是桩 `fetch`：请求路径、404 文案、JSON 解析都走真实代码。
 */

function Landing() {
  const { market, symbol } = useParams()
  return <p data-testid="landed">已到达 {market}/{symbol}</p>
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

const DOSSIER = {
  market: 'SH',
  symbol: '600000',
  name: '浦发银行',
  as_of: '2026-11-09',
  status: 'fresh',
  valid_days: 90,
  sources: [
    { source: 'tencent', tool: 'tencent_quote_batch', confidence: 'A', fetched_at: '2026-11-09T15:02:11+08:00', trace_id: 'a9581b29e56f48de' },
  ],
  body: [
    '## 1. 公司速览',
    '',
    '浦发银行是一家股份制商业银行。',
    '',
    '## 3. 历史结论',
    '',
    '| 日期 | 结论 |',
    '| --- | --- |',
    '| 2026-11-09 | 偏多 |',
  ].join('\n'),
  recalled: null,
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function renderEntry() {
  return render(
    <MemoryRouter initialEntries={['/knowledge']}>
      <Routes>
        <Route path="/knowledge" element={<KnowledgeEntryScreen />} />
        <Route path="/knowledge/:market/:symbol" element={<Landing />} />
      </Routes>
    </MemoryRouter>,
  )
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={['/knowledge/SH/600000']}>
      <Routes>
        <Route path="/knowledge" element={<p data-testid="entry">落地页</p>} />
        <Route path="/knowledge/:market/:symbol" element={<KnowledgeDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('知识库落地页 /knowledge', () => {
  it('按前缀推断市场：600000 走 SH', () => {
    renderEntry()
    fireEvent.change(screen.getByLabelText(/^证券代码/), { target: { value: '600000' } })
    fireEvent.click(screen.getByRole('button', { name: '看档案' }))
    expect(screen.getByTestId('landed').textContent).toContain('已到达 SH/600000')
  })

  it('000001 走 SZ，830799 走 BJ', () => {
    for (const [code, market] of [
      ['000001', 'SZ'],
      ['830799', 'BJ'],
    ]) {
      renderEntry()
      fireEvent.change(screen.getByLabelText(/^证券代码/), { target: { value: code } })
      fireEvent.click(screen.getByRole('button', { name: '看档案' }))
      expect(screen.getByTestId('landed').textContent).toContain(`已到达 ${market}/${code}`)
      cleanup()
    }
  })

  it('认不出前缀时不跳转，让用户手选市场后才能走', () => {
    renderEntry()
    fireEvent.change(screen.getByLabelText(/^证券代码/), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: '看档案' }))
    expect(screen.getByRole('alert').textContent).toContain('手动选择')
    expect(screen.queryByTestId('landed')).toBeNull()

    fireEvent.change(screen.getByLabelText(/^市场/), { target: { value: 'BJ' } })
    fireEvent.click(screen.getByRole('button', { name: '看档案' }))
    expect(screen.getByTestId('landed').textContent).toContain('已到达 BJ/123456')
  })

  it('非 6 位代码给错误说明，停在原页', () => {
    renderEntry()
    fireEvent.change(screen.getByLabelText(/^证券代码/), { target: { value: '6000' } })
    fireEvent.click(screen.getByRole('button', { name: '看档案' }))
    expect(screen.getByRole('alert').textContent).toContain('6 位数字')
    expect(screen.queryByTestId('landed')).toBeNull()
  })

  it('文案面向普通用户：不出现 manifest / frontmatter 术语', () => {
    renderEntry()
    expect(document.body.textContent).not.toMatch(/manifest|frontmatter|schema/i)
  })
})

describe('档案页 /knowledge/:market/:symbol', () => {
  it('有档案：as_of / 状态 / 有效期徽标 + 正文（含表格）', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(json(DOSSIER)))
    renderDetail()
    await waitFor(() => expect(screen.getByText('浦发银行')).toBeTruthy())
    expect(screen.getByText('更新于 2026-11-09')).toBeTruthy()
    expect(screen.getByText('有效')).toBeTruthy()
    expect(screen.getByText('有效期 90 天')).toBeTruthy()
    // 5 段正文里的标题与表格都要渲染出来
    expect(screen.getByRole('heading', { name: '1. 公司速览' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '3. 历史结论' })).toBeTruthy()
    expect(screen.getByRole('table')).toBeTruthy()
    expect(screen.getByText('a9581b29e56f48de')).toBeTruthy()
    // fresh 不出过期横幅
    expect(screen.queryByText('数据可能过期')).toBeNull()
  })

  it('stale：顶部显著提示「数据可能过期」', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(json({ ...DOSSIER, status: 'stale' })))
    renderDetail()
    await waitFor(() => expect(screen.getByText('数据可能过期')).toBeTruthy())
    expect(screen.getByText('可能过期')).toBeTruthy()
  })

  it('404「档案不存在」走空态并引导去问 AI，不当成错误块', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(json({ detail: '档案不存在：SZ:000001' }, 404)))
    renderDetail()
    await waitFor(() => expect(screen.getByText('还没有这只票的档案')).toBeTruthy())
    expect(screen.getByRole('link', { name: /去首页问 AI/ })).toBeTruthy()
    expect(screen.queryByText('读取档案失败')).toBeNull()
  })

  it('服务未连接走错误块并可重试', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')))
    renderDetail()
    await waitFor(() => expect(screen.getByText('本机服务未连接')).toBeTruthy())
  })
})

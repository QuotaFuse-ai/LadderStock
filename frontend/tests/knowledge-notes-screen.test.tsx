// @vitest-environment jsdom
/**
 * E 包 / DEC-091 笔记页（`/knowledge/notes`）三标签的行为钉子：
 *
 * - **笔记**：列表来自 `GET /api/knowledge/notes`，点行展开 markdown 渲染正文；
 * - **跨票检索**：`GET /api/knowledge/search` 的降级来源要如实标注（字面 ≠ 语义），
 *   有标的的命中链到 `/knowledge/:market/:symbol`；
 * - **估值换算**：表单数字原样提交（百分比档除以 100 在页面做），结果恒带免责声明。
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import KnowledgeNotesScreen from '../src/screens/knowledge/KnowledgeNotesScreen'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

const NOTES_FIXTURE = {
  ok: true,
  total: 2,
  items: [
    {
      id: 1,
      market: 'SH',
      symbol: '600000',
      target: 'SH:600000',
      title: '浦发观察要点',
      body: '# 观点\n等三季报再决定加仓',
      tags: ['银行', '观察'],
      created_at: '2026-11-22T10:00:00+08:00',
      updated_at: '2026-11-22T11:00:00+08:00',
    },
    {
      id: 2,
      market: '',
      symbol: '',
      target: '',
      title: '',
      body: '随手记：盯一下 LPR',
      tags: [],
      created_at: '2026-11-22T09:00:00+08:00',
      updated_at: '2026-11-22T09:30:00+08:00',
    },
  ],
}

const TAGS_FIXTURE = {
  ok: true,
  tags: [
    { tag: '银行', notes: 1 },
    { tag: '观察', notes: 1 },
  ],
}

function renderNotes(): void {
  render(
    <MemoryRouter initialEntries={['/knowledge/notes']}>
      <KnowledgeNotesScreen />
    </MemoryRouter>,
  )
}

describe('笔记标签页', () => {
  it('列表渲染笔记行，点行展开 markdown 正文', async () => {
    const fetchMock = vi.fn(async (input: unknown) => {
      const url = String(input)
      if (url.startsWith('/api/knowledge/notes/tags')) return jsonResponse(TAGS_FIXTURE)
      if (url.startsWith('/api/knowledge/notes')) return jsonResponse(NOTES_FIXTURE)
      return jsonResponse({ detail: 'Not Found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderNotes()

    expect(await screen.findByText('浦发观察要点')).toBeTruthy()
    expect(screen.getByText('随手记：盯一下 LPR')).toBeTruthy()
    expect(screen.getByTestId('tag-chip-银行')).toBeTruthy()

    fireEvent.click(screen.getByTestId('note-row-1'))
    const body = await waitFor(() => screen.getByTestId('note-body-1'))
    expect(body.textContent).toContain('观点')
    expect(body.textContent).toContain('等三季报再决定加仓')
    expect(body.querySelector('h1')).toBeTruthy()
  })

  it('空库给下一步动作的空态，不静默空白', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: unknown) => {
        const url = String(input)
        if (url.startsWith('/api/knowledge/notes/tags')) return jsonResponse({ ok: true, tags: [] })
        if (url.startsWith('/api/knowledge/notes')) return jsonResponse({ ok: true, total: 0, items: [] })
        return jsonResponse({ detail: 'Not Found' }, 404)
      }),
    )
    renderNotes()
    expect(await screen.findByText('还没有笔记')).toBeTruthy()
    expect(screen.getByText(/新建笔记/)).toBeTruthy()
  })
})

describe('跨票检索标签页', () => {
  it('降级来源如实标注为「字面」，语义命中标注「语义」并链到档案页', async () => {
    const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
      const url = String(input)
      if (init?.method === 'POST') return jsonResponse({ detail: 'Not Found' }, 404)
      if (url.startsWith('/api/knowledge/notes/tags')) return jsonResponse({ ok: true, tags: [] })
      if (url.startsWith('/api/knowledge/notes')) return jsonResponse({ ok: true, total: 0, items: [] })
      if (url.startsWith('/api/knowledge/search')) {
        return jsonResponse({
          ok: true,
          q: '银行',
          engine: 'local',
          vector_error: 'RuntimeError: qdrant down',
          total: 1,
          hits: [
            {
              market: 'SH',
              symbol: '600000',
              as_of: null,
              status: null,
              section: null,
              score: null,
              snippet: '……浦发银行……',
              source: 'local',
            },
          ],
        })
      }
      return jsonResponse({ detail: 'Not Found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderNotes()

    fireEvent.click(await screen.findByText('跨票检索'))
    fireEvent.change(await screen.findByLabelText('搜索词'), { target: { value: '银行' } })
    fireEvent.click(screen.getByRole('button', { name: '搜索' }))

    expect(await screen.findByText('字面')).toBeTruthy()
    expect(screen.getByText(/向量库不可用/)).toBeTruthy()
    expect(screen.getByText(/本地字面（降级）/)).toBeTruthy()
    const link = screen.getByRole('link', { name: 'SH:600000' })
    expect(link.getAttribute('href')).toBe('/knowledge/SH/600000')
    // 空库仍允许搜索（搜的是档案正文，不是笔记）
    expect(fetchMock).toHaveBeenCalled()
  })
})

describe('估值换算标签页', () => {
  it('PE 表单原样提交，结果带免责声明；百分比档按 /100 提交', async () => {
    const bodies: Array<Record<string, unknown>> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: unknown, init?: RequestInit) => {
        const url = String(input)
        if (url === '/api/valuation/estimate' && init?.method === 'POST') {
          const body = JSON.parse(String(init.body)) as Record<string, unknown>
          bodies.push(body)
          const kind = body.kind
          if (kind === 'pe') {
            return jsonResponse({
              kind: 'PE',
              price: body.price,
              per_share: body.eps,
              value: (body.price as number) / (body.eps as number),
              disclaimer: '这是换算器，不是估值判断：数字全部来自你给的输入，不构成投资建议。',
            })
          }
          return jsonResponse({ detail: '未知换算类型' }, 400)
        }
        if (url.startsWith('/api/knowledge/notes/tags')) return jsonResponse({ ok: true, tags: [] })
        if (url.startsWith('/api/knowledge/notes')) return jsonResponse({ ok: true, total: 0, items: [] })
        return jsonResponse({ detail: 'Not Found' }, 404)
      }),
    )
    renderNotes()

    fireEvent.click(await screen.findByText('估值换算'))
    fireEvent.change(await screen.findByLabelText('价格（元）'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('每股收益（元）'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: '计算' }))

    const result = await screen.findByTestId('calc-result')
    expect(result.textContent).toContain('PE')
    expect(result.textContent).toContain('5')
    expect(result.textContent).toContain('这是换算器')
    expect(bodies[0]).toEqual({ kind: 'pe', price: 10, eps: 2 })

    // 换 DCF：百分比输入 8 → 0.08
    fireEvent.change(screen.getByLabelText('换算类型'), { target: { value: 'dcf' } })
    fireEvent.change(screen.getByLabelText('基期自由现金流（元）'), { target: { value: '100' } })
    fireEvent.change(screen.getByLabelText('增长段增速'), { target: { value: '8' } })
    fireEvent.change(screen.getByLabelText('终值增速'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('折现率'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText(/显式预测年数/), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: '计算' }))
    expect(await waitFor(() => bodies[1])).toEqual({
      kind: 'dcf',
      fcf: 100,
      growth: 0.08,
      terminal_growth: 0.03,
      discount: 0.1,
      periods: 5,
    })
  })

  it('缺输入给行内提示，不发请求', async () => {
    const fetchMock = vi.fn(async (input: unknown) => {
      const url = String(input)
      if (url === '/api/valuation/estimate') return jsonResponse({ detail: '不应到达' }, 500)
      if (url.startsWith('/api/knowledge/notes/tags')) return jsonResponse({ ok: true, tags: [] })
      if (url.startsWith('/api/knowledge/notes')) return jsonResponse({ ok: true, total: 0, items: [] })
      return jsonResponse({ detail: 'Not Found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderNotes()

    fireEvent.click(await screen.findByText('估值换算'))
    fireEvent.click(screen.getByRole('button', { name: '计算' }))
    expect(await screen.findByText(/请填写「价格」/)).toBeTruthy()
    const estimateCalls = fetchMock.mock.calls.filter(
      ([input]) => String(input) === '/api/valuation/estimate',
    )
    expect(estimateCalls).toHaveLength(0)
  })
})

// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BlockRenderer } from '../src/components/answer-blocks'
import { formatMetricValue } from '../src/components/answer-blocks'
import {
  ACTION_BLOCK,
  ALL_BLOCKS,
  CHART_BLOCK,
  CITATION_BLOCK,
  CONCLUSION_BLOCK,
  FULL_ANSWER_BLOCKS,
  LONG_TABLE_BLOCK,
  METRIC_BLOCK,
  NOTICE_BLOCK,
  TABLE_BLOCK,
} from './fixtures/answerBlocks'

/**
 * `BlockRenderer`（任务书阶段 E 验收）。
 *
 * 钉的是「服务端给什么，页面就画什么」：七种块按 type 分发、只在渲染器里判一次；
 * 数字走等宽、方向色只在 up/down/flat 三种情况下生效；表格超过 5 行折叠；
 * 来源整行可点；目标不在路由表就不摆死按钮；整列只有一块提示时再给「重试该块」。
 *
 * 画布：jsdom 没有 2D context（`tests/setup-dom.ts` 让它返回 null），图表块按
 * 「画不出来」的真实分支走；这里只断言块结构与等价文本（§9）。
 */

function renderBlocks(ui: Parameters<typeof render>[0]) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

function blockOf(type: string): HTMLElement {
  const node = document.querySelector<HTMLElement>(`[data-answer-block="${type}"]`)
  if (!node) throw new Error(`没有渲染出 ${type} 块`)
  return node
}

let errorSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  // 先收尾再断言：断言抛错会让 cleanup 不执行，DOM 泄漏到下一条用例，
  // 失败会从「某条用例挂了」扩散成一批无关用例一起挂
  const calls = errorSpy.mock.calls.slice()
  errorSpy.mockRestore()
  cleanup()
  // jsdom 没有 2D context，图表初始化 / 渲染 / 销毁都会走 console.error 的真实分支
  // （本轮修掉的是销毁期把异常抛回 React 树）；块本身不许在渲染期抛错
  const unexpected = calls.filter(
    ([first]) => !/图表(初始化|渲染|销毁)失败|Not implemented/.test(String(first)),
  )
  expect(unexpected).toEqual([])
})

describe('BlockRenderer · 七块全渲染', () => {
  it('七个 fixture 块一次全渲染：顺序不变、类型各自命中、无渲染期报错', () => {
    renderBlocks(
      <BlockRenderer
        blocks={ALL_BLOCKS}
        onRetry={vi.fn()}
        onOpenStatus={vi.fn()}
        onNavigate={vi.fn()}
      />,
    )

    const types = Array.from(document.querySelectorAll('[data-answer-block]')).map((node) =>
      node.getAttribute('data-answer-block'),
    )
    // conclusion 与 chart 内部各自还有一层；table / citation 是 BlockShell 的 section
    // MAX_BLOCKS=6（QUOT-581 提交 34f708e3 后端拍板）：七块 fixture 截到前 6 块，citation 落在截断线外
    expect(types).toEqual([
      'conclusion',
      'metric',
      'chart',
      'table',
      'notice',
      'action',
    ])

    expect(screen.getByText(CONCLUSION_BLOCK.payload.text)).toBeTruthy()
    expect(screen.getByText('收盘价', { selector: 'p' })).toBeTruthy()
    expect(screen.getByText('38.62')).toBeTruthy()
    expect(screen.getByText('近二十个交易日收盘价')).toBeTruthy()
    expect(screen.getByRole('table')).toBeTruthy()
    expect(screen.getByText('这一块没有取到数据')).toBeTruthy()
    expect(screen.getByRole('button', { name: '展开完整面板' })).toBeTruthy()
  })

  it('full_answer 整串（含 degraded 提示、三指标、无 symbol 来源）也能渲染', () => {
    renderBlocks(<BlockRenderer blocks={FULL_ANSWER_BLOCKS} />)

    const metric = blockOf('metric')
    expect(within(metric).getAllByRole('listitem')).toHaveLength(3)
    expect(within(metric).getAllByText('上涨')).toHaveLength(3)
    expect(screen.getAllByText('AI 叙述降级')).toHaveLength(1)
    expect(screen.getByRole('link', { name: /腾讯行情/ }).textContent).toContain('—')
  })

  it('单块结论不被挤成一行，也不多出别的块', () => {
    renderBlocks(<BlockRenderer blocks={[CONCLUSION_BLOCK]} />)

    const flow = document.querySelector('[data-answer-block="conclusion"]')?.parentElement
    expect(flow?.className).toContain('flex-col')
    expect(document.querySelectorAll('[data-answer-block]')).toHaveLength(1)
  })

  it('未经解析的原始数组（后端直接透传 JSON 解析结果）也行', () => {
    renderBlocks(<BlockRenderer blocks={JSON.parse(JSON.stringify([METRIC_BLOCK]))} />)
    expect(screen.getByText('38.62')).toBeTruthy()
    expect(document.querySelector('[data-answer-block="empty"]')).toBeNull()
  })
})

describe('BlockRenderer · 结论块', () => {
  it('正文原样展示，带数字也不改写；tone 只做文字标记', () => {
    renderBlocks(
      <BlockRenderer
        blocks={[
          { type: 'conclusion', payload: { text: '近二十日主力净流出 12.3 亿，跌幅 4.3%', tone: 'negative', confidence: 0.62 } },
        ]}
      />,
    )

    expect(screen.getByText('近二十日主力净流出 12.3 亿，跌幅 4.3%')).toBeTruthy()
    expect(screen.getByText('结论 · 偏空')).toBeTruthy()
    // 置信度按载荷原值展示（0.62 → "0.62"），不重算、不评级
    expect(screen.getByText('0.62')).toBeTruthy()
  })

  it('载荷没给 confidence：走卡片自己的降级态，不伪造置信度', () => {
    renderBlocks(<BlockRenderer blocks={[{ type: 'conclusion', payload: { text: '震荡偏弱', tone: 'neutral' } }]} />)

    expect(screen.getByText('后端尚未透传白话结论')).toBeTruthy()
    expect(screen.queryByText('0.5')).toBeNull()
    // 正文属于载荷，但卡片降级时不该自行编结论
    expect(screen.queryByText('震荡偏弱')).toBeNull()
  })
})

describe('BlockRenderer · 指标块', () => {
  it('数值走等宽数字（ldst-num）+ 字重，单位是辅助文字', () => {
    renderBlocks(<BlockRenderer blocks={[METRIC_BLOCK]} />)

    const metric = blockOf('metric')
    const value = within(metric).getByText('38.62')
    expect(value.className).toContain('ldst-num')
    expect(value.className).toContain('font-semibold')
    expect(value.className).toContain('text-text')
    expect(value.className).not.toContain('text-up')
    expect(value.className).not.toContain('text-down')
    expect(within(metric).getByText('元').className).toContain('text-text-muted')
  })

  it('三种方向各自上色：up → text-up，down → text-down，flat → text-flat', () => {
    renderBlocks(
      <BlockRenderer
        blocks={[
          {
            type: 'metric',
            payload: {
              items: [
                { label: '上涨项', value: 1.2, unit: '%', direction: 'up' },
                { label: '下跌项', value: -1.2, unit: '%', direction: 'down' },
                { label: '持平项', value: 0, unit: '%', direction: 'flat' },
              ],
            },
          },
        ]}
      />,
    )

    expect(screen.getByText('1.2').className).toContain('text-up')
    expect(screen.getByText('1.2').className).toContain('ldst-num')
    expect(screen.getByText('-1.2').className).toContain('text-down')
    expect(screen.getByText('-1.2').className).toContain('ldst-num')
    expect(screen.getByText('0').className).toContain('text-flat')
    expect(screen.getByText('0').className).toContain('ldst-num')
    // 方向同时给读屏文字，不只靠颜色（§9）；「持平」没有箭头，只上色不加字
    expect(screen.getByText('上涨')).toBeTruthy()
    expect(screen.getByText('下跌')).toBeTruthy()
    expect(screen.queryByText('持平')).toBeNull()
    expect(screen.getByText('0').className).toContain('text-flat')
  })

  it('375px：容器固定两列；两块时每格横跨两列，三/四块时各占一格', () => {
    const { unmount } = renderBlocks(<BlockRenderer blocks={[METRIC_BLOCK]} />)
    const two = blockOf('metric')
    expect(two.className).toContain('grid-cols-2')
    expect(two.className).toContain('sm:grid-cols-4')
    for (const cell of Array.from(two.querySelectorAll('li'))) {
      expect(cell.className).toContain('sm:col-span-2')
    }
    unmount()

    renderBlocks(
      <BlockRenderer
        blocks={[FULL_ANSWER_BLOCKS[1]]}
      />,
    )
    const three = blockOf('metric')
    for (const cell of Array.from(three.querySelectorAll('li'))) {
      expect(cell.className).toContain('sm:col-span-1')
    }
  })

  it('千分位只是格式化：保留载荷原始小数位', () => {
    expect(formatMetricValue(12345.6)).toBe('12,345.6')
    expect(formatMetricValue(-4.3)).toBe('-4.3')
    expect(formatMetricValue(0)).toBe('0')
  })
})

describe('BlockRenderer · 表格块', () => {
  it('不超过 5 行：直接全给，不出折叠按钮', () => {
    renderBlocks(<BlockRenderer blocks={[TABLE_BLOCK]} />)
    expect(screen.queryByRole('button', { name: /还有|收起表格/ })).toBeNull()
  })

  it('超过 5 行：先给 5 行，按钮写「还有 N 行」（N 取 rows_total）', () => {
    renderBlocks(<BlockRenderer blocks={[LONG_TABLE_BLOCK]} />)

    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('row')).toHaveLength(1 + 5)

    const toggle = screen.getByRole('button', { name: '还有 7 行' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(toggle.className).toContain('duration-fast')
  })

  it('展开后给全部已下发行并说明剩余行；再点收起回 5 行', () => {
    renderBlocks(<BlockRenderer blocks={[LONG_TABLE_BLOCK]} />)

    const toggle = screen.getByRole('button', { name: '还有 7 行' })
    act(() => {
      fireEvent.click(toggle)
    })

    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('row')).toHaveLength(1 + 6)
    expect(screen.getByText('其余 6 行未随本块下发。')).toBeTruthy()
    const collapse = screen.getByRole('button', { name: '收起表格' })
    expect(collapse.getAttribute('aria-expanded')).toBe('true')

    act(() => {
      fireEvent.click(collapse)
    })
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(1 + 5)
  })

  it('375px：横向滚动容器 + 数字列右对齐走等宽数字，首列 sticky', () => {
    renderBlocks(<BlockRenderer blocks={[TABLE_BLOCK]} />)

    const table = screen.getByRole('table')
    expect(table.className).toContain('min-w-full')
    // 窄屏横向滚动交给外层容器（与 ui/Table 同口径），表格不被压成一条缝
    expect(table.parentElement?.className).toContain('overflow-auto')
    expect(screen.getByText('78.2').className).toContain('text-right')
    expect(screen.getByText('78.2').className).toContain('ldst-num')
    expect(screen.getByText('营业收入(亿)').className).toContain('sticky')
  })
})

describe('BlockRenderer · 来源块', () => {
  it('整行可点：a[href] 新窗口打开，行内含名称与抓取时间', () => {
    renderBlocks(<BlockRenderer blocks={[CITATION_BLOCK]} />)

    const link = screen.getByRole('link', { name: /腾讯行情/ })
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe('https://example.com/quote/600036')
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toContain('noreferrer')
    expect(link.textContent).toContain('600036')
    expect(link.textContent).toContain('2026-11-11')
    expect(link.textContent).toContain('2026-11-11T15:02:01+08:00')
    expect(link.className).toContain('duration-fast')
  })

  it('symbol / as_of 缺失显示 —，不猜代码和日期', () => {
    renderBlocks(
      <BlockRenderer
        blocks={[{ type: 'citation', payload: { items: [{ ...CITATION_BLOCK.payload.items[0], symbol: null, as_of: null }] } }]}
      />,
    )

    const link = screen.getByRole('link', { name: /腾讯行情/ })
    expect(link.textContent).toContain('—')
    expect(link.textContent).not.toContain('600036')
  })

  it('缺 url 的来源照常列出但不可点：不伪造 href', () => {
    renderBlocks(
      <BlockRenderer
        blocks={[{ type: 'citation', payload: { items: [{ ...CITATION_BLOCK.payload.items[0], url: null }] } }]}
      />,
    )

    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText('腾讯行情')).toBeTruthy()
    expect(screen.getByText('无链接')).toBeTruthy()
  })
})

describe('BlockRenderer · 提示块', () => {
  it('missing_data：warn 色调、说明缺的是哪一块，非整轮时给「重试该块」', () => {
    const onRetry = vi.fn()
    const onOpenStatus = vi.fn()
    renderBlocks(
      <BlockRenderer
        blocks={[CONCLUSION_BLOCK, NOTICE_BLOCK]}
        onRetry={onRetry}
        onOpenStatus={onOpenStatus}
      />,
    )

    const notice = blockOf('notice')
    const panel = within(notice).getByRole('status')
    expect(panel.textContent).toContain('这一块没有取到数据')
    expect(panel.textContent).toContain('分红')
    expect(panel.textContent).toContain('本页没有取到该标的的分红记录')
    expect(panel.className).toContain('bg-warn-soft')

    fireEvent.click(within(notice).getByRole('button', { name: '重试该段' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
    fireEvent.click(within(notice).getByRole('button', { name: '查看 AI 状态' }))
    expect(onOpenStatus).toHaveBeenCalledTimes(1)
  })

  it('整轮只有一块 missing_data（§6.4 整轮失败）：不摆「重试该块」', () => {
    renderBlocks(<BlockRenderer blocks={[NOTICE_BLOCK]} onRetry={vi.fn()} />)
    expect(screen.queryByRole('button', { name: '重试该段' })).toBeNull()
  })

  it('服务端给了前端不认识的 kind：退到 internal 兜底，整块不白屏', () => {
    renderBlocks(
      <BlockRenderer blocks={[{ type: 'notice', payload: { kind: 'brand_new', message: '未来才有的失败' } }]} />,
    )

    expect(screen.getByRole('alert').textContent).toContain('AI 调用未归类失败')
    expect(screen.getByRole('alert').textContent).toContain('未来才有的失败')
  })
})

describe('BlockRenderer · 动作块', () => {
  it('open_screen 命中路由表：主按钮点击交给导航口', () => {
    const onNavigate = vi.fn()
    renderBlocks(<BlockRenderer blocks={[ACTION_BLOCK]} onNavigate={onNavigate} />)

    const button = screen.getByRole('button', { name: '展开完整面板' })
    expect(button.className).toContain('bg-accent-solid')
    fireEvent.click(button)
    expect(onNavigate).toHaveBeenCalledWith('/research?code=600036')
  })

  it('目标不在页面表里（§7 红线 7）：不摆死按钮，只说明走不到', () => {
    const onNavigate = vi.fn()
    renderBlocks(
      <BlockRenderer
        blocks={[{ type: 'action', payload: { items: [{ label: '去投顾', action: 'open_screen', target: '/advisory' }] } }]}
        onNavigate={onNavigate}
      />,
    )

    expect(screen.queryByRole('button', { name: '去投顾' })).toBeNull()
    const panel = screen.getByRole('status')
    expect(panel.textContent).toContain('这些跳转目标不在本应用的页面表里')
    expect(panel.textContent).toContain('/advisory')
  })

  it('同一块里合法与非法目标混排：合法的照常可点，非法的单独说明', () => {
    const onNavigate = vi.fn()
    renderBlocks(
      <BlockRenderer
        blocks={[
          {
            type: 'action',
            payload: {
              items: [
                ...ACTION_BLOCK.payload.items,
                { label: '外部链接', action: 'open_screen', target: 'https://example.com' },
              ],
            },
          },
        ]}
        onNavigate={onNavigate}
      />,
    )

    expect(screen.getByRole('button', { name: '展开完整面板' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '外部链接' })).toBeNull()
    expect(screen.getByRole('status').textContent).toContain('https://example.com')
  })
})

describe('BlockRenderer · 兜底', () => {
  it('没有可渲染块：明确说没有块，可选「重新提问」，不伪造结论', () => {
    const onRetry = vi.fn()
    renderBlocks(<BlockRenderer blocks={[]} onRetry={onRetry} />)

    const empty = document.querySelector<HTMLElement>('[data-answer-block="empty"]')
    expect(empty?.textContent).toContain('这一轮没有可展示的答案块。')
    fireEvent.click(screen.getByRole('button', { name: '重新提问' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('载荷不合法：占位说明这块没画出来，原因写清楚', () => {
    renderBlocks(<BlockRenderer blocks={[{ type: 'metric', payload: { items: [] } }]} />)

    const invalid = document.querySelector<HTMLElement>('[data-answer-block="invalid"]')
    expect(invalid?.textContent).toContain('这一块没有画出来：指标块没有可展示的数值。')
    expect(invalid?.getAttribute('role')).toBe('status')
  })

  it('图表块：画布画不出来时给等价文本，不把整列带走', () => {
    renderBlocks(<BlockRenderer blocks={[CHART_BLOCK]} />)

    const chart = blockOf('chart')
    expect(within(chart).getByText('近二十个交易日收盘价')).toBeTruthy()
    expect(within(chart).getByRole('img', { name: /3 个点/ })).toBeTruthy()
  })
})

/**
 * 整块进、整块出（阶段 F 的 `ChatPane` 靠这个前提做增量 append）。
 *
 * 块内文本是服务端定稿，不是流式增量：不许打字机 / 逐字渐显，否则等于把逐字更新
 * 搬回组件内部；展开 / 折叠也只准用 `duration-fast` 120ms 这一档。
 */
describe('BlockRenderer · 整块进整块出', () => {
  it('渲染完成的瞬间文本一次性完整呈现，之后不再变', () => {
    vi.useFakeTimers()
    try {
      renderBlocks(<BlockRenderer blocks={ALL_BLOCKS} />)
      const texts = () =>
        Array.from(document.querySelectorAll('[data-answer-block]')).map((node) =>
          (node.textContent ?? '').replace(/\s+/g, ' ').trim(),
        )
      const first = texts()
      act(() => {
        vi.advanceTimersByTime(2000)
      })
      expect(texts()).toEqual(first)

      for (const node of document.querySelectorAll('[data-answer-block]')) {
        // 打字机 / 渐显的两种典型实现：逐字拆成行内单字元素，或靠内联 opacity 逐步显影
        for (const el of Array.from(node.querySelectorAll<HTMLElement>('*'))) {
          expect(el.style.opacity).not.toMatch(/^(0|0\.\d+)$/)
          const svgOwned = el instanceof SVGElement
          const className = svgOwned ? el.getAttribute('class') ?? '' : el.className
          expect(className).not.toMatch(/typewriter|blink-|caret-blink/)
        }
      }
      expect(document.querySelector('[data-answer-block="conclusion"]')?.textContent).toContain(
        CONCLUSION_BLOCK.payload.text,
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('块内过渡只走 duration-fast，不出现更长的动画档位', () => {
    renderBlocks(<BlockRenderer blocks={ALL_BLOCKS} />)
    const animated = Array.from(document.querySelectorAll<HTMLElement>('[data-answer-block] *')).filter((el) =>
      /\btransition-|animate-/.test(el.className),
    )
    // 全部块都在渲染时，至少来源行 / 折叠按钮这类交互元素会带过渡
    expect(animated.length).toBeGreaterThan(0)
    for (const el of animated) {
      expect(el.className).not.toMatch(/\bduration-(?!fast\b)/)
      expect(el.className).not.toMatch(/\banimate-/)
    }
    expect(blockOf('table')).toBeTruthy()
  })

  it('同一个 blocks 引用重渲染不重新解析，块对象稳定', () => {
    const { rerender } = renderBlocks(<BlockRenderer blocks={FULL_ANSWER_BLOCKS} />)
    const before = Array.from(document.querySelectorAll('[data-answer-block]')).map((n) => n.textContent)
    rerender(
      <MemoryRouter>
        <BlockRenderer blocks={FULL_ANSWER_BLOCKS} />
      </MemoryRouter>,
    )
    const after = Array.from(document.querySelectorAll('[data-answer-block]')).map((n) => n.textContent)
    expect(after).toEqual(before)
  })
})

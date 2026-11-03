// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DataTable, StatePanel, type Column, type Expandable } from '../src/components/ui'
import type { PageState } from '../src/lib/pageState'

afterEach(cleanup)

interface Row {
  code: string
  name: string
  pct: number
  note: string | null
}

const ROWS: Row[] = [
  { code: '600001', name: 'A', pct: 1.5, note: '甲' },
  { code: '600002', name: 'B', pct: -3.25, note: '乙' },
]

const rowKey = (row: Row) => row.code

describe('DataTable · 列宽与截断', () => {
  const columns: Array<Column<Row>> = [
    { key: 'code', header: '代码', width: '88px', render: (row) => row.code, numeric: true },
    { key: 'name', header: '名称', truncate: true, render: (row) => row.name },
    {
      key: 'note',
      header: '理由',
      truncate: true,
      render: (row) => <em>{row.note ?? '—'}</em>,
      cellTitle: (row) => row.note ?? '未填写理由',
    },
  ]

  it('长文本列：单元格吃掉剩余宽度、内容截断、title 自动取文本', () => {
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} caption="名单" />)

    const cell = screen.getByRole('cell', { name: 'A' })
    expect(cell.className).toContain('max-w-0')
    expect(cell.className).toContain('w-full')
    expect(cell.title).toBe('A')
    expect(cell.querySelector('span')?.className).toContain('truncate')
  })

  it('给了 width 的短列不加宽度策略，长列仍可截断', () => {
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} caption="名单" />)
    expect(screen.getByRole('cell', { name: '600001' }).className).not.toContain('max-w-0')
    expect(screen.getByRole('columnheader', { name: '代码' }).getAttribute('style')).toContain('88px')
  })

  it('富文本单元格用 cellTitle 给悬浮文案，不给就不写 title', () => {
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} caption="名单" />)
    expect(screen.getByRole('cell', { name: '甲' }).title).toBe('甲')

    cleanup()
    const noTruncate: Array<Column<Row>> = [{ key: 'name', header: '名称', render: (row) => row.name }]
    render(<DataTable columns={noTruncate} rows={ROWS} rowKey={rowKey} />)
    expect(screen.getAllByRole('cell', { name: 'A' })[0].hasAttribute('title')).toBe(false)
  })

  it('truncate 列的缺省文案也有悬浮说明，用户能知道是没填而不是空', () => {
    const columns2: Array<Column<Row>> = [
      { key: 'note', header: '理由', truncate: true, render: (row) => <em>{row.note ?? '—'}</em>, cellTitle: (row) => row.note ?? '未填写理由' },
    ]
    const blank: Row = { code: '600003', name: 'C', pct: 0, note: null }
    render(<DataTable columns={columns2} rows={[blank]} rowKey={rowKey} />)
    expect(screen.getByRole('cell', { name: '—' }).title).toBe('未填写理由')
  })
})

describe('DataTable · 数字列基线', () => {
  it('numeric 即右对齐 + .ldst-num，表头同步右对齐', () => {
    const columns: Array<Column<Row>> = [
      { key: 'name', header: '名称', render: (row) => row.name },
      { key: 'pct', header: '涨幅', numeric: true, render: (row) => `${row.pct}%` },
    ]
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} caption="名单" />)

    const head = screen.getByRole('columnheader', { name: '涨幅' })
    const cell = screen.getByRole('cell', { name: '1.5%' })
    expect(head.className).toContain('text-right')
    expect(cell.className).toContain('text-right')
    expect(cell.className).toContain('ldst-num')
    expect(screen.getByRole('cell', { name: 'A' }).className).not.toContain('ldst-num')
  })

  it('显式 align 优先于 numeric 的方向，但等宽数字仍然生效', () => {
    const columns: Array<Column<Row>> = [
      { key: 'pct', header: '涨幅', numeric: true, align: 'center', render: (row) => `${row.pct}%` },
    ]
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} />)
    const cell = screen.getByRole('cell', { name: '1.5%' })
    expect(cell.className).toContain('text-center')
    expect(cell.className).toContain('ldst-num')
  })
})

describe('DataTable · sticky 层级', () => {
  const columns: Array<Column<Row>> = [
    { key: 'name', header: '名称', render: (row) => row.name },
    { key: 'pct', header: '涨幅', numeric: true, render: (row) => `${row.pct}%` },
  ]

  it('首列表体用 sticky-col，表头首列用更高的交叉层级，普通表头用 sticky', () => {
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} caption="名单" maxHeight="240px" />)
    const bodyCell = screen.getByRole('cell', { name: 'A' })
    expect(bodyCell.classList.contains('z-sticky-col')).toBe(true)
    const firstNameHead = screen.getByRole('columnheader', { name: '名称' })
    expect(firstNameHead.classList.contains('z-sticky-col-head')).toBe(true)
    expect(firstNameHead.classList.contains('z-sticky-col')).toBe(false)
    expect(screen.getByRole('columnheader', { name: '涨幅' }).classList.contains('z-sticky')).toBe(true)
  })

  it('不 sticky 首列时，表头仍纵向吸附', () => {
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} stickyFirstColumn={false} />)
    expect(screen.getByRole('cell', { name: 'A' }).className).not.toContain('sticky')
    expect(screen.getByRole('columnheader', { name: '名称' }).className).toContain('top-0')
  })

  it('有展开列时，首列让开展开列的宽度', () => {
    const expandable: Expandable<Row> = { render: (row) => <p>详情 {row.code}</p> }
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} expandable={expandable} caption="名单" />)
    const bodyCell = screen.getByRole('cell', { name: 'A' })
    expect(bodyCell.classList.contains('left-expander-col')).toBe(true)
    expect(bodyCell.classList.contains('bg-inherit')).toBe(true)
  })

  it('四态行按含展开列的总列数铺满', () => {
    const expandable: Expandable<Row> = { render: (row) => <p>详情 {row.code}</p> }
    render(<DataTable columns={columns} rows={[]} rowKey={rowKey} expandable={expandable} empty={<p>暂无数据</p>} />)
    const [, stateRow] = screen.getAllByRole('row')
    expect(stateRow.querySelector('td')?.getAttribute('colspan')).toBe('3')
  })
})

describe('DataTable · 合计行', () => {
  /** tfoot 在 ARIA 里的隐式角色是 rowgroup（没有 rowfooter），断言只能落到 DOM 标签 */
  const foot = (): HTMLElement => {
    const node = document.querySelector('tfoot')
    if (!node) throw new Error('合计行没有渲染')
    return node as HTMLElement
  }

  const columns: Array<Column<Row>> = [
    { key: 'name', header: '名称', render: (row) => row.name, summary: (rows) => `共 ${rows.length} 只` },
    {
      key: 'pct',
      header: '涨幅',
      numeric: true,
      render: (row) => row.pct,
      summary: (rows) => rows.reduce((total, row) => total + row.pct, 0).toFixed(2),
    },
  ]

  it('任一列声明 summary 才渲染 tfoot，数字单元格同样走等宽基线', () => {
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} caption="名单" />)
    expect(within(foot()).getByText('共 2 只')).toBeTruthy()
    expect(within(foot()).getByText('-1.75').className).toContain('ldst-num')

    cleanup()
    const noSummary: Array<Column<Row>> = [
      { key: 'name', header: '名称', render: (row) => row.name },
      { key: 'pct', header: '涨幅', numeric: true, render: (row) => row.pct },
    ]
    render(<DataTable columns={noSummary} rows={ROWS} rowKey={rowKey} />)
    expect(document.querySelector('tfoot')).toBeNull()
  })

  it('summary 拿的是当前排序后的行，合计跟着用户看到的顺序走', () => {
    const sortable: Array<Column<Row>> = [
      { key: 'name', header: '名称', render: (row) => row.name, sort: (a, b) => a.name.localeCompare(b.name) },
      { key: 'pct', header: '涨幅', numeric: true, render: (row) => row.pct, summary: (rows) => `首行 ${rows[0].pct}` },
    ]
    render(<DataTable columns={sortable} rows={ROWS} rowKey={rowKey} caption="名单" />)
    expect(within(foot()).getByText('首行 1.5').textContent).toBe('首行 1.5')
    fireEvent.click(screen.getByRole('button', { name: /名称/ }))
    fireEvent.click(screen.getByRole('button', { name: /名称/ }))
    expect(within(foot()).getByText('首行 -3.25').textContent).toBe('首行 -3.25')
  })

  it('加载 / 错误 / 空态不出合计行', () => {
    render(<DataTable columns={columns} rows={[]} rowKey={rowKey} loading />)
    expect(document.querySelector('tfoot')).toBeNull()
    cleanup()
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} error={<p>取数失败</p>} />)
    expect(document.querySelector('tfoot')).toBeNull()
    cleanup()
    render(<DataTable columns={columns} rows={[]} rowKey={rowKey} empty={<p>暂无数据</p>} />)
    expect(document.querySelector('tfoot')).toBeNull()
  })
})

describe('DataTable · 展开行', () => {
  const columns: Array<Column<Row>> = [{ key: 'name', header: '名称', render: (row) => row.name }]
  const expandable: Expandable<Row> = { render: (row) => <p>「{row.name}」的依据</p>, label: '展开依据' }

  /** 两行的展开按钮共用同一个动作名，取第 index 行那个 */
  const expander = (index: number, name = '展开依据'): HTMLElement =>
    screen.getAllByRole('button', { name }).filter((node) => node.closest('tbody') !== null)[index]

  it('默认收起，aria-expanded 跟着状态走，展开区整行铺满', () => {
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} expandable={expandable} caption="名单" />)
    const trigger = expander(0)
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText('「A」的依据')).toBeTruthy()
    const expandedCell = screen.getByText('「A」的依据').closest('td')
    expect(expandedCell?.getAttribute('colspan')).toBe('2')
  })

  it('展开按钮不触发行点击', () => {
    const onRowClick = vi.fn()
    render(
      <DataTable
        columns={columns}
        rows={ROWS}
        rowKey={rowKey}
        expandable={expandable}
        onRowClick={onRowClick}
        caption="名单"
      />,
    )
    fireEvent.click(screen.getByRole('row', { name: /A/ }))
    expect(onRowClick).toHaveBeenCalledTimes(1)
    fireEvent.click(expander(0))
    expect(onRowClick).toHaveBeenCalledTimes(1)
  })

  it('不给 label 时用状态本身做动作名，展开后读屏能知道是收起', () => {
    render(
      <DataTable
        columns={columns}
        rows={[ROWS[0]]}
        rowKey={rowKey}
        expandable={{ render: (row) => <p>「{row.name}」的依据</p> }}
        caption="名单"
      />,
    )
    const trigger = screen.getByRole('button', { name: '展开详情' })
    fireEvent.click(trigger)
    expect(screen.getByRole('button', { name: '收起详情' })).toBeTruthy()
    fireEvent.click(trigger)
    expect(screen.getByRole('button', { name: '展开详情' })).toBeTruthy()
  })

  it('defaultExpanded 直接展开，收起后内容消失', () => {
    render(
      <DataTable
        columns={columns}
        rows={ROWS}
        rowKey={rowKey}
        expandable={{ ...expandable, defaultExpanded: ['600002'] }}
        caption="名单"
      />,
    )
    expect(screen.getByText('「B」的依据')).toBeTruthy()
    fireEvent.click(expander(1))
    expect(screen.queryByText('「B」的依据')).toBeNull()
  })
})

describe('DataTable · 既有能力不回归', () => {
  const columns: Array<Column<Row>> = [
    { key: 'name', header: '名称', render: (row) => row.name, sort: (a, b) => a.name.localeCompare(b.name) },
  ]

  it('排序三态 cycle 与 aria-sort 保持', () => {
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} caption="名单" />)
    const head = screen.getByRole('columnheader', { name: '名称' })
    fireEvent.click(screen.getByRole('button', { name: /名称/ }))
    expect(head.getAttribute('aria-sort')).toBe('ascending')
    fireEvent.click(screen.getByRole('button', { name: /名称/ }))
    expect(head.getAttribute('aria-sort')).toBe('descending')
    fireEvent.click(screen.getByRole('button', { name: /名称/ }))
    expect(head.getAttribute('aria-sort')).toBeNull()
  })

  it('loading 时表体是骨架，区域不消失', () => {
    const { container } = render(<DataTable columns={columns} rows={[]} rowKey={rowKey} loading caption="名单" />)
    const wrapper = container.firstElementChild as HTMLElement
    expect(wrapper.getAttribute('aria-busy')).toBe('true')
    expect(container.querySelector('table')).toBeTruthy()
    expect(screen.getAllByRole('row')).toHaveLength(6)
    expect(document.querySelectorAll('.ldst-skeleton').length).toBeGreaterThan(0)
  })

  it('密度三档仍然映射到行高', () => {
    render(<DataTable columns={columns} rows={ROWS} rowKey={rowKey} density="compact" caption="名单" />)
    expect(screen.getByRole('cell', { name: 'A' }).parentElement?.className).toContain('h-row-compact')
  })
})

describe('StatePanel · 四态同源', () => {
  const copy = {
    empty: { title: '还没有条件', description: '写下明天要盯的代码。', action: <button type="button">新增条件</button> },
    error: { title: '对账未完成', detail: '数据源不可用', code: 'HTTP 503', onRetry: () => undefined },
    notice: <p>部分数据缺失（涨停池）</p>,
  }

  const panel = (state: PageState) => (
    <StatePanel state={state} {...copy}>
      <p>正文内容</p>
    </StatePanel>
  )

  it('loading：骨架在场、内容不渲染，读屏知道在忙而不是「什么都没有」', () => {
    render(panel('loading'))
    expect(screen.queryByText('正文内容')).toBeNull()
    const status = screen.getByRole('status')
    expect(status.getAttribute('aria-busy')).toBe('true')
    expect(status.getAttribute('aria-live')).toBe('polite')
    expect(status.textContent).toContain('正在加载')
    expect(document.querySelectorAll('.ldst-skeleton').length).toBeGreaterThan(0)
  })

  it('skeleton 可换成屏幕自己的骨架，行数可配', () => {
    render(
      <StatePanel state="loading" skeleton={<p>表格骨架</p>}>
        <p>正文内容</p>
      </StatePanel>,
    )
    expect(screen.getByText('表格骨架')).toBeTruthy()
    cleanup()
    render(
      <StatePanel state="loading" skeletonRows={2}>
        <p>正文内容</p>
      </StatePanel>,
    )
    expect(document.querySelectorAll('.ldst-skeleton').length).toBe(8)
  })

  it('error：结论 / 原因 / 代码 / 重试，内容替换而不是并存', () => {
    const onRetry = vi.fn()
    render(
      <StatePanel state="error" error={{ ...copy.error, onRetry }}>
        <p>正文内容</p>
      </StatePanel>,
    )
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('对账未完成')
    expect(alert.textContent).toContain('数据源不可用')
    expect(alert.textContent).toContain('HTTP 503')
    expect(screen.queryByText('正文内容')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('empty：文案与出口动作都来自屏幕侧', () => {
    render(panel('empty'))
    expect(screen.getByText('还没有条件')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '新增条件' }))
    expect(screen.queryByText('正文内容')).toBeNull()
  })

  it('partial / offline：说明条在上，内容保留（降级不等于没有）', () => {
    for (const state of ['partial', 'offline'] as PageState[]) {
      cleanup()
      render(panel(state))
      expect(screen.getByText('部分数据缺失（涨停池）')).toBeTruthy()
      expect(screen.getByText('正文内容')).toBeTruthy()
    }
  })

  it('normal：原样渲染，不多包一层带 class 的容器', () => {
    render(
      <StatePanel state="normal" className="should-not-appear">
        <p data-testid="body">正文内容</p>
      </StatePanel>,
    )
    expect(screen.getByTestId('body').parentElement?.className).not.toContain('should-not-appear')
  })

  it('没给文案也有兜底，不会渲染出空面板', () => {
    render(
      <StatePanel state="error">
        <p>正文内容</p>
      </StatePanel>,
    )
    expect(screen.getByRole('alert').textContent).toContain('取数失败')
    cleanup()
    render(
      <StatePanel state="empty">
        <p>正文内容</p>
      </StatePanel>,
    )
    expect(screen.getByText('暂无数据')).toBeTruthy()
  })
})

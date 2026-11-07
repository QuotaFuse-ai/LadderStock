// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MASK, setPrivacyMode } from '../src/lib/privacy'
import { TradeCsvImport, importableRecords } from '../src/screens/my-stocks/TradeCsvImport'
import {
  CSV_MAX_ROWS,
  TRADE_CSV_TEMPLATE,
  decodeTableFile,
  normalizeDate,
  parseTradeCsv,
  splitTable,
} from '../src/screens/my-stocks/tradeCsv'

/**
 * QUOT-437 流水 CSV 导入（前端侧）：
 * - 解析在本机（表头别名、日期、数字、代码位数、行数上限）；
 * - 预览与写入同一套检查：问题行 / 重复行不可勾，勾选变了必须重新预览才允许写；
 * - 只发勾选到的行，成功后展示备份文件名；
 * - 请求体 / 文件大小上限在发请求前挡住，并给「分成几份」的话；
 * - 隐私模式遮明细与原因里的金额。
 */

const OK_ROW = { problem: null, duplicate: null, duplicate_of: null }
const BAD_ROW = { problem: '卖出 500 股，超过当时持有的 100 股', duplicate: null, duplicate_of: null }
const DUP_EXISTING = { problem: null, duplicate: 'existing' as const, duplicate_of: 42 }

function stubApi(responses: Record<string, unknown>, calls: Array<{ url: string; body: unknown }>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ url, body })
      const key = Object.keys(responses).find((prefix) => url.startsWith(prefix))
      if (!key) throw new Error(`unexpected fetch: ${url}`)
      const value = responses[key]
      if (value instanceof Error) throw value
      return new Response(JSON.stringify(value), {
        status: typeof value === 'object' && value !== null && 'status' in value ? Number((value as { status: number }).status) : 200,
      })
    }),
  )
}

const PREVIEW_ENDPOINT = '/api/portfolio/ledger/import/preview'
const IMPORT_ENDPOINT = '/api/portfolio/ledger/import'

const CSV = [
  '日期,类型,代码,名称,数量,价格,费用',
  '2025-01-02,买入,600519,贵州茅台,100,1500,5',
  '2025-02-03,卖出,600519,贵州茅台,500,1700,8',
  // 第 3 行本机就没过（代码只剩 1 位），根本不会进 `records`
  '2025-03-04,买入,1,万科A,100,10',
  '2025-04-01,买入,000001,平安银行,200,12',
  '2025-05-06,买入,600000,浦发银行,300,10',
].join('\n')

/** 后端按 `records` 的下标回行：0 / 2 可导，1 卖超，3 和已记的一笔完全一样 */
const PREVIEW_ROWS = [
  { index: 0, ...OK_ROW },
  { index: 1, ...BAD_ROW },
  { index: 2, ...OK_ROW },
  { index: 3, ...DUP_EXISTING },
]

beforeEach(() => {
  window.localStorage.clear()
  setPrivacyMode(false)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('tradeCsv 本机解析', () => {
  it('认列名别名、日期格式与千分位；不认识的列只说「没用上」', () => {
    const parsed = parseTradeCsv(
      [
        '成交日期(YYYYMMDD),买卖标志,证券代码,股数,成交价,备注,账户',
        '20250102,证券买入,600519,"1,200",1500.00,第一批,光大',
      ].join('\n'),
    )
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.unused).toEqual(['账户'])
    const row = parsed.rows[0]
    expect(row.error).toBeNull()
    expect(row.record).toMatchObject({
      trade_date: '2025-01-02',
      type: 'buy',
      symbol: '600519',
      shares: 1200,
      price: 1500,
      note: '第一批',
    })
    // 名称列没有就不硬造
    expect(row.record?.name ?? '').toBe('')
  })

  it('1–5 位数字代码一律退回：不补 0，也不当成别的市场', () => {
    const parsed = parseTradeCsv('日期,类型,代码\n2025-01-02,买入,1')
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.rows[0].error).toMatch(/只有 1 位/)
    expect(parsed.rows[0].record).toBeNull()
  })

  it('B 股与负数价格按「记一笔」同一条检查退回，不取绝对值', () => {
    const parsed = parseTradeCsv(
      ['日期,类型,代码,数量,价格', '2025-01-02,买入,900901,100,-12'].join('\n'),
    )
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.rows[0].error).toMatch(/B 股/)
  })

  it('缺列 / 重复列 / 行数超限：整体报错，不发半成品', () => {
    expect(parseTradeCsv('日期,金额\n2025-01-02,10').ok).toBe(false)
    expect((parseTradeCsv('日期,类型,代码,日期') as { error?: string }).error).toMatch(/有两列都是「日期」/)
    const tooMany = ['日期,类型,代码']
      .concat(Array.from({ length: CSV_MAX_ROWS + 1 }, (_, i) => `2025-01-0${(i % 9) + 1},买入,600519`))
      .join('\n')
    const parsed = parseTradeCsv(tooMany)
    expect(parsed.ok).toBe(false)
    if (parsed.ok) return
    expect((parsed as { error: string }).error).toMatch(/分成几份/)
  })

  it('分隔符、引号与换行：RFC 4180 的引号规则自己实现', () => {
    const tabs = splitTable('日期\t类型\t代码\n2025-01-02\t买入\t600519', '\t')
    expect(tabs[1]?.cells).toEqual(['2025-01-02', '买入', '600519'])
    const quoted = splitTable('a,"b,1",c\n1,"两""引号",3', ',')
    expect(quoted[1]?.cells).toEqual(['1', '两"引号', '3'])
  })

  it('日期归一：2025/1/2、2025年1月2日、带时间都成 YYYY-MM-DD', () => {
    expect(normalizeDate('2025/1/2')).toBe('2025-01-02')
    expect(normalizeDate('2025年1月2日')).toBe('2025-01-02')
    expect(normalizeDate('2025-01-02 14:30:00')).toBe('2025-01-02')
    expect(normalizeDate('不是日期')).toBe('不是日期')
  })

  it('文件解码：UTF-8 优先，GBK 兜底，xlsx 明确拒收', () => {
    const utf8 = new TextEncoder().encode('日期,类型,代码')
    expect(decodeTableFile(utf8)).toEqual({ text: '日期,类型,代码' })
    const zip = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0, 0])
    const result = decodeTableFile(zip)
    expect('error' in result).toBe(true)
    if ('error' in result) expect(result.error).toMatch(/另存为 CSV/)
  })

  it('模板每行都能被自己的解析器读成流水', () => {
    const parsed = parseTradeCsv(TRADE_CSV_TEMPLATE)
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.rows.every((row) => row.record !== null)).toBe(true)
    expect(parsed.rows.map((row) => row.record?.type)).toEqual([
      'opening',
      'buy',
      'sell',
      'dividend',
      'split',
    ])
  })
})

describe('importableRecords（预览一致才可写）', () => {
  const parsed = parseTradeCsv(CSV)
  if (!parsed.ok) throw new Error('fixture 坏了')
  const valid = parsed.rows.filter((row) => row.record !== null)
  const records = valid.map((row) => row.record!)

  it('本机就退回的行不进 records，剩下的 4 行才交给后端', () => {
    expect(records).toHaveLength(4)
    expect(valid.some((row) => row.record?.symbol === '000001')).toBe(true)
  })

  it('勾选变了、新预览还没回来时一笔都不导', () => {
    const preview = PREVIEW_ROWS
    const previewFor = 'true,true,false,true'
    expect(importableRecords(valid, preview, previewFor, [true, true, false, true])).toHaveLength(1)
    expect(importableRecords(valid, preview, previewFor, [true, true, false, false])).toHaveLength(0)
  })

  it('问题行与重复行即使被勾上也不导', () => {
    const picked = importableRecords(valid, PREVIEW_ROWS, 'true,true,true,true', [true, true, true, true])
    expect(picked).toHaveLength(2)
    expect(picked.map((record) => record.symbol)).toEqual(['600519', '000001'])
  })
})

describe('从表格导入组件', () => {
  it('粘贴 → 检查 → 预览：问题行不可勾，默认只勾能对上的行', async () => {
    const calls: Array<{ url: string; body: unknown }> = []
    stubApi(
      {
        [PREVIEW_ENDPOINT]: {
          ok: true,
          count: 4,
          limit: CSV_MAX_ROWS,
          max_bytes: 245760,
          rows: PREVIEW_ROWS,
          existing_issues: [],
        },
      },
      calls,
    )
    render(<TradeCsvImport method="average" onImported={() => {}} />)

    fireEvent.change(screen.getByLabelText('表格内容'), { target: { value: CSV } })
    fireEvent.click(screen.getByRole('button', { name: '检查' }))

    await waitFor(() => expect(calls.some((call) => call.url.startsWith(PREVIEW_ENDPOINT))).toBe(true))
    const body = calls.find((call) => call.url.startsWith(PREVIEW_ENDPOINT))?.body as {
      method: string
      records: unknown[]
      selected: boolean[]
    }
    expect(body.method).toBe('average')
    // 本机就没过的第 3 行（代码 1 位）根本没交给后端：4 行而不是 5 行
    expect(body.records).toHaveLength(4)
    expect(body.selected).toEqual([true, true, true, true])

    // 卖超那一行给出后端原因且不可勾；和已记流水重复那行也不给勾
    expect(await screen.findByText(/卖出 500 股，超过当时持有的 100 股/)).toBeTruthy()
    expect(screen.getByText(/只有 1 位/)).toBeTruthy()
    expect(screen.getByText(/和已记的一笔完全一样/)).toBeTruthy()
    expect(screen.getAllByRole('checkbox')).toHaveLength(2)
    expect(screen.getByRole('button', { name: /导入选中的 2 笔/ })).toBeTruthy()
  })

  it('导入只发勾选且预览通过的行；成功后展示备份文件名', async () => {
    const calls: Array<{ url: string; body: unknown }> = []
    stubApi(
      {
        [PREVIEW_ENDPOINT]: {
          ok: true,
          count: 4,
          limit: CSV_MAX_ROWS,
          max_bytes: 245760,
          rows: PREVIEW_ROWS,
          existing_issues: [],
        },
        [IMPORT_ENDPOINT]: {
          status: 200,
          ok: true,
          imported: 2,
          ids: [11, 12],
          backup: 'journal.sqlite.bak-portfolio-20261106T101500',
          at: '2026-11-06T10:15:00+08:00',
        },
      },
      calls,
    )
    const onImported = vi.fn()
    render(<TradeCsvImport method="fifo" onImported={onImported} />)

    fireEvent.change(screen.getByLabelText('表格内容'), { target: { value: CSV } })
    fireEvent.click(screen.getByRole('button', { name: '检查' }))
    await screen.findByText(/导入选中的 2 笔/)

    fireEvent.click(screen.getByRole('button', { name: /导入选中的 2 笔/ }))
    await waitFor(() => expect(onImported).toHaveBeenCalled())

    // 预览 URL 是写入 URL 的前缀，必须按整个 URL 比
    const importCall = calls.find((call) => call.url === IMPORT_ENDPOINT)
    expect(importCall?.url).toBe(IMPORT_ENDPOINT)
    // 写入接口只收 `{method, records}`：`selected` 是它不认的字段，前端不能发
    expect(importCall?.body).toMatchObject({ method: 'fifo' })
    expect(Object.keys(importCall?.body as object)).toEqual(['method', 'records'])
    expect((importCall?.body as { records: unknown[] }).records).toHaveLength(2)
    expect(onImported).toHaveBeenCalledWith({
      imported: 2,
      backup: 'journal.sqlite.bak-portfolio-20261106T101500',
    })
  })

  it('整批被后端退回时：一行都不写，原因原样展示', async () => {
    const calls: Array<{ url: string; body: unknown }> = []
    stubApi(
      {
        [PREVIEW_ENDPOINT]: {
          ok: true,
          count: 4,
          limit: CSV_MAX_ROWS,
          max_bytes: 245760,
          rows: PREVIEW_ROWS,
          existing_issues: [],
        },
        [IMPORT_ENDPOINT]: {
          status: 422,
          detail: '这次导入没有写入：1/2 行有问题。第 2 行：卖出超过持有',
        },
      },
      calls,
    )
    render(<TradeCsvImport method="average" onImported={() => {}} />)
    fireEvent.change(screen.getByLabelText('表格内容'), { target: { value: CSV } })
    fireEvent.click(screen.getByRole('button', { name: '检查' }))
    await screen.findByText(/导入选中的 2 笔/)
    fireEvent.click(screen.getByRole('button', { name: /导入选中的 2 笔/ }))

    expect(await screen.findByText(/这次导入没有写入/)).toBeTruthy()
    expect(screen.queryByText(/已导入/)).toBeNull()
  })

  it('取消勾选后要按新勾选重新预览，核对完才放开导入按钮', async () => {
    const calls: Array<{ url: string; body: unknown }> = []
    stubApi(
      {
        [PREVIEW_ENDPOINT]: {
          ok: true,
          count: 4,
          limit: CSV_MAX_ROWS,
          max_bytes: 245760,
          rows: PREVIEW_ROWS,
          existing_issues: [],
        },
      },
      calls,
    )
    render(<TradeCsvImport method="average" onImported={() => {}} />)
    fireEvent.change(screen.getByLabelText('表格内容'), { target: { value: CSV } })
    fireEvent.click(screen.getByRole('button', { name: '检查' }))

    const boxes = await screen.findAllByRole('checkbox')
    fireEvent.click(boxes[0] as HTMLElement)
    const button = screen.getByRole('button', { name: /导入选中的/ }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    // 置灰要给出看得见的理由（§4.8）：理由挂在 aria-describedby 上
    const reasonId = button.getAttribute('aria-describedby')
    expect(reasonId).toBeTruthy()
    expect(document.getElementById(reasonId ?? '')?.textContent).toMatch(/重新核对/)
    await waitFor(() => expect(calls.filter((call) => call.url.startsWith(PREVIEW_ENDPOINT)).length).toBe(2))
    const second = calls.filter((call) => call.url.startsWith(PREVIEW_ENDPOINT))[1]?.body as { selected: boolean[] }
    expect(second.selected[0]).toBe(false)
  })

  it('粘贴内容里的金额在隐私模式下遮成掩码', async () => {
    const calls: Array<{ url: string; body: unknown }> = []
    stubApi(
      {
        [PREVIEW_ENDPOINT]: {
          ok: true,
          count: 4,
          limit: CSV_MAX_ROWS,
          max_bytes: 245760,
          rows: PREVIEW_ROWS,
          existing_issues: [],
        },
      },
      calls,
    )
    setPrivacyMode(true)
    const { container } = render(<TradeCsvImport method="average" onImported={() => {}} />)
    fireEvent.change(screen.getByLabelText('表格内容'), { target: { value: CSV } })
    fireEvent.click(screen.getByRole('button', { name: '检查' }))
    await screen.findByText(/导入选中的 2 笔/)

    expect(container.textContent).toContain(MASK)
    // 遮的是自己的数字：行号、日期不遮
    expect(container.textContent).toContain('2025-01-02')
  })

  it('模板下载：没有 Blob 环境时退回把模板填进输入框', () => {
    const calls: Array<{ url: string; body: unknown }> = []
    stubApi({}, calls)
    const original = URL.createObjectURL
    // @ts-expect-error 测试里临时拿掉 createObjectURL，走降级分支
    URL.createObjectURL = undefined
    render(<TradeCsvImport method="average" onImported={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /下载模板/ }))
    expect((screen.getByLabelText('表格内容') as HTMLTextAreaElement).value).toBe(TRADE_CSV_TEMPLATE)
    expect(calls).toHaveLength(0)
    URL.createObjectURL = original
  })
})

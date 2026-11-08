import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Download, FileUp, Table2 } from 'lucide-react'

import { describeApiError } from '../../api'
import {
  importPortfolioTrades,
  previewTradeImport,
  type ImportPreviewRow,
  type PortfolioMethod,
} from '../../api/radar'
import { Banner, Button, DataTable, Field, Textarea, type Column } from '../../components/ui'
import { cn } from '../../lib/cn'
import { MISSING } from '../../lib/format'
import { usePrivacyMode } from '../../lib/privacy'
import { PrivacyText } from './PrivacyText'
import {
  CSV_MAX_ROWS,
  TRADE_CSV_TEMPLATE,
  decodeTableFile,
  parseTradeCsv,
  recordDetail,
  typeLabel,
  type CsvParse,
  type CsvRow,
} from './tradeCsv'
import type { TradeInput } from '../../api/radar'

/**
 * 从表格导入流水（QUOT-437）：本机解析 → 后端预览（不写）→ 勾选 → 一次写入（先备份）。
 *
 * 四条口径：
 * 1. **解析在本机**：CSV / 粘贴的表格不出浏览器，后端只收到和「记一笔」同形的 `records`；
 *    不引第三方 CSV 库（`tradeCsv.ts` 自己实现 RFC 4180 的那点规则）。
 * 2. **预览与写入同一套检查**：只有「按当前勾选重算后没问题」的行才交给写入接口——
 *    写入接口会原样再查一遍，任何一行不合格整批不写，所以勾选变了必须重新预览。
 * 3. **问题行与重复行不可勾**：数据不对的行、和已记流水 / 本批前一行完全一样的行，
 *    这次不导入，原因照后端原文展示（不猜是录重还是真有两笔）。
 * 4. 一次最多 `CSV_MAX_ROWS` 行、文件 ≤2MB、请求体 ≤240KB，超了在发请求前就退回「分成几份」。
 */

const MAX_FILE_BYTES = 2 * 1024 * 1024
const MAX_BODY_BYTES = 240 * 1024
const REPREVIEW_DEBOUNCE_MS = 250
const TOO_BIG = '这份表格一次传不过去：请分成几份导入'

const byteLengthOf = (value: unknown) => new TextEncoder().encode(JSON.stringify(value)).length

/**
 * 可以写进去的行：只认**和当前勾选对得上**的那份预览。
 *
 * 勾选刚改、按新勾选重算的预览还没回来时一笔都不导——否则会按旧预览把
 * 「卖出超过持有」那类行写进去（后端按整批重放才知道）。
 */
export function importableRecords(
  rows: CsvRow[],
  preview: ImportPreviewRow[] | null,
  previewFor: string | null,
  selected: boolean[],
): TradeInput[] {
  if (!preview || previewFor !== selected.join(',')) return []
  const picked: TradeInput[] = []
  rows.forEach((row, index) => {
    if (!selected[index] || !row.record) return
    if (preview[index]?.problem === null && preview[index]?.duplicate == null) {
      picked.push(row.record)
    }
  })
  return picked
}

export interface TradeCsvImportProps {
  /** 与手动「记一笔」同一个成本法：预览要按它重放，写入也按它校验。 */
  method: PortfolioMethod
  /** 写成功后由页面刷新视图并展示备份文件名。 */
  onImported: (summary: { imported: number; backup: string | null }) => void
}

interface PreviewRow {
  key: string
  row: CsvRow
  /** 在 `records`（后端看到的下标）里的位置；本机就没过的行是 `-1` */
  index: number
  info: ImportPreviewRow | null
}

export function TradeCsvImport({ method, onImported }: TradeCsvImportProps) {
  const { on: privacyOn } = usePrivacyMode()
  const [text, setText] = useState('')
  const [parsed, setParsed] = useState<CsvParse | null>(null)
  const [preview, setPreview] = useState<ImportPreviewRow[] | null>(null)
  const [previewFor, setPreviewFor] = useState<string | null>(null)
  const [selected, setSelected] = useState<boolean[]>([])
  const [existingIssues, setExistingIssues] = useState<string[]>([])
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<{ imported: number; backup: string | null } | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  useEffect(
    () => () => {
      abortRef.current?.abort()
      if (timerRef.current) clearTimeout(timerRef.current)
    },
    [],
  )

  // 本机检查过的行才能交给后端；`records` 与预览的 `index` 一一对应
  const valid = useMemo(
    () => (parsed?.ok ? parsed.rows.filter((row) => row.record !== null) : []),
    [parsed],
  )
  const records = useMemo(() => valid.map((row) => row.record as TradeInput), [valid])

  const reset = () => {
    abortRef.current?.abort()
    if (timerRef.current) clearTimeout(timerRef.current)
    setParsed(null)
    setPreview(null)
    setPreviewFor(null)
    setSelected([])
    setExistingIssues([])
    setMessage(null)
    setDone(null)
  }

  /** 后端预览：`selected` 决定「按哪几行重放」，不写库。 */
  const ask = async (next: boolean[] | undefined, rows: TradeInput[]) => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const payload = await previewTradeImport({ method, records: rows, selected: next ?? rows.map(() => true) }, controller.signal)
      if (controller.signal.aborted) return null
      setPreview(payload.rows)
      setExistingIssues(
        payload.existing_issues.map(
          (issue) => `${issue.date || MISSING} ${typeLabel(issue.type)} ${issue.symbol || MISSING}：${issue.why}`,
        ),
      )
      return payload.rows
    } catch (cause) {
      if (!controller.signal.aborted) setMessage(describeApiError(cause).detail)
      return null
    }
  }

  const check = async () => {
    reset()
    const result = parseTradeCsv(text)
    setParsed(result)
    if (!result.ok) return
    const rows = result.rows.filter((row) => row.record !== null).map((row) => row.record as TradeInput)
    if (rows.length === 0) {
      setMessage('这一份里没有任何一行是能导入的：先看下面每行的原因')
      return
    }
    if (byteLengthOf({ method, records: rows, selected: rows.map(() => true) }) > MAX_BODY_BYTES) {
      setMessage(TOO_BIG)
      return
    }
    setBusy(true)
    const rows2 = await ask(undefined, rows)
    setBusy(false)
    if (!rows2) return
    // 默认勾：本机与后端都点头的行（重复行先不勾，理由逐行写着）
    const defaults = rows2.map((row) => row.problem === null && row.duplicate === null)
    setSelected(defaults)
    setPreviewFor(defaults.join(','))
  }

  const toggle = (index: number) => {
    const next = selected.map((value, position) => (position === index ? !value : value))
    setSelected(next)
    // 勾选变了，「卖出超过持有」要按新的勾选重算；连点几下只问最后一次
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      void ask(next, records).then((rows2) => {
        if (rows2) setPreviewFor(next.join(','))
      })
    }, REPREVIEW_DEBOUNCE_MS)
  }

  const pickFile = async (file: File | undefined) => {
    if (!file) return
    if (file.size > MAX_FILE_BYTES) {
      setMessage('文件超过 2MB：请分成几份导入')
      return
    }
    const decoded = decodeTableFile(await file.arrayBuffer())
    if ('error' in decoded) {
      setMessage(decoded.error)
      return
    }
    reset()
    setText(decoded.text)
  }

  const downloadTemplate = () => {
    if (typeof window === 'undefined' || typeof URL.createObjectURL !== 'function') {
      // 没有 Blob URL 的环境（测试、SSR）：直接当文本填进去，口径不变
      setText(TRADE_CSV_TEMPLATE)
      setParsed(null)
      return
    }
    const blob = new Blob([`\ufeff${TRADE_CSV_TEMPLATE}`], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'ladderstock-流水模板.csv'
    link.click()
    URL.revokeObjectURL(url)
  }

  const chosen = importableRecords(valid, preview, previewFor, selected)
  const rechecking = preview !== null && previewFor !== selected.join(',')

  const importNow = async () => {
    if (chosen.length === 0) return
    if (byteLengthOf({ method, records: chosen }) > MAX_BODY_BYTES) {
      setMessage(TOO_BIG)
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      const result = await importPortfolioTrades({ method, records: chosen })
      const summary = { imported: result.imported, backup: result.backup }
      setText('')
      reset()
      setDone(summary)
      onImported(summary)
    } catch (cause) {
      setMessage(describeApiError(cause).detail)
    } finally {
      setBusy(false)
    }
  }

  const rows: PreviewRow[] = useMemo(() => {
    if (!parsed?.ok) return []
    const position = new Map<CsvRow, number>()
    valid.forEach((row, index) => position.set(row, index))
    return parsed.rows.map((row) => ({
      key: String(row.line),
      row,
      index: position.get(row) ?? -1,
      info: preview?.[position.get(row) ?? -1] ?? null,
    }))
  }, [parsed, valid, preview])

  const columns: Array<Column<PreviewRow>> = [
    {
      key: 'select',
      header: '导入',
      width: '48px',
      align: 'center',
      render: (item) => {
        const can = item.index >= 0 && item.info?.problem === null && item.info?.duplicate === null
        if (!can) {
          return <span className="text-caption text-text-muted" title="这一行不能导入">{MISSING}</span>
        }
        return (
          <input
            type="checkbox"
            checked={Boolean(selected[item.index])}
            onChange={() => toggle(item.index)}
            aria-label={`导入第 ${item.row.line} 行`}
            className="size-4 cursor-pointer accent-accent-solid"
          />
        )
      },
    },
    {
      key: 'line',
      header: '行',
      width: '48px',
      numeric: true,
      render: (item) => <span className="ldst-num text-caption text-text-muted">{item.row.line}</span>,
    },
    {
      key: 'date',
      header: '日期',
      width: '104px',
      render: (item) => (
        <span className="ldst-num whitespace-nowrap">
          {item.row.record?.trade_date || item.row.cells.date || MISSING}
        </span>
      ),
    },
    {
      key: 'type',
      header: '类型',
      width: '96px',
      render: (item) => (
        <span className="whitespace-nowrap">{item.row.record ? typeLabel(item.row.record.type) : item.row.cells.type || MISSING}</span>
      ),
    },
    {
      key: 'symbol',
      header: '名称 / 代码',
      truncate: true,
      render: (item) => {
        const symbol = item.row.record?.symbol ?? item.row.cells.symbol
        const name = item.row.record?.name ?? item.row.cells.name
        return (
          <span className="flex min-w-0 items-baseline gap-1.5">
            <span className={name ? 'truncate font-medium' : 'text-text-muted'}>{name || MISSING}</span>
            <span className="ldst-num shrink-0 text-caption text-text-muted">{symbol || MISSING}</span>
          </span>
        )
      },
      cellTitle: (item) => `${item.row.record?.name || item.row.cells.name || '名称未填'} ${item.row.record?.symbol ?? item.row.cells.symbol}`,
    },
    {
      key: 'detail',
      header: '明细',
      width: '168px',
      render: (item) =>
        item.row.record ? (
          <PrivacyText
            className="ldst-num whitespace-nowrap text-caption text-text-secondary"
            text={recordDetail(item.row.record)}
          />
        ) : (
          <span className="text-text-muted">{MISSING}</span>
        ),
    },
    {
      key: 'status',
      header: '检查结果',
      truncate: true,
      render: (item) => {
        const status = statusOf(item, selected, Boolean(preview))
        return <span className={cn(status.tone === 'bad' ? 'text-danger' : status.tone === 'warn' ? 'text-warn' : 'text-text-muted')}>{masked(status.text, privacyOn)}</span>
      },
      cellTitle: (item) => statusOf(item, selected, Boolean(preview)).text,
    },
  ]

  const badCount =
    rows.filter((item) => item.row.error).length +
    (preview ?? []).filter((row) => row.problem !== null).length
  const duplicateCount = (preview ?? []).filter((row) => row.duplicate !== null).length

  return (
    <div className="flex flex-col gap-3 rounded border border-border bg-surface p-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <Table2 className="size-4 shrink-0 text-accent-solid" aria-hidden="true" />
        <h3 className="text-subtitle font-semibold text-text">从表格导入</h3>
        <p className="text-caption text-text-muted">
          粘贴或选 CSV，在本机解析；写之前先备份，要么全写要么一笔都不写。
        </p>
      </div>

      <p className="text-caption leading-body text-text-secondary">
        第一行是列名：<b>日期、类型、代码</b>必填，名称、数量、价格、费用、金额、比例、备注按类型填（和上面「记一笔」一样）。
        类型写 买入 / 卖出 / 现金分红 / 送转 / 期初持仓；代码写满 6 位 A 股。一次最多 {CSV_MAX_ROWS} 行。
      </p>

      <div className="flex flex-wrap gap-2">
        <Button
          variant="ghost"
         
          leadingIcon={<FileUp className="size-3.5" aria-hidden="true" />}
          onClick={() => fileRef.current?.click()}
        >
          选择 CSV 文件
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.tsv,.txt,text/csv,text/plain"
          className="hidden"
          aria-label="选择 CSV 文件"
          onChange={(event) => {
            void pickFile(event.target.files?.[0])
            event.target.value = ''
          }}
        />
        <Button variant="ghost" leadingIcon={<Download className="size-3.5" aria-hidden="true" />} onClick={downloadTemplate}>
          下载模板
        </Button>
        <Button
          variant="primary"
         
          loading={busy && !preview}
          disabled={!text.trim()}
          disabledReason={text.trim() ? undefined : '先粘贴表格或选一个 CSV 文件'}
          onClick={() => void check()}
        >
          检查
        </Button>
      </div>

      <Field label="表格内容" htmlFor="portfolio-import-text" hint="可以在表格软件里整表复制后粘贴到这里；隐私模式下输入内容显示成圆点">
        <Textarea
          id="portfolio-import-text"
          value={text}
          spellCheck={false}
          rows={5}
          onChange={(event) => {
            setText(event.target.value)
            if (parsed) reset()
          }}
          style={privacyOn ? ({ WebkitTextSecurity: 'disc' } as CSSProperties) : undefined}
          placeholder={'日期,类型,代码,名称,数量,价格,费用\n2025-01-02,买入,600519,贵州茅台,100,1500,5'}
        />
      </Field>

      {message && (
        <Banner tone="danger" title="这次没往下走">
          {masked(message, privacyOn)}
        </Banner>
      )}
      {parsed && !parsed.ok && (
        <Banner tone="danger" title="表格没读成">
          {parsed.error}
        </Banner>
      )}

      {parsed?.ok && (
        <>
          <p className="text-caption text-text-secondary">
            共 {rows.length} 行：勾了 {chosen.length} 行
            {badCount > 0 && `，${badCount} 行数据不对`}
            {duplicateCount > 0 && `，${duplicateCount} 行完全重复`}。
            {parsed.unused.length > 0 && <>这些列没用上：{parsed.unused.join('、')}。</>}
          </p>

          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(item) => item.key}
            density="compact"
            maxHeight="320px"
            empty={<p className="text-caption text-text-muted">表格里没有数据行。</p>}
          />

          {existingIssues.length > 0 && (
            <Banner tone="warn" title={`按现在的勾选写进去以后，已记的 ${existingIssues.length} 笔会不参与计算`}>
              <ul className="mt-1 flex flex-col gap-0.5 text-caption">
                {existingIssues.slice(0, 10).map((issue, index) => (
                  <li key={`${issue}-${index}`}>{masked(issue, privacyOn)}</li>
                ))}
              </ul>
              <p className="mt-1 text-caption text-text-muted">多半是表格里有和已记的重复、或日期对不上的流水，核对后再导入。</p>
            </Banner>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="primary"
              loading={busy && Boolean(preview)}
              disabled={chosen.length === 0 || rechecking}
              disabledReason={
                rechecking
                  ? '正在按新的勾选重新核对'
                  : chosen.length === 0
                    ? '没有可导入的行（问题行与重复行不导入）'
                    : undefined
              }
              onClick={() => void importNow()}
            >
              导入选中的 {chosen.length} 笔
            </Button>
            <p className="text-caption leading-body text-text-muted">
              {rechecking
                ? '勾选变了，正在按新的勾选重新核对，核对完才能导入。'
                : '写入前后端会先备份原文件；任何一行不合格整批不写。'}
            </p>
          </div>
        </>
      )}

      {done && (
        <Banner tone="ok" title={`已导入 ${done.imported} 笔流水`}>
          <p className="text-caption">
            备份文件：<code className="ldst-num">{done.backup ?? MISSING}</code>
          </p>
        </Banner>
      )}
    </div>
  )
}

/** 行状态：本机原因优先，其次是后端预览的原因。 */
function statusOf(
  item: PreviewRow,
  selected: boolean[],
  hasPreview: boolean,
): { text: string; tone: 'bad' | 'warn' | 'ok' } {
  if (item.row.error) return { text: item.row.error, tone: 'bad' }
  if (item.index < 0) return { text: '这一行没交给后端', tone: 'warn' }
  const info = item.info
  if (!info) return { text: hasPreview ? '预览没回来' : '还没检查', tone: 'ok' }
  if (info.problem) return { text: info.problem, tone: 'bad' }
  if (info.duplicate === 'existing') {
    return { text: '和已记的一笔完全一样：这次不导入，确实是另一笔请改日期或数量', tone: 'warn' }
  }
  if (info.duplicate === 'table') {
    return { text: `和本批第 ${info.duplicate_of !== null ? info.duplicate_of + 1 : '?'} 行完全一样：这次不导入`, tone: 'warn' }
  }
  return { text: selected[item.index] ? '可以导入' : '不导入', tone: 'ok' }
}

/** 隐私模式遮原因里的金额与股数：只动带单位的数字，日期、行号、百分比照旧。 */
const SECRET_WITH_UNIT = /[-+]?\d[\d,]*(?:\.\d+)?\s*(?=股|元)/g

function masked(value: string, privacyOn: boolean): string {
  if (!privacyOn) return value
  return value.replace(SECRET_WITH_UNIT, '••••')
}

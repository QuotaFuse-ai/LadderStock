import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileDown, Search } from 'lucide-react'

import {
  exportArchive,
  loadArchiveList,
  searchArchive,
  type ArchiveItem,
} from '../../api/archive'
import { describeApiError } from '../../api'
import { useAsync } from '../../lib/useAsync'
import {
  Banner,
  Button,
  Card,
  CardBody,
  CardHeader,
  DataTable,
  EmptyState,
  Input,
  SkeletonText,
  type Column,
} from '../ui'

/**
 * F8 研究档案面板：挂在 `/research` 入口页。
 *
 * 三件事，全走本机只读接口：
 * 1. **体检**：每份档案一行，写清「跑了几段 / 缺哪段 / 哪段降级 / 哪段未核实」；
 * 2. **检索**：按代码 / 日期 / 正文找，命中回原文片段与所在段；
 * 3. **导出**：合并成一份完整 Markdown（封面体检 + 逐段正文 + 依据链总览）。
 *
 * 导出是写盘动作，路径由后端返回并原样展示；页面不承诺"导出了什么内容"以外的东西。
 */

function summaryOf(item: ArchiveItem): string {
  const parts = [`${item.stageCount}/${item.totalStages} 段`]
  if (item.complete) parts.push('齐全')
  else if (item.missing.length > 0) parts.push(`缺 ${item.missing.join('、')}`)
  if (item.degraded.length > 0) parts.push(`降级 ${item.degraded.length}`)
  if (item.unverified.length > 0) parts.push(`未核实 ${item.unverified.length}`)
  if (item.failed.length > 0) parts.push(`失败 ${item.failed.join('、')}`)
  return parts.join(' · ')
}

export default function ResearchArchivePanel() {
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [query, setQuery] = useState('')
  const [exporting, setExporting] = useState<string | null>(null)
  const [note, setNote] = useState<{ tone: 'ok' | 'danger'; text: string } | null>(null)

  const archiveRequest = useAsync<ArchiveItem[]>(
    (signal) => (query ? searchArchive(query, 30, signal) : loadArchiveList(30, signal)),
    [query],
    { enabled: true },
  )

  const items = archiveRequest.data ?? []

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setQuery(keyword.trim())
  }

  const runExport = async (item: ArchiveItem) => {
    const key = `${item.date}-${item.code}`
    setExporting(key)
    setNote(null)
    try {
      const result = await exportArchive(item.code, item.date)
      setNote({
        tone: 'ok',
        text: `已导出 ${result.code} · ${result.date} → ${result.path}（${result.bytes} 字节，${
          result.complete ? '完整' : `缺 ${result.missing.join('、')}`
        }）`,
      })
    } catch (error) {
      setNote({ tone: 'danger', text: describeApiError(error).detail })
    } finally {
      setExporting(null)
    }
  }

  const columns: Array<Column<ArchiveItem>> = [
    { key: 'date', header: '日期', width: 'w-28', render: (row) => row.date },
    { key: 'code', header: '代码', width: 'w-20', render: (row) => <span className="ldst-num">{row.code}</span> },
    {
      key: 'summary',
      header: '体检',
      render: (row) => <span className="text-caption text-text-secondary">{summaryOf(row)}</span>,
    },
    {
      key: 'updated',
      header: '更新于',
      width: 'w-40',
      render: (row) => <span className="ldst-num text-caption text-text-muted">{row.updatedAt ?? '—'}</span>,
    },
    {
      key: 'actions',
      header: '操作',
      width: 'w-40',
      render: (row) => (
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            onClick={(event) => {
              event.stopPropagation()
              navigate(`/research/${encodeURIComponent(row.code)}?date=${row.date}`)
            }}
          >
            打开
          </Button>
          <Button
            variant="secondary"
            leadingIcon={<FileDown className="size-3.5" aria-hidden="true" />}
            loading={exporting === `${row.date}-${row.code}`}
            onClick={(event) => {
              event.stopPropagation()
              void runExport(row)
            }}
          >
            导出
          </Button>
        </div>
      ),
    },
  ]

  return (
    <Card className="max-w-content">
      <CardHeader
        title="研究档案"
        subtitle="已落盘的六段产物；按代码 / 日期 / 正文检索，可合并导出一份完整报告。"
      />
      <CardBody className="flex flex-col gap-3">
        <form onSubmit={submit} className="flex flex-wrap items-center gap-2">
          <Input
            value={keyword}
            placeholder="代码 / 日期 / 正文关键词，例如 600202 或 主营业务"
            leadingIcon={<Search className="size-4" aria-hidden="true" />}
            onChange={(event) => {
              setKeyword(event.target.value)
              if (event.target.value === '' && query !== '') setQuery('')
            }}
            aria-label="检索研究档案"
            className="w-full max-w-md"
          />
          <Button type="submit" variant="primary">
            检索
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              setKeyword('')
              setQuery('')
              archiveRequest.reload()
            }}
          >
            刷新
          </Button>
        </form>

        {note && (
          <Banner tone={note.tone === 'ok' ? 'ok' : 'danger'} title={note.tone === 'ok' ? '导出完成' : '导出失败'}>
            {note.text}
          </Banner>
        )}

        {archiveRequest.status === 'error' ? (
          <Banner tone="danger" title={describeApiError(archiveRequest.error).title}>
            {describeApiError(archiveRequest.error).detail}
          </Banner>
        ) : archiveRequest.status === 'loading' ? (
          <SkeletonText lines={4} />
        ) : items.length === 0 ? (
          <EmptyState
            compact
            title={query ? `没有含「${query}」的档案` : '还没有研究档案'}
            description={
              query
                ? '换个关键词试试：代码、日期，或正文里的原话。'
                : '先在报告页跑一次六段研究（company→…→report），每段落盘后这里就会出现一行体检。'
            }
          />
        ) : (
          <DataTable<ArchiveItem>
            columns={columns}
            rows={items}
            rowKey={(row) => `${row.date}-${row.code}`}
            caption={query ? `检索「${query}」命中 ${items.length} 份` : `共 ${items.length} 份档案`}
            density="compact"
            onRowClick={(row) => navigate(`/research/${encodeURIComponent(row.code)}?date=${row.date}`)}
          />
        )}

        {query && items.some((item) => item.matches.length > 0) && (
          <div className="flex flex-col gap-1 text-caption text-text-secondary">
            {items
              .filter((item) => item.matches.length > 0)
              .slice(0, 3)
              .map((item) => (
                <span key={`${item.date}-${item.code}`}>
                  {item.date} {item.code}：{item.matches[0]?.stageTitle} — {item.matches[0]?.text}
                </span>
              ))}
          </div>
        )}
      </CardBody>
    </Card>
  )
}

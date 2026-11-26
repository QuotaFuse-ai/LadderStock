import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Scale } from 'lucide-react'

import {
  fetchLedger,
  runRecall,
  type LedgerPayload,
  type LedgerRecord,
  type RecallResult,
} from '../api/conversation'
import { PageHeader } from '../components/PageShell'
import { Button, Card, CardBody, CardHeader } from '../components/ui'

/**
 * 观点账本（`/ledger`，F9）。
 *
 * 这一页回答的是别处都不回答的那个问题：**「之前那些判断，后来对了吗」**。
 * 判断在问答落账时写入（方向 + 基准价 + 翻转条件 + 期限），到期由回访拉日线判定，
 * 本页只读地展示结果——不重算、不四舍五入美化，也不把没到期的算进命中率。
 *
 * 数字全部来自 `GET /api/conversation/ledger`；「跑一次回访」调 `POST /recall`，
 * 返回的是这一轮实际判了多少条，不是承诺。
 */

const STATUS_META: Record<string, { label: string; tone: string }> = {
  open: { label: '待判', tone: 'text-text-muted' },
  hit: { label: '应验', tone: 'text-emerald-600' },
  miss: { label: '未应验', tone: 'text-rose-600' },
  flipped: { label: '已翻转', tone: 'text-amber-600' },
  undecided: { label: '判不了', tone: 'text-text-muted' },
}

const DIRECTION_LABEL: Record<string, string> = {
  bullish: '偏多',
  bearish: '偏空',
  neutral: '中性',
}

function statusMeta(status: string) {
  return STATUS_META[status] ?? { label: status, tone: 'text-text-muted' }
}

function directionLabel(direction: string): string {
  return DIRECTION_LABEL[direction] ?? direction
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${(value * 100).toFixed(2)}%`
}

function rateText(rate: number | null): string {
  return rate === null ? '—' : `${(rate * 100).toFixed(1)}%`
}

function RecordRow({ record }: { record: LedgerRecord }) {
  const meta = statusMeta(record.status)
  const reason = record.outcome?.reason ?? ''
  return (
    <div className="grid grid-cols-12 gap-3 border-b border-border py-3 text-body last:border-b-0">
      <div className="col-span-3 flex flex-col gap-1">
        <span className="font-medium text-text">{record.question || '（无问题原文）'}</span>
        <span className="text-caption text-text-muted">
          {record.symbol ? `${record.symbol} · ` : ''}
          {directionLabel(record.direction)}
          {record.base_price !== null ? ` · 基准 ${record.base_price}（${record.base_date ?? '日期缺'}）` : ''}
        </span>
      </div>
      <div className="col-span-3 flex flex-col gap-1">
        <span className="text-caption text-text-muted">翻转条件</span>
        <span className={record.falsifier ? 'text-text' : 'text-text-muted'}>
          {record.falsifier ?? '没写（到期只看方向）'}
        </span>
      </div>
      <div className="col-span-2 flex flex-col gap-1">
        <span className="text-caption text-text-muted">
          {record.horizon_days} 天 · 到期 {record.due_date ?? '—'}
        </span>
        <span className={meta.tone}>{meta.label}</span>
      </div>
      <div className="col-span-4 flex flex-col gap-1">
        <span className="text-caption text-text-muted">判定</span>
        <span className="text-text">{reason || '还没判'}</span>
        {record.outcome?.pct_change !== null && record.outcome?.pct_change !== undefined ? (
          <span className="text-caption text-text-muted">区间涨跌 {pct(record.outcome.pct_change)}</span>
        ) : null}
      </div>
    </div>
  )
}

export default function LedgerScreen() {
  const [data, setData] = useState<LedgerPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [recallText, setRecallText] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const payload = await fetchLedger({ limit: 100 })
      setData(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const refresh = async () => {
    setRunning(true)
    setRecallText(null)
    try {
      const result: RecallResult = await runRecall()
      if (!result.ok) {
        setRecallText(result.error ? `回访没跑成：${result.error}` : '回访没跑成')
      } else {
        setRecallText(
          `回访 ${result.as_of ?? ''}：到期 ${result.due ?? 0} 条，判了应验 ${result.hit ?? 0}、` +
            `未应验 ${result.miss ?? 0}、翻转 ${result.flipped ?? 0}、判不了 ${result.undecided ?? 0}；` +
            `缺行情留待下次 ${result.skipped ?? 0} 条`
        )
      }
      await load()
    } catch (err) {
      setRecallText(err instanceof Error ? err.message : String(err))
    } finally {
      setRunning(false)
    }
  }

  const stats = data?.stats
  const records = data?.records ?? []

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="观点账本"
        subtitle="每次给出方向判断就落一笔，到期拉日线回访：命中率只算已经判出来的"
        badge={<Scale className="size-4" aria-hidden="true" />}
      />

      <Card>
        <CardHeader
          title="命中率"
          subtitle="分母是已判条数（应验 / 未应验 / 已翻转），没到期的不进分母"
          actions={
            <Button
              variant="secondary"
              size="md"
              disabled={running}
              leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
              onClick={() => void refresh()}
            >
              {running ? '回访中…' : '跑一次回访'}
            </Button>
          }
        />
        <CardBody className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <div className="rounded-lg border border-border px-3 py-2">
              <div className="text-caption text-text-muted">命中率</div>
              <div className="text-title font-semibold text-text">{rateText(stats?.hit_rate ?? null)}</div>
            </div>
            <div className="rounded-lg border border-border px-3 py-2">
              <div className="text-caption text-text-muted">已判</div>
              <div className="text-title font-semibold text-text">{stats?.judged ?? 0}</div>
            </div>
            <div className="rounded-lg border border-border px-3 py-2">
              <div className="text-caption text-text-muted">应验 / 未应验</div>
              <div className="text-title font-semibold text-text">
                {stats?.hit ?? 0} / {stats?.miss ?? 0}
              </div>
            </div>
            <div className="rounded-lg border border-border px-3 py-2">
              <div className="text-caption text-text-muted">已翻转</div>
              <div className="text-title font-semibold text-text">{stats?.flipped ?? 0}</div>
            </div>
            <div className="rounded-lg border border-border px-3 py-2">
              <div className="text-caption text-text-muted">待判</div>
              <div className="text-title font-semibold text-text">{stats?.open ?? 0}</div>
            </div>
          </div>

          {recallText ? <p className="text-caption text-text-muted">{recallText}</p> : null}
          {error ? <p className="text-caption text-rose-600">{error}</p> : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="记录"
          subtitle="落账条件是「有明确方向 + 指标卡里有一个价格」；中性回答不记，没基准价不记"
        />
        <CardBody className="flex flex-col gap-1">
          {loading ? <p className="text-caption text-text-muted">读取中…</p> : null}
          {!loading && records.length === 0 ? (
            <p className="text-caption text-text-muted">
              还没有记录。去首页问一个带方向的判断（例如「600519 一个月内偏多还是偏空，跌破多少算错」），
              这一轮答完就会自动落一笔。
            </p>
          ) : null}
          {records.map((record) => (
            <RecordRow key={record.record_id} record={record} />
          ))}
        </CardBody>
      </Card>

      <p className="text-caption text-text-muted">
        数据在本机 <code>data/chat.sqlite</code> 的 <code>research_record</code> 表；判定只比方向，
        幅度仅作参考（<code>pct_change</code> 不参与命中标准）。翻转条件先于方向：它一触发，这条就不算对。
      </p>
    </div>
  )
}

import { useRef, useState } from 'react'

import { describeApiError, isApiError } from '../../api'
import {
  fetchVerifyCapabilities,
  fetchVerifyHistory,
  verifyFile,
  type VerifyCapabilities,
  type VerifyHistoryItem,
  type VerifyResult,
  type VerifyRow,
} from '../../api/verify'
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  ErrorBlock,
  SectionCard,
} from '../ui'
import { cn } from '../../lib/cn'
import { formatInboxTime } from '../../lib/format'
import { useAsync } from '../../lib/useAsync'

/**
 * F10 截图 / PDF 查证：上传 → 抽取 → 关键数字 → 与本机数据对账。
 *
 * 三条口径（后端契约的镜像）：
 *
 * - **能力先说清楚**：PDF 文本层 / 图片 vision / 本地 OCR 三项各自可用与否 + 原因，
 *   不可用就别让用户白传（本机实测无 tesseract，图片路径必须配 vision）；
 * - **识别失败不算成功**：`ok=false` 时把 `extract.reason` 原样显示，不画数字表格；
 * - **对账逐项可数**：每个字段给「图上 / 本地 / 判定」，`unverifiable` 写清本地缺什么。
 */

const VERDICT_TONE: Record<string, 'ok' | 'danger' | 'neutral'> = {
  match: 'ok',
  mismatch: 'danger',
  unverifiable: 'neutral',
}

const VERDICT_LABEL: Record<string, string> = {
  match: '一致',
  mismatch: '不一致',
  unverifiable: '无法核对',
}

const STATUS_LABEL: Record<string, string> = {
  ok: '已识别',
  needs_ocr: '需要 OCR',
  ocr_unavailable: '无 OCR 能力',
  unsupported: '不支持的类型',
  failed: '抽取失败',
}

function errorText(error: unknown): string {
  const info = describeApiError(error)
  return info.code ? `${info.title}：${info.detail}（${info.code}）` : `${info.title}：${info.detail}`
}

function formatValue(value: number | string | null, unit = ''): string {
  if (value === null || value === undefined || value === '') return '—'
  const text = typeof value === 'number' ? String(Number(value.toFixed(4))) : value
  return unit ? `${text}${unit}` : text
}

function CapabilityLine({ label, ready, reason }: { label: string; ready: boolean; reason: string }) {
  return (
    <li className="flex flex-wrap items-baseline gap-2">
      <Badge tone={ready ? 'ok' : 'neutral'}>{ready ? '可用' : '不可用'}</Badge>
      <span className="text-body text-text">{label}</span>
      {!ready && reason && <span className="text-caption text-text-muted">{reason}</span>}
    </li>
  )
}

function ReconcileTable({ rows }: { rows: VerifyRow[] }) {
  if (rows.length === 0) {
    return <p className="text-body text-text-muted">没有可对账的字段（图上没抽到关键数字）。</p>
  }
  return (
    <ul className="flex flex-col gap-2" aria-label="对账明细">
      {rows.map((row) => (
        <li
          key={`${row.field}-${row.label}`}
          className={cn(
            'flex flex-wrap items-center gap-x-3 gap-y-1 rounded border px-3 py-2',
            row.verdict === 'mismatch' ? 'border-danger' : 'border-border',
          )}
        >
          <span className="min-w-0 flex-1 text-body text-text">{row.label}</span>
          <span className="ldst-num text-caption text-text-secondary">
            图上 {formatValue(row.doc_value, row.doc_unit)}
          </span>
          <span className="ldst-num text-caption text-text-secondary">
            本地 {formatValue(row.local_value)}
            {row.local_value !== null && row.local_date ? `（${row.local_date}）` : ''}
          </span>
          <Badge tone={VERDICT_TONE[row.verdict] ?? 'neutral'}>
            {VERDICT_LABEL[row.verdict] ?? row.verdict}
          </Badge>
          <span className="w-full text-caption text-text-muted">
            来源：{row.local_source || '未标注'}
            {row.note ? ` · ${row.note}` : ''}
          </span>
        </li>
      ))}
    </ul>
  )
}

function ResultBlock({ result }: { result: VerifyResult }) {
  const accuracy = result.reconciled.accuracy
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-body font-medium text-text">{result.filename}</span>
        <Badge tone={result.ok ? 'ok' : 'warn'}>
          {STATUS_LABEL[result.extract.status] ?? result.extract.status}
        </Badge>
        <span className="ldst-num text-caption text-text-muted">
          {result.bytes} 字节 · {result.reconciled.code ? `标的 ${result.reconciled.code}` : '未识别代码'}
        </span>
        {accuracy !== null && (
          <span className="ldst-num text-caption text-text-secondary">
            正确率 {(accuracy * 100).toFixed(1)}%（{result.reconciled.matched}/
            {result.reconciled.comparable}）
          </span>
        )}
      </div>

      {result.extract.reason && (
        <p className="text-caption leading-body text-text-secondary">{result.extract.reason}</p>
      )}

      {result.extract.status === 'ok' ? (
        <>
          <div className="flex flex-wrap gap-2 text-caption text-text-secondary">
            {result.dates.length > 0 && <span>图上日期：{result.dates.slice(0, 4).join('、')}</span>}
            {result.extract.pages ? <span>页数 {result.extract.pages}</span> : null}
            {result.figures.length > 0 && <span>抽出 {result.figures.length} 个关键数字</span>}
          </div>
          <ReconcileTable rows={result.reconciled.rows} />
          <p className="text-caption leading-body text-text-muted">
            正确率口径：{result.reconciled.accuracy_formula}。本地缺这个域就记「无法核对」，不拿别的字段凑。
          </p>
        </>
      ) : (
        <p className="text-body text-text-muted">
          这份文件没有可用的文本层，所以没有抽数字、也没有对账结论。
        </p>
      )}

      {result.saved && <p className="text-caption text-text-muted">已留档：{result.saved}</p>}
      {result.save_error && (
        <p className="text-caption text-danger">落档失败：{result.save_error}</p>
      )}
    </div>
  )
}

export default function VerifyPanel() {
  const caps = useAsync<VerifyCapabilities>((signal) => fetchVerifyCapabilities(signal), [])
  const history = useAsync<VerifyHistoryItem[]>((signal) => fetchVerifyHistory(8, signal), [])

  const [file, setFile] = useState<File | null>(null)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<VerifyResult | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const capabilities = caps.data

  const submit = async () => {
    if (!file) {
      setError('先选一份 PDF 或截图。')
      return
    }
    setBusy(true)
    try {
      const payload = await verifyFile(file, { code: code.trim() || undefined })
      setResult(payload)
      setError(null)
      history.refresh()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <SectionCard
      title="截图 / PDF 查证"
      subtitle="上传研报截图或公告 PDF，抽出关键数字并与本机数据逐项对账"
      tone={error ? 'warn' : 'neutral'}
    >
      {caps.status === 'loading' && (
        <p className="text-body text-text-muted" aria-busy="true">
          正在探测本机能力…
        </p>
      )}

      {caps.status === 'error' &&
        (isApiError(caps.error) && caps.error.kind === 'not-connected' ? (
          <Banner tone="info" title="本机接口未连上">
            <p>后端没在跑或端口不对：启动本机服务后重试。</p>
            <Button variant="secondary" onClick={() => caps.reload()}>
              重试
            </Button>
          </Banner>
        ) : (
          <ErrorBlock
            variant="banner"
            title="能力探测失败"
            detail={errorText(caps.error)}
            onRetry={() => caps.reload()}
            retryLabel="重试"
          />
        ))}

      {caps.status === 'ready' && capabilities && (
        <div className="flex flex-col gap-4">
          <ul className="flex flex-col gap-2" aria-label="本机查证能力">
            <CapabilityLine label="PDF 文本层（pypdf）" ready={capabilities.pdf_text} reason={capabilities.pdf_reason} />
            <CapabilityLine label="图片识别（vision 后端）" ready={capabilities.image_vision} reason={capabilities.image_reason} />
            <CapabilityLine label="本地 OCR" ready={capabilities.local_ocr} reason={capabilities.local_ocr_reason} />
          </ul>

          {error && (
            <Banner
              tone="warn"
              title="这次没有查成"
              action={
                <Button variant="ghost" onClick={() => setError(null)}>
                  知道了
                </Button>
              }
            >
              {error}
            </Banner>
          )}

          <div className="flex flex-wrap items-end gap-3 border-t border-border pt-3">
            <div className="flex flex-col gap-1">
              <span className="text-caption text-text-secondary">文件（PDF / PNG / JPEG / WEBP / BMP）</span>
              <input
                ref={inputRef}
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.webp,.bmp"
                className="text-caption"
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null)
                  setResult(null)
                  setError(null)
                }}
              />
            </div>
            <label className="flex flex-col gap-1">
              <span className="text-caption text-text-secondary">标的代码（可选）</span>
              <input
                value={code}
                onChange={(event) => setCode(event.target.value)}
                placeholder="600519"
                inputMode="numeric"
                className="h-8 w-28 rounded border border-border bg-surface px-2 text-caption text-text"
              />
            </label>
            <Button variant="primary" loading={busy} onClick={() => void submit()}>
              上传并对账
            </Button>
            {file && (
              <span className="text-caption text-text-muted">
                已选 {file.name}（{Math.round(file.size / 1024)} KB）
              </span>
            )}
          </div>

          {result ? (
            <ResultBlock result={result} />
          ) : (
            <EmptyState
              compact
              title="还没有查证结果"
              description="选一份文件上传，就会给出「图上 / 本地 / 判定」三列；没有文本层的图片会在这里说明缺什么。"
            />
          )}

          {history.status === 'ready' && (history.data?.length ?? 0) > 0 && (
            <div className="border-t border-border pt-3">
              <p className="mb-2 text-caption text-text-secondary">最近的查证</p>
              <ul className="flex flex-col gap-1" aria-label="查证历史">
                {(history.data ?? []).map((item) => (
                  <li key={item.path} className="flex flex-wrap items-center gap-2 text-caption">
                    <span className="ldst-num text-text-muted">
                      {item.generated_at ? formatInboxTime(item.generated_at) : '—'}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-text">{item.filename}</span>
                    <span className="text-text-secondary">{STATUS_LABEL[item.status] ?? item.status}</span>
                    <span className="ldst-num text-text-muted">
                      {item.figures} 项
                      {item.accuracy !== null ? ` · ${(item.accuracy * 100).toFixed(0)}%` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </SectionCard>
  )
}

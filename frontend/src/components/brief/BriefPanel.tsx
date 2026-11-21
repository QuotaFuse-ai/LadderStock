import { useState } from 'react'
import { Link } from 'react-router-dom'
import { FileText, RefreshCw, Send } from 'lucide-react'

import { describeApiError, isApiError } from '../../api'
import {
  fetchBriefToday,
  runBrief,
  updateBriefConfig,
  type BriefItem,
  type BriefPayload,
  type BriefView,
} from '../../api/brief'
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
 * 今日简报（F11）：晨报 / 收盘复盘 / 持仓异动。
 *
 * 三条设计口径，都是后端契约的镜像：
 *
 * - **每条都带出处**：`source` 字段必须显示，读者要能顺着它回查（`daily_k` / `watch/digest` /
 *   `news_candidates/<day>.json`）。
 * - **只显示真实条目**：后端 `skipped`（周末、无持仓、无异动）就显示 skipped 与原因，
 *   不画一条"暂无数据"的假条目。
 * - **按钮会写库**：`生成并落提醒` 走 `POST /api/brief/{kind}/run`，服务端按内容指纹去重，
 *   重复点不会重复推送；点完刷新收件箱。
 */

const STATUS_TONE: Record<string, 'ok' | 'warn' | 'danger' | 'neutral' | 'info'> = {
  ok: 'ok',
  degraded: 'warn',
  failed: 'danger',
  skipped: 'neutral',
}

const STATUS_LABEL: Record<string, string> = {
  ok: '正常',
  degraded: '降级',
  failed: '失败',
  skipped: '跳过',
}

const PREVIEW_LINES = 4

function errorText(error: unknown): string {
  const info = describeApiError(error)
  return info.code ? `${info.title}：${info.detail}（${info.code}）` : `${info.title}：${info.detail}`
}

function internalLink(link: string | null): string | null {
  if (!link) return null
  return link.startsWith('/') && !link.startsWith('//') ? link : null
}

function ItemBlock({ item }: { item: BriefItem }) {
  const link = internalLink(item.link)
  const rest = item.lines.length - PREVIEW_LINES
  return (
    <li className="rounded border border-border px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="min-w-0 flex-1 text-body font-medium text-text">{item.title}</span>
        {link && (
          <Link to={link} className="text-caption text-accent hover:underline">
            打开
          </Link>
        )}
        <span className="w-full text-caption text-text-muted">出处：{item.source || '未标注'}</span>
      </div>
      {item.lines.length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5 text-caption leading-body text-text-secondary">
          {item.lines.slice(0, PREVIEW_LINES).map((line, index) => (
            <li key={`${item.title}-${index}`} className="ldst-num">
              {line}
            </li>
          ))}
          {rest > 0 && <li className="text-text-muted">另有 {rest} 行（点「打开」看全文）</li>}
        </ul>
      )}
    </li>
  )
}

function BriefBlock({
  view,
  busy,
  onRun,
}: {
  view: BriefView
  busy: boolean
  onRun: (kind: string) => void
}) {
  const tone = STATUS_TONE[view.status] ?? 'neutral'
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-body font-medium text-text">{view.label}</span>
        <Badge tone={tone}>{STATUS_LABEL[view.status] ?? view.status}</Badge>
        <span className="ldst-num text-caption text-text-muted">
          {view.items.length} 条 · {view.generated_at ? formatInboxTime(view.generated_at) : '未生成'}
        </span>
        <Button
          variant="ghost"
          className="ml-auto h-7 px-2 text-caption"
          loading={busy}
          leadingIcon={<RefreshCw className="size-3" aria-hidden="true" />}
          onClick={() => onRun(view.kind)}
        >
          生成并落提醒
        </Button>
      </div>
      <p className="text-caption leading-body text-text-secondary">{view.detail}</p>
      {view.items.length === 0 ? (
        <p className="text-caption text-text-muted">这一份没有条目（状态：{view.status}）。</p>
      ) : (
        <ul className="flex flex-col gap-2" aria-label={`${view.label}条目`}>
          {view.items.map((item) => (
            <ItemBlock key={`${view.kind}-${item.fingerprint || item.title}`} item={item} />
          ))}
        </ul>
      )}
    </div>
  )
}

export default function BriefPanel({ onPublished }: { onPublished?: () => void } = {}) {
  const view = useAsync<BriefPayload>((signal) => fetchBriefToday(signal), [])
  const payload = view.data
  const [busy, setBusy] = useState<string | null>(null)
  const [actError, setActError] = useState<string | null>(null)
  const [savingSwitch, setSavingSwitch] = useState(false)

  const reload = () => {
    setActError(null)
    view.reload()
  }

  const run = async (kind: string) => {
    setBusy(kind)
    try {
      const result = await runBrief(kind)
      setActError(null)
      view.refresh()
      onPublished?.()
      if (result.pushed.length === 0) {
        // 全都推过：如实说"没重发"，不假装成功
        setActError(`${result.title}：${result.skipped.length} 条今日已推送过，未重发`)
      }
    } catch (error) {
      setActError(errorText(error))
    } finally {
      setBusy(null)
    }
  }

  const toggleNotify = async (next: boolean) => {
    setSavingSwitch(true)
    try {
      await updateBriefConfig({ notify: next })
      setActError(null)
      view.refresh()
    } catch (error) {
      setActError(errorText(error))
    } finally {
      setSavingSwitch(false)
    }
  }

  const changeChannel = async (next: string) => {
    setSavingSwitch(true)
    try {
      await updateBriefConfig({ channel: next })
      setActError(null)
      view.refresh()
    } catch (error) {
      setActError(errorText(error))
    } finally {
      setSavingSwitch(false)
    }
  }

  const header = (
    <SectionCard
      title="今日简报"
      subtitle={
        payload
          ? `${payload.day || '日期未知'} · 已推送 ${payload.published.length} 条 · 阈值 ±${
              payload.settings.threshold
            }%`
          : '晨报 / 收盘复盘 / 持仓异动'
      }
      actions={
        <span className="flex items-center gap-2">
          <Send className="size-4 text-text-muted" aria-hidden="true" />
          <label className="flex items-center gap-1 text-caption text-text-secondary">
            <input
              type="checkbox"
              className="size-3.5"
              checked={payload?.settings.notify ?? false}
              disabled={!payload || savingSwitch}
              onChange={(event) => void toggleNotify(event.target.checked)}
            />
            同时投递本机通知
          </label>
        </span>
      }
      tone={actError ? 'warn' : 'neutral'}
    >
      {view.status === 'loading' && (
        <p className="text-body text-text-muted" aria-busy="true">
          正在生成今天的简报…
        </p>
      )}

      {view.status === 'error' &&
        (isApiError(view.error) && view.error.kind === 'not-connected' ? (
          <Banner tone="info" title="本机接口未连上">
            <p>后端没在跑或端口不对：先启动本机服务，再重新读取简报。</p>
            <Button variant="secondary" onClick={reload}>
              重新读取简报
            </Button>
          </Banner>
        ) : (
          <ErrorBlock
            variant="banner"
            title="简报读取失败"
            detail={errorText(view.error)}
            onRetry={reload}
            retryLabel="重试"
          />
        ))}

      {view.status === 'ready' && payload && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2 text-caption text-text-secondary">
            <FileText className="size-3.5" aria-hidden="true" />
            <span>投递渠道</span>
            {['console', 'desktop'].map((channel) => (
              <button
                key={channel}
                type="button"
                disabled={savingSwitch}
                onClick={() => void changeChannel(channel)}
                className={cn(
                  'rounded border px-2 py-0.5 text-caption',
                  payload.settings.channel === channel
                    ? 'border-accent text-accent'
                    : 'border-border text-text-secondary hover:border-accent',
                )}
              >
                {channel}
              </button>
            ))}
            <span className="text-text-muted">
              开关只影响"要不要弹窗"，条目总是先落收件箱（左侧列表）。
            </span>
          </div>

          {payload.briefs.length === 0 ? (
            <EmptyState
              compact
              icon={<FileText aria-hidden="true" />}
              title="还没有简报"
              description="点「生成并落提醒」先跑一份；定时任务在 09:10 / 15:45 / 15:50 也会跑。"
            />
          ) : (
            payload.briefs.map((brief, index) => (
              <div
                key={brief.kind}
                className={cn(index > 0 && 'border-t border-border pt-3')}
              >
                <BriefBlock view={brief} busy={busy === brief.kind} onRun={(kind) => void run(kind)} />
              </div>
            ))
          )}
        </div>
      )}
    </SectionCard>
  )

  return (
    <div className="flex flex-col gap-3">
      {actError && (
        <Banner
          tone="warn"
          title="提示"
          action={
            <Button variant="ghost" onClick={() => setActError(null)}>
              知道了
            </Button>
          }
        >
          {actError}
        </Banner>
      )}
      {header}
    </div>
  )
}

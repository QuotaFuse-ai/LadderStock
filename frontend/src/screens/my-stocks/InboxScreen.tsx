import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Bell,
  CheckCheck,
  Clock,
  Monitor,
  Send,
  Terminal,
} from 'lucide-react'

import { describeApiError, isApiError } from '../../api'
import {
  fetchInbox,
  markAllInboxRead,
  markInboxRead,
  type InboxItem,
} from '../../api/inbox'
import { OfflineBanner, PageHeader } from '../../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  ErrorBlock,
  SectionCard,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { MISSING, countOrMissing, formatInboxTime } from '../../lib/format'
import { useAsync } from '../../lib/useAsync'

/**
 * 提醒（`/inbox`）：本机收件箱 + 定时任务清单 + 本机投递渠道。
 *
 * 复刻参考库 `pages/Inbox.tsx:139-178`（收件箱列表）与 `:181-213`（定时任务 / 渠道），
 * 但只做本仓后端 `GET /api/inbox` 真正给得出的部分（QUOT-432 / DEC-058）：
 *
 * - 收件箱按时间倒序，未读左侧圆点；有站内 `link` 才给「打开」，外链只显示不跳转；
 * - 定时任务只读（后端没有启停接口），不摆开关；
 * - 渠道只有 `console` / `desktop` 两种本机投递，不出现第三方推送的空 UI；
 * - 空态 / 错误态都给下一步（重试），不静默空白、不画假条目。
 *
 * 时间口径与参考 `formatInboxTime` 一致：今天 `HH:mm`，更早 `MM-DD HH:mm`。
 */

const CHANNELS = [
  {
    key: 'console',
    label: '终端（console）',
    icon: Terminal,
    detail: '打印到运行 ldst 的那个终端；不落文件、不发网络请求。',
  },
  {
    key: 'desktop',
    label: '桌面通知（desktop）',
    icon: Monitor,
    detail: '走本机系统通知（Windows 用 winotify，其它平台退回 plyer）；只在程序运行时生效。',
  },
] as const

/** 只认站内绝对路径：`//host` 与协议开头的外链一律不当跳转目标。 */
function internalLink(link: string | null): string | null {
  if (!link) return null
  return link.startsWith('/') && !link.startsWith('//') ? link : null
}

function errorText(error: unknown): string {
  const info = describeApiError(error)
  return info.code ? `${info.title}：${info.detail}（${info.code}）` : `${info.title}：${info.detail}`
}

function ItemRow({
  item,
  busy,
  onRead,
}: {
  item: InboxItem
  busy: boolean
  onRead: (id: string) => void
}) {
  const link = internalLink(item.link)
  const taskName = item.job_label || item.job || MISSING
  return (
    <li
      className={cn(
        'rounded border p-3',
        item.is_read ? 'border-border' : 'border-accent bg-accent-soft',
      )}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {!item.is_read && (
          <span aria-label="未读" className="size-2 shrink-0 rounded-full bg-accent" />
        )}
        <span className="text-caption text-text-secondary">{taskName}</span>
        <span className="min-w-0 flex-1 truncate text-body font-medium text-text">{item.title}</span>
        <time className="ldst-num shrink-0 text-caption text-text-muted" dateTime={item.created_at}>
          {formatInboxTime(item.created_at)}
        </time>
        <span className="flex shrink-0 items-center gap-2">
          {link && (
            <Link
              to={link}
              onClick={() => {
                if (!item.is_read) onRead(item.id)
              }}
              className="text-caption text-accent hover:underline"
            >
              打开
            </Link>
          )}
          {!link && item.link && (
            <span className="text-caption text-text-muted" title={item.link}>
              外链不在页面打开
            </span>
          )}
          {!item.is_read && (
            <Button
              variant="ghost"
              className="h-6 px-2 text-caption"
              loading={busy}
              onClick={() => onRead(item.id)}
            >
              标为已读
            </Button>
          )}
        </span>
      </div>
      {item.lines.length > 0 && (
        <ul className="mt-2 flex flex-col gap-0.5 text-caption leading-body text-text-secondary">
          {item.lines.map((line, index) => (
            <li key={`${item.id}-${index}`} className="ldst-num">
              {line}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

export default function InboxScreen() {
  const view = useAsync((signal) => fetchInbox(signal), [])
  const payload = view.data
  const items = payload?.items ?? []
  // 未读总数以后端 COUNT 为准；载荷缺这个键时退回本页已加载条目的未读数，不补 0
  const unread = payload?.unread ?? (payload ? items.filter((item) => !item.is_read).length : null)
  const hasUnread = (unread ?? 0) > 0

  const [busy, setBusy] = useState<string | null>(null)
  const [actError, setActError] = useState<string | null>(null)

  const reload = () => {
    setActError(null)
    view.reload()
  }

  const readOne = async (id: string) => {
    setBusy(`read:${id}`)
    try {
      await markInboxRead(id)
      setActError(null)
      view.refresh()
    } catch (error) {
      setActError(errorText(error))
    } finally {
      setBusy(null)
    }
  }

  const readAll = async () => {
    setBusy('all')
    try {
      await markAllInboxRead()
      setActError(null)
      view.refresh()
    } catch (error) {
      setActError(errorText(error))
    } finally {
      setBusy(null)
    }
  }

  const header = (
    <PageHeader
      title="提醒"
      subtitle={
        payload
          ? `最近 ${items.length} 条 · 未读 ${countOrMissing(unread)}`
          : '本机提醒收件箱'
      }
      badge={
        payload ? (
          <Badge tone={hasUnread ? 'info' : 'neutral'}>
            {hasUnread ? `${countOrMissing(unread)} 条未读` : '全部已读'}
          </Badge>
        ) : undefined
      }
      refresh={{ onClick: reload, disabled: view.status === 'loading' }}
      actions={
        payload ? (
          <Button
            variant="secondary"
            leadingIcon={<CheckCheck className="size-3.5" aria-hidden="true" />}
            disabled={!hasUnread || busy !== null}
            loading={busy === 'all'}
            onClick={() => void readAll()}
          >
            全部已读
          </Button>
        ) : undefined
      }
    />
  )

  const note = (
    <Banner tone="info" title="只在本机运行与投递">
      <p className="max-w-measure">
        提醒由本机定时任务生成，只投递到 console / desktop 两种本机渠道：不接第三方推送、不联网；
        电脑关机或程序没开时不会运行。消息正文只列事实，不给买卖建议。
      </p>
    </Banner>
  )

  return (
    <div className="flex flex-col gap-5">
      {header}
      {note}

      {actError && (
        <Banner
          tone="danger"
          title="操作没有成功"
          action={
            <Button variant="ghost" onClick={() => setActError(null)}>
              知道了
            </Button>
          }
        >
          {actError}
        </Banner>
      )}

      {view.status === 'loading' && (
        <p className="text-body text-text-muted" aria-busy="true">
          正在读取提醒…
        </p>
      )}

      {view.status === 'error' &&
        (isApiError(view.error) && view.error.kind === 'not-connected' ? (
          <OfflineBanner onRetry={reload} />
        ) : (
          <ErrorBlock
            variant="banner"
            title="提醒读取失败"
            detail={errorText(view.error)}
            onRetry={reload}
            retryLabel="重试"
          />
        ))}

      {view.status === 'ready' && payload && (
        <div className="grid grid-cols-1 gap-4 desk:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <SectionCard
            title={`收件箱${hasUnread ? ` · ${countOrMissing(unread)} 条未读` : ''}`}
            subtitle="按时间倒序；正文是任务生成的事实行"
            tone={actError ? 'warn' : 'neutral'}
          >
            {items.length === 0 ? (
              <EmptyState
                compact
                icon={<Bell aria-hidden="true" />}
                title="还没有提醒"
                description="定时任务跑过之后会出现在这里；到点运行一次就会落一条。"
              />
            ) : (
              <ul className="flex flex-col gap-2" aria-label="提醒列表">
                {items.map((item) => (
                  <ItemRow
                    key={item.id}
                    item={item}
                    busy={busy === `read:${item.id}`}
                    onRead={(id) => void readOne(id)}
                  />
                ))}
              </ul>
            )}
          </SectionCard>

          <div className="flex flex-col gap-4">
            <SectionCard
              title="定时任务"
              subtitle="只列会产生提醒的任务"
              actions={<Clock className="size-4 text-text-muted" aria-hidden="true" />}
            >
              {payload.jobs.length === 0 ? (
                <p className="text-body text-text-muted">没有会产生提醒的定时任务。</p>
              ) : (
                <ul className="flex flex-col gap-2" aria-label="定时任务列表">
                  {payload.jobs.map((job) => (
                    <li
                      key={job.id}
                      className="flex flex-wrap items-center gap-2 rounded border border-border px-3 py-2"
                    >
                      <span className="min-w-0 flex-1 truncate text-body text-text">{job.label}</span>
                      <span className="ldst-num text-caption text-text-secondary">
                        {job.trigger || MISSING}
                      </span>
                      <Badge tone={job.enabled ? 'ok' : 'neutral'}>
                        {job.enabled ? '已启用' : '已停用'}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-2 text-caption leading-body text-text-muted">
                只读：启停与运行时刻在 config/schedule.yaml 里改，本页不提供开关。
              </p>
            </SectionCard>

            <SectionCard
              title="推送渠道"
              subtitle="只投递到这两种本机渠道"
              actions={<Send className="size-4 text-text-muted" aria-hidden="true" />}
            >
              <ul className="flex flex-col gap-2" aria-label="推送渠道列表">
                {CHANNELS.map((channel) => (
                  <li
                    key={channel.key}
                    className="flex items-start gap-2 rounded border border-border px-3 py-2"
                  >
                    <channel.icon
                      className="mt-half size-4 shrink-0 text-text-muted"
                      strokeWidth={1.75}
                      aria-hidden="true"
                    />
                    <div className="min-w-0">
                      <p className="text-body text-text">{channel.label}</p>
                      <p className="text-caption leading-body text-text-secondary">{channel.detail}</p>
                    </div>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-caption leading-body text-text-muted">
                不开渠道也不影响收件箱：消息照样会出现在左侧，渠道只是额外的本机提醒。
              </p>
            </SectionCard>
          </div>
        </div>
      )}

      <p className="text-caption leading-body text-text-muted">
        收件箱只走 127.0.0.1 回环读写，响应不进缓存；不上传、不进 AI 上下文，也不写浏览器存储。
      </p>
    </div>
  )
}

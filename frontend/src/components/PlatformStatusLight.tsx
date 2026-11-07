import { useState } from 'react'
import { RotateCw } from 'lucide-react'

import { Button } from './ui/Button'
import { cn } from '../lib/cn'
import {
  formatUptime,
  lightLabel,
  usePlatformHealth,
  type PlatformHealthApi,
  type PlatformLight,
} from '../lib/usePlatformHealth'

/**
 * 顶栏工具区最左的服务状态灯（QUOT-443 / DEC-063）。
 *
 * - 三态圆点：绿 = `ok`、黄 = `degraded`（含 `db_ok=false`）、红 = `offline`；
 *   首拍返回前是灰色「探测中」，不渲染重启入口。
 * - hover / 聚焦 / 点击都能看到浮层：运行时长、版本、数据库连通。
 * - **只有红灯**才渲染「重启平台」；绿 / 黄不占位（避免误点，也避免把重启当日常操作）。
 * - 重启是两步：点「重启平台」→ 浮层里再点「确认重启」。
 * - 浮层与按钮只展示健康字段，**不含任何持仓 / 金额 / 日志内容**。
 */

const DOT_CLASS: Record<PlatformLight, string> = {
  ok: 'bg-ok',
  degraded: 'bg-warn',
  offline: 'bg-danger',
  unknown: 'bg-text-muted',
}

const TEXT_CLASS: Record<PlatformLight, string> = {
  ok: 'text-ok',
  degraded: 'text-warn',
  offline: 'text-danger',
  unknown: 'text-text-muted',
}

export interface PlatformStatusLightViewProps {
  api: PlatformHealthApi
}

/** 纯展示层：单测直接喂一个假的 `PlatformHealthApi`，不启动轮询。 */
export function PlatformStatusLightView({ api }: PlatformStatusLightViewProps) {
  const { light, restarting, restartError, restart } = api
  const [open, setOpen] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const uptime = api.health?.api?.uptime_sec ?? api.health?.uptime_sec
  const version = api.health?.api?.version ?? api.health?.version
  const db = api.health?.db_ok === true ? '可读' : api.health?.db_ok === false ? '不可读' : '未知'
  const label = lightLabel(light)

  const askRestart = () => {
    setOpen(true)
    setConfirming(true)
  }

  return (
    <div className="relative flex shrink-0 items-center gap-1.5">
      <button
        type="button"
        aria-label={`平台状态：${label}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={`平台状态：${label}`}
        onClick={() => setOpen((value) => !value)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => {
          if (!confirming) setOpen(false)
        }}
        className="flex items-center rounded p-1.5 transition-colors duration-fast hover:bg-surface-3"
      >
        <span
          aria-hidden="true"
          data-status={light}
          className={cn(
            'size-2.5 rounded-full',
            DOT_CLASS[light],
            light === 'offline' && 'animate-pulse',
          )}
        />
      </button>

      {/* 浮层：鼠标悬停 / 点击打开；纯展示，不参与布局 */}
      {open && (
        <div
          role="dialog"
          aria-label="平台状态详情"
          className="absolute left-0 top-full z-50 mt-1 w-60 rounded border border-border bg-surface p-2.5 text-caption text-text-secondary shadow-card"
        >
          <dl className="grid grid-cols-[4.5rem_1fr] gap-x-2 gap-y-1">
            <dt className="text-text-muted">状态</dt>
            <dd className={cn('font-medium', TEXT_CLASS[light])}>{label}</dd>
            <dt className="text-text-muted">已运行</dt>
            <dd className="ldst-num">{formatUptime(uptime)}</dd>
            <dt className="text-text-muted">版本</dt>
            <dd className="ldst-num">{version ?? '—'}</dd>
            <dt className="text-text-muted">数据库</dt>
            <dd>{db}</dd>
            <dt className="text-text-muted">检查时间</dt>
            <dd className="ldst-num">{api.health?.checked_at ?? '—'}</dd>
          </dl>

          {light === 'offline' && !confirming && (
            <Button
              variant="danger"
              className="mt-2 h-7 w-full px-2 text-caption"
              disabled={restarting}
              onClick={() => setConfirming(true)}
            >
              {restarting ? '重启中…' : '重启平台'}
            </Button>
          )}

          {confirming && (
            <div className="mt-2 rounded border border-border-strong bg-surface-2 p-2">
              <p className="text-text">确认重启平台？正在编辑的内容可能丢失。</p>
              <div className="mt-2 flex justify-end gap-2">
                <Button
                  variant="ghost"
                  className="h-7 px-2 text-caption"
                  onClick={() => setConfirming(false)}
                >
                  取消
                </Button>
                <Button
                  variant="danger"
                  className="h-7 bg-danger-soft px-2 text-caption"
                  disabled={restarting}
                  onClick={() => {
                    setConfirming(false)
                    void restart()
                  }}
                >
                  确认重启
                </Button>
              </div>
            </div>
          )}

          {restartError && (
            <p role="alert" className="mt-2 text-caption text-danger">
              {restartError}
            </p>
          )}
        </div>
      )}

      {/* 红灯时按钮常驻在顶栏（不必先打开浮层）；绿 / 黄整颗不渲染 */}
      {light === 'offline' && (
        <button
          type="button"
          aria-label="重启平台"
          disabled={restarting}
          onClick={askRestart}
          className="flex items-center gap-1 rounded border border-danger px-2 py-0.5 text-caption font-medium text-danger transition-colors duration-fast hover:bg-danger-soft disabled:opacity-60"
        >
          <RotateCw
            className={cn('size-3', restarting && 'animate-spin')}
            strokeWidth={2}
            aria-hidden="true"
          />
          {restarting ? '重启中…' : '重启平台'}
        </button>
      )}
    </div>
  )
}

export function PlatformStatusLight() {
  return <PlatformStatusLightView api={usePlatformHealth()} />
}

export default PlatformStatusLight

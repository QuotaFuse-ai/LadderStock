import { useEffect, useMemo, useState } from 'react'
import { CandlestickChart, Check, Plus, Swords } from 'lucide-react'

import { addWatch, fetchWatchlist } from '../../api/radar'
import { normalizeResearchCode } from '../../api/research'
import { cn } from '../../lib/cn'
import { Badge, Button } from '../ui'
import {
  MARKET_LABEL,
  ResearchSymbolSearch,
  marketOfCode,
  type ResearchSymbolMatch,
} from './ResearchSymbolSearch'

/**
 * 个股深挖页顶部的标的搜索条（QUOT-448）。
 *
 * 与参考库 `pages/Research.tsx` 的页头选股一致：搜索只在这里做一次，两个分页共用；
 * 选中后「看这只」跳 `/research/{code}`，并把当前 `demo / variant / date / state`
 * 取证参数原样带过去（`ResearchDetailScreen` 负责拼 URL）。
 *
 * 诚实边界：
 * - 市场识别只对 A 股三段（沪 / 深 / 北）成立；港股、美股在本机数据面里没有，
 *   入口置灰并写明「未接入」，不假装能查。
 * - 「多空辩论」路由当前不在 `ROUTES` 里，入口照参考库保留但标「待接入」，
 *   用 `aria-disabled` 挡住点击，绝不导航到 404。
 * - 「已在自选」读 `GET /api/watchlist`；接口不通时按钮禁用并写明原因，不默认成「未加」。
 */

export interface ResearchSymbolBarProps {
  /** 当前页的规范代码（`600000` / `600000.SH`），没选中时为 `null` */
  code: string | null
  /** 当前页已取到的名称（概览 / 六段产物给），没有就显示代码 */
  name: string | null
  onOpen: (code: string) => void
  className?: string
}

/** 详情页代码可能带 `.SH` 后缀，自选清单存的是裸 6 位；统一成裸码比较。 */
function bareCode(code: string | null): string | null {
  if (!code) return null
  const matched = /^(\d{6})(?:\.[A-Z]{2})?$/i.exec(code.trim())
  return matched ? matched[1] : null
}

const SUPPORTED = [
  { key: 'SH', label: '沪' },
  { key: 'SZ', label: '深' },
  { key: 'BJ', label: '北' },
] as const

const UNSUPPORTED = [
  { key: 'HK', label: '港股', reason: '本机数据面只有 A 股，港股行情未接入' },
  { key: 'US', label: '美股', reason: '本机数据面只有 A 股，美股行情未接入' },
] as const

export function ResearchSymbolBar({ code, name, onOpen, className }: ResearchSymbolBarProps) {
  const [raw, setRaw] = useState(code ?? '')
  const [error, setError] = useState<string | null>(null)
  const [watchCodes, setWatchCodes] = useState<Set<string> | null>(null)
  const [watchAvailable, setWatchAvailable] = useState<boolean | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setRaw(code ?? '')
    setError(null)
  }, [code])

  useEffect(() => {
    let live = true
    fetchWatchlist()
      .then((payload) => {
        if (!live) return
        setWatchAvailable(payload.available)
        setWatchCodes(
          new Set(
            payload.items
              .map((item) => bareCode(item.code))
              .filter((value): value is string => value !== null),
          ),
        )
      })
      .catch(() => {
        if (!live) return
        setWatchAvailable(false)
        setWatchCodes(new Set())
      })
    return () => {
      live = false
    }
  }, [])

  const current = bareCode(code)
  const inWatch = current !== null && watchCodes?.has(current) === true
  const market = marketOfCode(current)

  const choose = (value: string) => {
    const normalized = normalizeResearchCode(value)
    if (!normalized) {
      setError('输入 6 位 A 股代码，或在下拉里选一个标的；港股 / 美股本机数据未接入。')
      return
    }
    setError(null)
    if (bareCode(normalized) === current) return
    onOpen(normalized)
  }

  const pick = (match: ResearchSymbolMatch) => {
    setError(null)
    choose(match.symbol)
  }

  const add = () => {
    if (!current || saving) return
    setSaving(true)
    addWatch(current)
      .then(() => {
        setWatchCodes((previous) => new Set(previous ?? []).add(current))
      })
      .catch((cause) => {
        setError(`没加进自选：${cause instanceof Error ? cause.message : String(cause)}`)
      })
      .finally(() => setSaving(false))
  }

  const currentLabel = useMemo(() => {
    if (!current) return null
    const label = name?.trim() || current
    return name?.trim() ? `${label}（${current}）` : label
  }, [current, name])

  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded border border-border bg-surface p-4 shadow-card',
        className,
      )}
      aria-label="标的搜索"
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex w-72 max-w-full flex-col gap-1">
          <span className="text-caption text-text-secondary">代码 / 名称 / 拼音</span>
          <ResearchSymbolSearch
            value={raw}
            onChange={(next) => {
              setRaw(next)
              setError(null)
            }}
            onPick={pick}
            onEnter={() => choose(raw)}
            ariaLabel="股票代码、名称或拼音"
          />
        </div>

        <Button
          variant="primary"
          leadingIcon={<CandlestickChart className="size-3.5" aria-hidden="true" />}
          onClick={() => choose(raw)}
        >
          看这只
        </Button>

        {current && (
          <div className="flex min-w-0 flex-wrap items-center gap-2 pb-1 text-body">
            <span className="font-semibold text-text">{currentLabel}</span>
            {market && (
              <Badge tone="flat" title={`市场：${MARKET_LABEL[market] ?? market}`}>
                {MARKET_LABEL[market] ?? market}
              </Badge>
            )}
            <button
              type="button"
              onClick={add}
              disabled={inWatch || saving || watchAvailable === false}
              title={
                watchAvailable === false
                  ? '自选接口未接入，暂时无法加入自选'
                  : inWatch
                    ? '这只已经在自选里'
                    : '加入我的自选'
              }
              className={cn(
                'inline-flex items-center gap-1 rounded-xs border border-border px-2 py-1 text-caption',
                'text-text-secondary transition-colors duration-fast ease-ldst',
                'hover:text-accent disabled:cursor-not-allowed disabled:opacity-60',
              )}
            >
              {inWatch ? (
                <>
                  <Check className="size-3" aria-hidden="true" /> 已在自选
                </>
              ) : (
                <>
                  <Plus className="size-3" aria-hidden="true" /> 加入自选
                </>
              )}
            </button>
            <button
              type="button"
              aria-disabled="true"
              title="多空辩论页尚未接入（路由未注册），入口先保留"
              className="inline-flex cursor-not-allowed items-center gap-1 rounded-xs border border-border px-2 py-1 text-caption text-text-muted"
            >
              <Swords className="size-3" aria-hidden="true" /> 多空辩论
              <Badge tone="warn">待接入</Badge>
            </button>
          </div>
        )}
      </div>

      {error && (
        <p role="alert" className="text-caption leading-body text-danger">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-1.5 border-t border-border pt-2 text-caption text-text-muted">
        <span>市场：</span>
        {SUPPORTED.map((item) => (
          <Badge key={item.key} tone="flat" title={`${item.label}：本机数据面支持`}>
            {item.label}
          </Badge>
        ))}
        {UNSUPPORTED.map((item) => (
          <span
            key={item.key}
            aria-disabled="true"
            title={item.reason}
            className="inline-flex h-5 items-center gap-1 rounded-xs border border-border px-2 text-caption text-text-muted opacity-60"
          >
            {item.label}
            <span className="text-[10px]">未接入</span>
          </span>
        ))}
        <span className="ml-auto">只做 A 股：港股 / 美股不假装支持</span>
      </div>
    </div>
  )
}

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Loader2, Search } from 'lucide-react'

import { api } from '../../api'
import { cn } from '../../lib/cn'
import { classifyIntent, normalizeQuery } from '../../lib/searchIntent'

/**
 * 个股深挖页顶部标的搜索（QUOT-448 缺口 1）。
 *
 * 只认 `GET /api/symbols/search`（QUOT-405 已交付的标的字典），不另造第二套
 * 前端字典、不拼假候选：代码精确、中文名包含、拼音（后端接入 pypinyin 后）三种写法
 * 都走同一条接口。接口未接入 / 失败时如实报错并提示「可以直接输入 6 位代码」，
 * 不弹演示数据。
 *
 * 键盘与读屏契约照 `components/search/SearchPanel` 的既有口径：`role=combobox` +
 * `aria-expanded` / `aria-controls` / `aria-activedescendant`，上下键循环、Enter 选中、
 * Esc 收起，中文输入法选字期间的 Enter 不算。
 */

export interface ResearchSymbolMatch {
  symbol: string
  name: string
  market: string
  kind: string
  board: string
  pinyin: string | null
}

/** 本机数据面只覆盖 A 股三段（沪 / 深 / 北）；港股、美股明确不假装支持。 */
export const A_SHARE_MARKETS = ['SH', 'SZ', 'BJ'] as const

export const MARKET_LABEL: Record<string, string> = {
  SH: '沪',
  SZ: '深',
  BJ: '北',
}

export function marketOfCode(code: string | null): string | null {
  if (!code) return null
  if (/^[689]/.test(code)) return 'SH'
  if (/^[03]/.test(code)) return 'SZ'
  if (/^[48]/.test(code)) return 'BJ'
  return null
}

const CACHE_MS = 10 * 60 * 1000
const DEBOUNCE_MS = 300
const DROP_MAX_PX = 288

interface CacheEntry {
  at: number
  matches: ResearchSymbolMatch[]
}

const cache = new Map<string, CacheEntry>()

export type SearchPhase = 'idle' | 'loading' | 'results' | 'none' | 'failed'

export interface ResearchSymbolSearchProps {
  value: string
  onChange: (value: string) => void
  /** 从下拉里选中一条：调用方负责换页或换 URL */
  onPick: (match: ResearchSymbolMatch) => void
  /** 下拉没开时按回车（页面原来的「回车开始」） */
  onEnter: () => void
  id?: string
  placeholder?: string
  ariaLabel?: string
  disabled?: boolean
  className?: string
}

function parseMatches(raw: unknown): ResearchSymbolMatch[] {
  if (!raw || typeof raw !== 'object') return []
  const payload = (raw as { payload?: unknown }).payload
  if (!payload || typeof payload !== 'object') return []
  const list = (payload as { matches?: unknown }).matches
  if (!Array.isArray(list)) return []
  const out: ResearchSymbolMatch[] = []
  for (const item of list) {
    if (!item || typeof item !== 'object') continue
    const row = item as Record<string, unknown>
    const symbol = typeof row.symbol === 'string' ? row.symbol : ''
    const name = typeof row.name === 'string' ? row.name : ''
    if (!symbol) continue
    out.push({
      symbol,
      name,
      market: typeof row.market === 'string' ? row.market : (marketOfCode(symbol) ?? ''),
      kind: typeof row.kind === 'string' ? row.kind : 'stock',
      board: typeof row.board === 'string' ? row.board : '',
      pinyin: typeof row.pinyin === 'string' ? row.pinyin : null,
    })
  }
  return out
}

/**
 * 没命中时给一句能解释原因的话：拼音检索依赖后端 `pypinyin`（当前不在依赖里，
 * `pinyin` 字段如实返回 `null`），港股 / 美股不在本机数据面内。
 */
function emptyHint(raw: string): string {
  const query = normalizeQuery(raw)
  const intent = classifyIntent(query)
  if (intent === 'keyword' && /^[a-z]+$/i.test(query.text)) {
    return `没找到「${query.text}」。拼音检索尚未接入（后端未安装 pypinyin），可改输入代码或中文名称；美股代码本机数据未接入。`
  }
  if (/^\d{5}$/.test(query.text)) {
    return '港股（5 位代码）本机数据未接入，只支持 A 股 6 位代码。'
  }
  return `没找到「${query.text}」，也可以直接输入 6 位 A 股代码。`
}

export function ResearchSymbolSearch({
  value,
  onChange,
  onPick,
  onEnter,
  id,
  placeholder = '600000 / 浦发银行 / pfyh',
  ariaLabel = '股票代码、名称或拼音',
  disabled = false,
  className,
}: ResearchSymbolSearchProps) {
  const autoId = useId()
  const inputId = id ?? `${autoId}-input`
  const listId = `${autoId}-list`
  const [phase, setPhase] = useState<SearchPhase>('idle')
  const [matches, setMatches] = useState<ResearchSymbolMatch[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [place, setPlace] = useState<{ left: number; top: number; minWidth: number } | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  const focused = useRef(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const query = normalizeQuery(value)
    controller.current?.abort()
    controller.current = null

    if (query.empty || (picked !== null && picked === value.trim())) {
      setPhase('idle')
      setMatches([])
      setOpen(false)
      setError(null)
      return
    }

    const key = query.text.toLowerCase()
    const hit = cache.get(key)
    if (hit && Date.now() - hit.at < CACHE_MS) {
      setMatches(hit.matches)
      setPhase(hit.matches.length > 0 ? 'results' : 'none')
      setActive(0)
      setOpen(focused.current)
      setError(null)
      return
    }

    setPhase('loading')
    setError(null)
    setOpen(false)
    const timer = window.setTimeout(() => {
      const next = new AbortController()
      controller.current = next
      api
        .symbolsSearch(query.text, 10, next.signal)
        .then((raw) => {
          if (next.signal.aborted) return
          const rows = parseMatches(raw)
          cache.set(key, { at: Date.now(), matches: rows })
          setMatches(rows)
          setPhase(rows.length > 0 ? 'results' : 'none')
          setActive(0)
          setOpen(focused.current)
        })
        .catch((cause) => {
          if (next.signal.aborted) return
          const detail = cause instanceof Error ? cause.message : String(cause)
          setMatches([])
          setPhase('failed')
          setOpen(false)
          setError(`名称搜索暂时不通（${detail}），可以直接输入 6 位代码。`)
        })
    }, DEBOUNCE_MS)

    return () => window.clearTimeout(timer)
  }, [value, picked])

  useEffect(() => () => controller.current?.abort(), [])

  const showList = open && matches.length > 0

  const updatePlace = useCallback(() => {
    const rect = box.current?.getBoundingClientRect()
    if (!rect) return
    const below = window.innerHeight - rect.bottom
    const up = below < DROP_MAX_PX && rect.top > below
    setPlace({
      left: rect.left,
      top: up ? Math.max(8, rect.top - DROP_MAX_PX - 4) : rect.bottom + 4,
      minWidth: Math.max(288, rect.width),
    })
  }, [])

  useEffect(() => {
    if (!showList) {
      setPlace(null)
      return
    }
    updatePlace()
    window.addEventListener('scroll', updatePlace, true)
    window.addEventListener('resize', updatePlace)
    return () => {
      window.removeEventListener('scroll', updatePlace, true)
      window.removeEventListener('resize', updatePlace)
    }
  }, [showList, updatePlace])

  const choose = (match: ResearchSymbolMatch) => {
    setPicked(match.symbol)
    setOpen(false)
    onChange(match.symbol)
    onPick(match)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return
    if (showList) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActive((index) => (index + 1) % matches.length)
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActive((index) => (index - 1 + matches.length) % matches.length)
        return
      }
      if (event.key === 'Enter') {
        event.preventDefault()
        choose(matches[active] ?? matches[0])
        return
      }
    }
    if (event.key === 'Escape') {
      setOpen(false)
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      onEnter()
    }
  }

  const activeOptionId = showList ? `${listId}-opt-${active}` : undefined
  const hint = useMemo(() => (phase === 'none' ? emptyHint(value) : null), [phase, value])

  return (
    <div className={cn('relative', className)}>
      <div ref={box} className="relative">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-2 top-1/2 flex -translate-y-1/2 text-text-muted"
        >
          <Search className="size-3.5" />
        </span>
        <input
          id={inputId}
          value={value}
          onChange={(event) => {
            setPicked(null)
            onChange(event.target.value)
          }}
          onKeyDown={handleKeyDown}
          onFocus={() => {
            focused.current = true
            if (matches.length > 0) setOpen(true)
          }}
          onBlur={() => {
            focused.current = false
            setOpen(false)
          }}
          placeholder={placeholder}
          aria-label={ariaLabel}
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={activeOptionId}
          aria-invalid={error !== null || undefined}
          aria-describedby={`${inputId}-status`}
          autoComplete="off"
          spellCheck={false}
          disabled={disabled}
          className={cn(
            'h-control w-full rounded border border-border-strong bg-sunken pl-7 pr-8 text-body text-text placeholder:text-text-muted',
            'transition-colors duration-fast ease-ldst focus-visible:border-accent disabled:cursor-not-allowed disabled:opacity-50',
            (error !== null || phase === 'failed') && 'border-danger',
          )}
        />
        {phase === 'loading' && (
          <Loader2
            className="absolute right-2 top-1/2 size-3.5 -translate-y-1/2 animate-spin text-text-muted"
            aria-hidden="true"
          />
        )}
      </div>

      <p id={`${inputId}-status`} className="sr-only" role="status">
        {phase === 'loading'
          ? '正在搜索标的'
          : showList
            ? `找到 ${matches.length} 个标的，用上下键选择，回车确认`
            : ''}
      </p>
      {error && (
        <p role="alert" className="mt-1 text-caption leading-body text-danger">
          {error}
        </p>
      )}
      {!error && hint && (
        <p className="mt-1 text-caption leading-body text-text-muted">{hint}</p>
      )}

      {showList &&
        place &&
        createPortal(
          <ul
            id={listId}
            role="listbox"
            aria-label="标的候选"
            style={{ position: 'fixed', left: place.left, top: place.top, minWidth: place.minWidth }}
            className="z-50 max-h-72 overflow-auto rounded border border-border bg-surface p-1 shadow-overlay"
          >
            {matches.map((match, index) => (
              <li
                key={match.symbol}
                id={`${listId}-opt-${index}`}
                role="option"
                aria-selected={index === active}
                onMouseDown={(event) => {
                  event.preventDefault()
                  choose(match)
                }}
                onMouseEnter={() => setActive(index)}
                className={cn(
                  'flex cursor-pointer items-center gap-2 rounded-xs px-2 py-1.5 text-body',
                  index === active && 'bg-surface-3',
                )}
              >
                <span className="min-w-0 flex-1 truncate">{match.name || '名称未接入'}</span>
                <span className="ldst-num shrink-0 text-caption text-text-secondary">{match.symbol}</span>
                <span className="w-14 shrink-0 text-right text-caption text-text-muted">
                  {[MARKET_LABEL[match.market], match.board].filter(Boolean).join(' · ') || '市场未知'}
                </span>
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </div>
  )
}

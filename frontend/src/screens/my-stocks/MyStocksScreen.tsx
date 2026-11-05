import { useEffect, useState } from 'react'
import { Star, Wallet } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import { Tabs, type TabOption } from '../../components/ui'
import { PortfolioPanel } from './PortfolioPanel'
import { WatchlistPanel } from './WatchlistPanel'

/**
 * 我的股票（QUOT-431）：一页两标签 `/my-stocks?tab=watch|hold`。
 *
 * 地址里的 `?tab=` 说了算——旧的 `/watchlist`、`/positions` 重定向过来时会带上；
 * 没带就回到上次看的那一页（本机 localStorage）。`hold` 由 QUOT-434 的
 * 流水驱动持仓页（`PortfolioPanel`）渲染，本页的路由与标签不变。
 */

export type MyStocksTab = 'watch' | 'hold'

const TAB_KEY = 'ldst.my-stocks.tab'

const TABS: Array<TabOption<MyStocksTab>> = [
  { value: 'watch', label: '自选', icon: <Star className="size-3.5" aria-hidden="true" /> },
  { value: 'hold', label: '持仓', icon: <Wallet className="size-3.5" aria-hidden="true" /> },
]

function isTab(value: string | null): value is MyStocksTab {
  return value === 'watch' || value === 'hold'
}

function readRemembered(): MyStocksTab | null {
  try {
    const raw = window.localStorage.getItem(TAB_KEY)
    return isTab(raw) ? raw : null
  } catch {
    // 隐私模式下读不到：退回 watch，不影响页面本身
    return null
  }
}

export default function MyStocksScreen() {
  const [params, setParams] = useSearchParams()
  // 只读一次：地址优先，其次上次记忆，最后缺省自选
  const [remembered] = useState<MyStocksTab | null>(readRemembered)
  const fromUrl = params.get('tab')
  const tab: MyStocksTab = isTab(fromUrl) ? fromUrl : (remembered ?? 'watch')

  useEffect(() => {
    try {
      window.localStorage.setItem(TAB_KEY, tab)
    } catch {
      // 存储不可用：本次会话仍然切换正常，只是下次不记得
    }
  }, [tab])

  const pick = (next: MyStocksTab) => {
    setParams(
      (prev) => {
        const updated = new URLSearchParams(prev)
        updated.set('tab', next)
        return updated
      },
      { replace: true },
    )
  }

  return (
    <div className="flex flex-col gap-5">
      <Tabs ariaLabel="我的股票" tabs={TABS} value={tab} onChange={pick} />
      {tab === 'watch' ? <WatchlistPanel /> : <PortfolioPanel />}
    </div>
  )
}

import { Link, Navigate, Route, Routes } from 'react-router-dom'
import type { ReactElement } from 'react'

import Layout from './components/Layout'
import HomeScreen from './screens/HomeScreen'
import IndustryDetailScreen from './screens/industry/IndustryDetailScreen'
import IndustryMatrixScreen from './screens/industry/IndustryMatrixScreen'
import IntradayScreen from './screens/intraday/IntradayScreen'
import LimitUpScreen from './screens/limitup/LimitUpScreen'
import JournalScreen from './screens/JournalScreen'
import MarketScreen from './screens/MarketScreen'
import CalendarScreen from './screens/radar/CalendarScreen'
import NewsScreen from './screens/radar/NewsScreen'
import PoolScreen from './screens/radar/PoolScreen'
import PositionsScreen from './screens/radar/PositionsScreen'
import ResearchDetailScreen from './screens/research/ResearchDetailScreen'
import ResearchEntryScreen from './screens/research/ResearchEntryScreen'
import ResearchReportScreen from './screens/research/ResearchReportScreen'
import ReviewScreen from './screens/ReviewScreen'
import SettingsScreen from './screens/SettingsScreen'
import SignalsScreen from './screens/signals/SignalsScreen'
import ValidateScreen from './screens/ValidateScreen'
import WatchScreen from './screens/WatchScreen'
import WeeklyScreen from './screens/WeeklyScreen'
import { ROUTE_PATHS, type RoutePath } from './routes'

/**
 * 路由表（DESIGN-SPEC §4.1）。
 *
 * 八个一级导航 + 设置，旧深链全部保留：
 * `/review` `/watch` `/validate` `/journal` 在此前版本就存在，不能 404；
 * 旧的 `index → /review` 重定向改成 `index → /`（首页是新的工作流起点）。
 * `/market` 是 QUOT-198 新增：三段用 `?tab=sentiment|ladder|theme` 区分，不是三个子路由。
 *
 * 路径清单在 `routes.ts`，组件表按 `Record<RoutePath, …>` 声明：两边少一条都编译不过，
 * `tests/nav-routes.test.ts` 再用同一份路径清单比对侧栏入口。
 */

function NotFound() {
  return (
    <section className="mx-auto flex min-h-empty max-w-content flex-col items-start justify-center gap-2">
      <p className="text-caption text-text-muted">404</p>
      <h1 className="text-page font-semibold text-text">页面不存在</h1>
      <p className="text-body text-text-secondary">
        地址可能已经变更。九个入口在左侧导航：首页 / 市场 / 盯盘 / 复盘 / 日志 / 我的股票 / 验证 /
        产业研究 / 个股深挖（设置固定在底部）。
      </p>
      <Link
        to="/"
        className="mt-2 inline-flex h-control items-center rounded border border-border-strong bg-surface px-3 text-body text-text hover:bg-surface-3"
      >
        回到首页
      </Link>
    </section>
  )
}

const ROUTE_ELEMENTS: Record<RoutePath, ReactElement> = {
  '/': <HomeScreen />,
  '/market': <MarketScreen />,
  '/watch': <WatchScreen />,
  // 盯盘第二页：竞价与盘中 6 时点快照（QUOT-302 / DEC-035 §5），`?date=` + `?slot=` 深链
  '/intraday': <IntradayScreen />,
  '/review': <ReviewScreen />,
  // R11 周度复盘（QUOT-149）：`?week=YYYY-Www`，缺省本周
  '/weekly': <WeeklyScreen />,
  '/journal': <JournalScreen />,
  '/validate': <ValidateScreen />,
  // 涨停样本：「验证」分组下的二级入口，支持 `?date=YYYY-MM-DD` 深链
  '/limit-up': <LimitUpScreen />,
  // R10 资讯雷达 / 事件日历 / 候选池 / 我的持仓；挂二级导航，不新增一级
  '/news': <NewsScreen />,
  '/calendar': <CalendarScreen />,
  '/pool': <PoolScreen />,
  '/positions': <PositionsScreen />,
  // R9 产业研究：矩阵 / 单行业 / 规则信号（QUOT-300 起有侧栏一级入口）
  '/industry': <IndustryMatrixScreen />,
  '/industry/:name': <IndustryDetailScreen />,
  '/signals': <SignalsScreen />,
  '/settings': <SettingsScreen />,
  // QUOT-146：个股深挖入口是落地页（填 6 位代码），深挖与报告页由它链入
  '/research': <ResearchEntryScreen />,
  '/research/:code': <ResearchDetailScreen />,
  '/research/:code/report': <ResearchReportScreen />,
  // 旧入口：此前首页重定向到复盘；带查询串保留（`?state=` 演示态）
  '/index.html': <Navigate to="/" replace />,
  '*': <NotFound />,
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        {ROUTE_PATHS.map((path) => (
          <Route key={path} path={path} element={ROUTE_ELEMENTS[path]} />
        ))}
      </Route>
    </Routes>
  )
}

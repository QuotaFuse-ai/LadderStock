import { Link, Navigate, Route, Routes } from 'react-router-dom'

import Layout from './components/Layout'
import HomeScreen from './screens/HomeScreen'
import IndustryDetailScreen from './screens/industry/IndustryDetailScreen'
import IndustryMatrixScreen from './screens/industry/IndustryMatrixScreen'
import LimitUpScreen from './screens/limitup/LimitUpScreen'
import JournalScreen from './screens/JournalScreen'
import MarketScreen from './screens/MarketScreen'
import CalendarScreen from './screens/radar/CalendarScreen'
import NewsScreen from './screens/radar/NewsScreen'
import PoolScreen from './screens/radar/PoolScreen'
import PositionsScreen from './screens/radar/PositionsScreen'
import ResearchDetailScreen from './screens/research/ResearchDetailScreen'
import ResearchReportScreen from './screens/research/ResearchReportScreen'
import ReviewScreen from './screens/ReviewScreen'
import SettingsScreen from './screens/SettingsScreen'
import SignalsScreen from './screens/signals/SignalsScreen'
import ValidateScreen from './screens/ValidateScreen'
import WatchScreen from './screens/WatchScreen'
import WeeklyScreen from './screens/WeeklyScreen'

/**
 * 路由表（DESIGN-SPEC §4.1）。
 *
 * 六个一级导航 + 设置，旧深链全部保留：
 * `/review` `/watch` `/validate` `/journal` 在此前版本就存在，不能 404；
 * 旧的 `index → /review` 重定向改成 `index → /`（首页是新的工作流起点）。
 * `/market` 是 QUOT-198 新增：三段用 `?tab=sentiment|ladder|theme` 区分，不是三个子路由。
 */

function NotFound() {
  return (
    <section className="mx-auto flex min-h-empty max-w-content flex-col items-start justify-center gap-2">
      <p className="text-caption text-text-muted">404</p>
      <h1 className="text-page font-semibold text-text">页面不存在</h1>
      <p className="text-body text-text-secondary">
        地址可能已经变更。六个入口在左侧导航：首页 / 市场 / 盯盘 / 复盘 / 日志 / 验证（设置固定在底部）。
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

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomeScreen />} />
        <Route path="/market" element={<MarketScreen />} />
        <Route path="/watch" element={<WatchScreen />} />
        <Route path="/review" element={<ReviewScreen />} />
        {/* R11 周度复盘（QUOT-149）：`?week=YYYY-Www`，缺省本周 */}
        <Route path="/weekly" element={<WeeklyScreen />} />
        <Route path="/journal" element={<JournalScreen />} />
        <Route path="/validate" element={<ValidateScreen />} />
        {/* 涨停样本：挂在「验证」分组下的二级入口，支持 `?date=YYYY-MM-DD` 深链 */}
        <Route path="/limit-up" element={<LimitUpScreen />} />
        {/* R10 资讯雷达 / 事件日历 / 候选池 / 我的持仓；挂二级导航，不新增一级 */}
        <Route path="/news" element={<NewsScreen />} />
        <Route path="/calendar" element={<CalendarScreen />} />
        <Route path="/pool" element={<PoolScreen />} />
        <Route path="/positions" element={<PositionsScreen />} />
        {/* R9 产业研究：矩阵 / 单行业 / 规则信号；深链进入，不占一级导航 */}
        <Route path="/industry" element={<IndustryMatrixScreen />} />
        <Route path="/industry/:name" element={<IndustryDetailScreen />} />
        <Route path="/signals" element={<SignalsScreen />} />
        <Route path="/settings" element={<SettingsScreen />} />
        {/* QUOT-146：个股深挖与六阶段研究报告（深链进入，不占一级导航） */}
        <Route path="/research/:code" element={<ResearchDetailScreen />} />
        <Route path="/research/:code/report" element={<ResearchReportScreen />} />
        {/* 旧入口：此前首页重定向到复盘；带查询串保留（`?state=` 演示态） */}
        <Route path="/index.html" element={<Navigate to="/" replace />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}

import { Link, Navigate, Route, Routes } from 'react-router-dom'

import Layout from './components/Layout'
import HomeScreen from './screens/HomeScreen'
import JournalScreen from './screens/JournalScreen'
import MarketScreen from './screens/MarketScreen'
import ReviewScreen from './screens/ReviewScreen'
import SettingsScreen from './screens/SettingsScreen'
import ValidateScreen from './screens/ValidateScreen'
import WatchScreen from './screens/WatchScreen'

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
        <Route path="/journal" element={<JournalScreen />} />
        <Route path="/validate" element={<ValidateScreen />} />
        <Route path="/settings" element={<SettingsScreen />} />
        {/* 旧入口：此前首页重定向到复盘；带查询串保留（`?state=` 演示态） */}
        <Route path="/index.html" element={<Navigate to="/" replace />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}

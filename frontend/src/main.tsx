import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import App from './App'
import { bootstrapPersistence } from '../persist/bootstrap'
import { applyTheme, readDensity, readThemeChoice, watchSystemTheme } from './lib/theme'
import { refreshTheme } from './lib/prefs'
import './styles/tokens.css'
import './styles/index.css'

/**
 * 首帧前把主题 / 密度落到 <html>（index.html 的内联脚本已先做过一次，
 * 这里负责 JS 侧状态与 matchMedia 监听，保证「跟随系统」时即时切换）。
 */
function setupPreferences(): void {
  applyTheme(readThemeChoice())
  document.documentElement.setAttribute('data-ldst-density', readDensity())
  watchSystemTheme(() => {
    if (readThemeChoice() === 'system') {
      applyTheme('system')
      refreshTheme()
    }
  })
}

/**
 * 先恢复本机状态再渲染：草稿在首帧就位，避免「先空后跳」的闪变。
 * 恢复层任何异常都只降级，不阻塞看板启动（任务要求 2 / 3）。
 */
async function start(): Promise<void> {
  setupPreferences()

  try {
    const result = await bootstrapPersistence()
    if (result.failed.length > 0) {
      console.warn('[state] 以下分片恢复失败，已按空状态启动：', result.failed)
    }
  } catch (error) {
    console.error('[state] 持久化初始化失败，按空状态启动', error)
  }

  const container = document.getElementById('root')
  if (!container) throw new Error('缺少 #root 容器')

  createRoot(container).render(
    <StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </StrictMode>,
  )
}

void start()

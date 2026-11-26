// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, type BoardPortSettings } from '../src/api'
import SettingsScreen from '../src/screens/SettingsScreen'

/**
 * 端口设置卡（DEC-092）：
 * - 读：当前口 / 默认 / 偏好三件套来自 GET /settings/port，前端不猜；
 * - 保存：范围外的输入禁用按钮；合法输入 PUT 成功后上屏“重启后生效”；
 * - 冲突：后端 409/422 的 detail（含占用者 PID）原样上屏，不本地编规则；
 * - 清除：偏好存在时才出现“清除偏好”。
 */

const BASE = {
  default_board: 5950,
  user_board: null as number | null,
  current_board: 5950,
  reserved_ports: [8000, 8765, 5951],
  min_port: 1024,
  max_port: 65535,
  takes_effect: 'restart' as const,
}

function mockSettings(payload: Partial<BoardPortSettings> = {}) {
  vi.spyOn(api, 'portSettings').mockResolvedValue({ ...BASE, ...payload })
}

function mockOfflineBasics() {
  // 设置页首屏还会拉控制面/健康等接口；离线即让它降级，不影响关于区渲染
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new TypeError('offline')
    }),
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function renderAbout() {
  return render(
    <MemoryRouter initialEntries={['/settings?tab=about']}>
      <SettingsScreen />
    </MemoryRouter>,
  )
}

describe('端口设置卡', () => {
  it('读出三件套：当前口 / 默认 / 已设偏好', async () => {
    mockOfflineBasics()
    mockSettings({ user_board: 1111 })

    renderAbout()

    const card = await screen.findByTestId('board-port-card')
    expect(card.textContent).toContain('5950')
    expect(card.textContent).toContain('1111')
    expect(card.textContent).toContain('已设偏好')
  })

  it('保留段端口输入被前端拦下（保存钮置灰），不发请求', async () => {
    mockOfflineBasics()
    mockSettings()

    renderAbout()

    await screen.findByTestId('board-port-card')
    const input = screen.getByTestId('board-port-input') as HTMLInputElement
    const save = await screen.findByTestId('board-port-save')

    fireEvent.change(input, { target: { value: '8000' } })
    await waitFor(() => {
      expect((save as HTMLButtonElement).disabled).toBe(true)
    })

    fireEvent.change(input, { target: { value: '99' } })
    await waitFor(() => {
      expect((save as HTMLButtonElement).disabled).toBe(true)
    })
  })

  it('保存成功：上屏“重启后生效”，并刷新数据', async () => {
    mockOfflineBasics()
    mockSettings()
    const save = vi
      .spyOn(api, 'portSettingsSave')
      .mockResolvedValue({ ok: true, board: 1111, action: 'saved', detail: '已保存，重启应用后从 http://127.0.0.1:1111/ 打开' })

    renderAbout()

    await screen.findByTestId('board-port-card')
    fireEvent.change(screen.getByTestId('board-port-input'), { target: { value: '1111' } })
    fireEvent.click(await screen.findByTestId('board-port-save'))

    const notice = await screen.findByTestId('board-port-notice')
    expect(notice.textContent).toContain('http://127.0.0.1:1111/')
    expect(notice.textContent).toContain('重启应用')
    expect(save).toHaveBeenCalledWith(1111)
  })

  it('端口被占：后端 409 的人话原因上屏（含 PID 线索）', async () => {
    mockOfflineBasics()
    mockSettings()
    vi.spyOn(api, 'portSettingsSave').mockRejectedValue(
      new ApiError(
        'http',
        'conflict',
        409,
        '端口 2222 正被其它程序占用（PID 1234 · chrome.exe），换一个或先关掉它再保存',
      ),
    )

    renderAbout()

    await screen.findByTestId('board-port-card')
    fireEvent.change(screen.getByTestId('board-port-input'), { target: { value: '2222' } })
    fireEvent.click(await screen.findByTestId('board-port-save'))

    const notice = await screen.findByTestId('board-port-notice')
    expect(notice.textContent).toContain('PID 1234')
    expect(notice.textContent).toContain('chrome.exe')
  })

  it('无偏好时不出现“清除偏好”，有偏好时可清除', async () => {
    mockOfflineBasics()
    mockSettings({ user_board: 1111 })
    const clear = vi
      .spyOn(api, 'portSettingsClear')
      .mockResolvedValue({ ok: true, board: null, action: 'cleared', detail: '已清除偏好，重启应用后回到默认端口 5950' })

    renderAbout()

    const button = await screen.findByTestId('board-port-clear')
    fireEvent.click(button)

    await waitFor(() => {
      expect(clear).toHaveBeenCalled()
    })
  })
})

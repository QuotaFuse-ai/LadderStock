// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PlatformStatusLightView } from '../src/components/PlatformStatusLight'
import {
  FAILURES_BEFORE_RED,
  PLATFORM_POLL_MS,
  nextLightState,
  usePlatformHealth,
  type PlatformHealth,
  type PlatformHealthApi,
  type ProbeResult,
} from '../src/lib/usePlatformHealth'

/**
 * 顶栏服务状态灯 + 看门狗健康（QUOT-443 / DEC-063）。
 *
 * 两层：状态机是纯函数（连续 2 次失败才红、`db_ok=false` 判黄），
 * 组件只负责「红灯才出重启入口」与两步确认。
 */

function health(partial: Partial<PlatformHealth> = {}): PlatformHealth {
  return {
    status: 'ok',
    uptime_sec: 42,
    version: '0.1.0',
    db_ok: true,
    checked_at: '2026-11-06T09:00:00+00:00',
    api: { uptime_sec: 42, version: '0.1.0', db_ok: true },
    watchdog: { uptime_sec: 42, version: '0.1.0' },
    ...partial,
  }
}

const OK: ProbeResult = { ok: true, body: health() }
const FAIL: ProbeResult = { ok: false, body: null }

describe('状态机', () => {
  it('ok → 绿', () => {
    expect(nextLightState({ light: 'unknown', failures: 0 }, OK)).toEqual({
      light: 'ok',
      failures: 0,
    })
  })

  it('degraded / db_ok=false → 黄', () => {
    const degraded = nextLightState(
      { light: 'ok', failures: 0 },
      { ok: true, body: health({ status: 'degraded' }) },
    )
    expect(degraded.light).toBe('degraded')

    const dbBroken = nextLightState(
      { light: 'ok', failures: 0 },
      { ok: true, body: health({ db_ok: false }) },
    )
    expect(dbBroken.light).toBe('degraded')
  })

  it('单次失败维持原色，连续 2 次才判红', () => {
    const first = nextLightState({ light: 'ok', failures: 0 }, FAIL)
    expect(first.light).toBe('ok')
    expect(first.failures).toBe(1)

    const second = nextLightState(first, FAIL)
    expect(second.light).toBe('offline')
    expect(second.failures).toBe(FAILURES_BEFORE_RED)

    // 恢复后计数清零
    expect(nextLightState(second, OK)).toEqual({ light: 'ok', failures: 0 })
  })

  it('看门狗明确回 offline → 立即红（不等 2 次）', () => {
    const state = nextLightState(
      { light: 'ok', failures: 0 },
      { ok: true, body: health({ status: 'offline', db_ok: null, api: null }) },
    )
    expect(state.light).toBe('offline')
  })
})

// --------------------------------------------------------------------------- //
// 轮询
// --------------------------------------------------------------------------- //

function Harness({ intervalMs = 1000 }: { intervalMs?: number }) {
  const api = usePlatformHealth({ intervalMs, baseUrl: '' })
  return (
    <div>
      <span data-testid="light">{api.light}</span>
      <span data-testid="version">{api.health?.version ?? '-'}</span>
    </div>
  )
}

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as unknown as Response
}

describe('轮询节奏', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    cleanup()
  })

  it('默认间隔 ≤2.5s：两次失败 + 2s 探测超时仍能在 5s 内转红', () => {
    expect(PLATFORM_POLL_MS).toBeLessThanOrEqual(2500)
  })

  it('首拍绿灯后按间隔继续探测', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(health()))
    vi.stubGlobal('fetch', fetchMock)

    render(<Harness intervalMs={1000} />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.getByTestId('light').textContent).toBe('ok')
    expect(fetchMock).toHaveBeenCalledWith('/platform/health', { cache: 'no-store' })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)

    const url = String(fetchMock.mock.calls[0][0])
    expect(url.endsWith('/platform/health')).toBe(true)
  })

  it('单次抖动不变红，连续 2 次失败转红', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(health()))
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(jsonResponse(health()))
      .mockRejectedValueOnce(new Error('boom'))
      .mockRejectedValueOnce(new Error('boom'))
    vi.stubGlobal('fetch', fetchMock)

    render(<Harness intervalMs={1000} />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.getByTestId('light').textContent).toBe('ok')

    // 第 1 次失败：保持绿
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(screen.getByTestId('light').textContent).toBe('ok')

    // 恢复一拍，再连续两次失败 → 红
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(screen.getByTestId('light').textContent).toBe('ok')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(screen.getByTestId('light').textContent).toBe('ok')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(screen.getByTestId('light').textContent).toBe('offline')
  })

  it('页面隐藏时暂停，回到前台立即补一拍', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(health()))
    vi.stubGlobal('fetch', fetchMock)
    let hidden = false
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => hidden,
    })

    render(<Harness intervalMs={1000} />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    const initial = fetchMock.mock.calls.length

    hidden = true
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(fetchMock.mock.calls.length).toBe(initial)

    hidden = false
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fetchMock.mock.calls.length).toBe(initial + 1)
  })
})

// --------------------------------------------------------------------------- //
// 重启：请求报错也要按真实健康轮询到底
// --------------------------------------------------------------------------- //

type ApiRef = { current: PlatformHealthApi | null; reload: () => void }

function RestartHarness({ apiRef }: { apiRef: ApiRef }) {
  apiRef.current = usePlatformHealth({
    baseUrl: '',
    intervalMs: 60_000,
    restartPollMs: 1000,
    restartTimeoutMs: 5000,
    reload: apiRef.reload,
  })
  return null
}

describe('重启流程', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    cleanup()
  })

  function setup(
    restartResponse: Response,
    healthBody: PlatformHealth,
  ): { reload: ReturnType<typeof vi.fn>; apiRef: ApiRef } {
    const reload = vi.fn()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/platform/restart')) return restartResponse
      return jsonResponse(healthBody)
    })
    vi.stubGlobal('fetch', fetchMock)
    const apiRef: ApiRef = { current: null, reload }
    render(<RestartHarness apiRef={apiRef} />)
    return { reload, apiRef }
  }

  it('POST 503 但服务实际恢复 → 不报错，直接刷新页面', async () => {
    const { reload, apiRef } = setup(
      {
        ok: false,
        status: 503,
        json: async () => ({ ok: false, reason: '所有重启方式都失败（见 attempts）' }),
      } as unknown as Response,
      health(),
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await act(async () => {
      const pending = apiRef.current?.restart()
      await vi.advanceTimersByTimeAsync(1000)
      await pending
    })

    expect(reload).toHaveBeenCalledTimes(1)
    expect(apiRef.current?.restartError).toBeNull()
  })

  it('POST 成功但服务一直没起来 → 90s 口径的超时文案（此处按 5s 缩短）', async () => {
    const { reload, apiRef } = setup(
      { ok: true, status: 200, json: async () => ({ ok: true }) } as unknown as Response,
      health({ status: 'offline', db_ok: null, api: null }),
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await act(async () => {
      const pending = apiRef.current?.restart()
      await vi.advanceTimersByTimeAsync(6000)
      await pending
    })

    expect(reload).not.toHaveBeenCalled()
    expect(apiRef.current?.restartError).toContain('还没恢复')
  })

  it('请求报错且始终未恢复 → 服务端原因与超时都如实展示', async () => {
    const { apiRef } = setup(
      {
        ok: false,
        status: 503,
        json: async () => ({ ok: false, reason: '所有重启方式都失败（见 attempts）' }),
      } as unknown as Response,
      health({ status: 'offline', db_ok: null, api: null }),
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await act(async () => {
      const pending = apiRef.current?.restart()
      await vi.advanceTimersByTimeAsync(6000)
      await pending
    })

    expect(apiRef.current?.restartError).toContain('所有重启方式都失败')
    expect(apiRef.current?.restartError).toContain('还没恢复')
  })
})

// --------------------------------------------------------------------------- //
// 顶栏状态灯
// --------------------------------------------------------------------------- //

function fakeApi(partial: Partial<PlatformHealthApi> = {}): PlatformHealthApi {
  return {
    light: 'ok',
    health: health(),
    restarting: false,
    restartError: null,
    restart: vi.fn(async () => {}),
    ...partial,
  }
}

describe('顶栏状态灯', () => {
  afterEach(cleanup)

  it('绿灯不渲染重启入口', () => {
    render(<PlatformStatusLightView api={fakeApi({ light: 'ok' })} />)
    expect(screen.queryByRole('button', { name: /重启平台/ })).toBeNull()
    expect(screen.getByRole('button', { name: '平台状态：服务正常' })).toBeTruthy()
  })

  it('黄灯不渲染重启入口', () => {
    render(
      <PlatformStatusLightView
        api={fakeApi({ light: 'degraded', health: health({ db_ok: false }) })}
      />,
    )
    expect(screen.queryByRole('button', { name: /重启平台/ })).toBeNull()
    expect(screen.getByRole('button', { name: '平台状态：服务降级' })).toBeTruthy()
  })

  it('红灯出现重启按钮，且要二次确认才真正重启', () => {
    const api = fakeApi({ light: 'offline', health: health({ status: 'offline', db_ok: null }) })
    render(<PlatformStatusLightView api={api} />)

    const buttons = screen.getAllByRole('button', { name: '重启平台' })
    expect(buttons.length).toBeGreaterThanOrEqual(1)

    fireEvent.click(buttons[buttons.length - 1])
    expect(api.restart).not.toHaveBeenCalled()
    expect(screen.getByText('确认重启平台？正在编辑的内容可能丢失。')).toBeTruthy()

    fireEvent.click(screen.getByText('确认重启'))
    expect(api.restart).toHaveBeenCalledTimes(1)
  })

  it('浮层展示运行时长 / 版本 / 数据库连通', () => {
    render(<PlatformStatusLightView api={fakeApi()} />)
    fireEvent.mouseEnter(screen.getByRole('button', { name: '平台状态：服务正常' }))

    expect(screen.getByText('已运行')).toBeTruthy()
    expect(screen.getByText('42 秒')).toBeTruthy()
    expect(screen.getByText('0.1.0')).toBeTruthy()
    expect(screen.getByText('可读')).toBeTruthy()
  })

  it('重启失败的原因如实展示，不静默', () => {
    render(
      <PlatformStatusLightView
        api={fakeApi({ light: 'offline', restartError: '服务在 90 秒内未恢复' })}
      />,
    )
    fireEvent.mouseEnter(screen.getByRole('button', { name: '平台状态：服务离线' }))
    expect(screen.getByRole('alert').textContent).toContain('90 秒内未恢复')
  })
})

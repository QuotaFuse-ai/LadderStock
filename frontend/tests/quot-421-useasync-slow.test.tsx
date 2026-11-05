// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAsync } from '../src/lib/useAsync'

/**
 * QUOT-421 返工：慢响应下 30 秒轮询不能打断在途请求（自我 abort），
 * `refreshing` 必须在 settle 后复位；到点的那一拍排队，当前请求结束后补发。
 */

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (cause: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function Harness({ loader }: { loader: (signal: AbortSignal) => Promise<string> }) {
  const request = useAsync(loader, [])
  const { refresh } = request
  useEffect(() => {
    const timer = window.setInterval(() => refresh(), 30_000)
    return () => window.clearInterval(timer)
  }, [refresh])
  return (
    <div>
      <span data-testid="value">{request.data ?? 'none'}</span>
      <span data-testid="refreshing">{request.refreshing ? 'yes' : 'no'}</span>
    </div>
  )
}

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('useAsync 慢响应轮询', () => {
  it('在途请求不被新一拍 abort，结束后补一拍且 refreshing 复位', async () => {
    const slow = deferred<string>()
    const signals: AbortSignal[] = []
    let call = 0
    const loader = (signal: AbortSignal) => {
      signals.push(signal)
      call += 1
      if (call === 1) return Promise.resolve('v1')
      if (call === 2) return slow.promise
      return Promise.resolve('v2')
    }

    render(<Harness loader={loader} />)
    await tick(0)
    expect(screen.getByTestId('value').textContent).toBe('v1')

    // 30s 一拍：第二个请求发出但一直不回来
    await tick(30_000)
    expect(call).toBe(2)
    expect(screen.getByTestId('refreshing').textContent).toBe('yes')

    // 60s 又一拍：不 abort 在途请求，只排队
    await tick(30_000)
    expect(call).toBe(2)
    expect(signals[1].aborted).toBe(false)
    expect(screen.getByTestId('refreshing').textContent).toBe('yes')

    // 慢请求回来：新值上屏、刷新态复位、排队的下一拍补发
    await act(async () => {
      slow.resolve('v2')
      await slow.promise
    })
    await tick(0)

    expect(screen.getByTestId('value').textContent).toBe('v2')
    expect(screen.getByTestId('refreshing').textContent).toBe('no')
    expect(call).toBe(3)
    await tick(0)
    expect(screen.getByTestId('refreshing').textContent).toBe('no')
  })

  it('在途请求失败后 refreshing 也会复位，不会永久卡住', async () => {
    const slow = deferred<string>()
    let call = 0
    const loader = () => {
      call += 1
      if (call === 1) return Promise.resolve('v1')
      return slow.promise
    }

    render(<Harness loader={loader} />)
    await tick(0)
    expect(screen.getByTestId('value').textContent).toBe('v1')

    await tick(30_000)
    expect(screen.getByTestId('refreshing').textContent).toBe('yes')

    await act(async () => {
      slow.reject(new Error('slow failure'))
      await Promise.resolve()
    })
    await tick(0)

    // 旧值保留，刷新态复位（下一拍仍会继续重试）
    expect(screen.getByTestId('value').textContent).toBe('v1')
    expect(screen.getByTestId('refreshing').textContent).toBe('no')
  })
})

// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPane } from '../src/screens/home/shell/ChatPane'
import { WaitStatus } from '../src/screens/home/shell/WaitStatus'
import {
  STATUS_STAGES,
  fallbackDisplay,
  mergeFrame,
  stageNumber,
  toDisplay,
  type StatusFrame,
  type TurnStatusFeed,
} from '../src/screens/home/shell/turnStatus'

/**
 * QUOT-585：首页对话等待态 —— L1 阶段指示 + L2 已用时跳动。
 *
 * 红线（E5）：任意时刻连续 2 秒无任何视觉变化 = 违规。
 * 后端 `event: status`（QUOT-581）未落地：接口按契约形状写死，帧源可注入 mock。
 */

function jsonOk(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

/** 可手工驱动帧的最小 mock feed（不依赖真实后端） */
function makeFeed(): TurnStatusFeed & { emit: (frame: StatusFrame) => void } {
  const listeners = new Set<(frame: StatusFrame) => void>()
  return {
    emit(frame) {
      listeners.forEach((listener) => listener(frame))
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
  }
}

/** fake timers 不推进 performance.now：用可控 spy 让已用时能真实累加 */
function useFakeClock(startNow = 10_000) {
  vi.useFakeTimers()
  const nowSpy = vi.spyOn(performance, 'now')
  let current = startNow
  nowSpy.mockImplementation(() => {
    current += 50
    return current
  })
  return nowSpy
}

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      jsonOk({
        session: { session_id: 's-1', title: '', created_at: '', updated_at: '' },
        turns: [],
      }),
    ),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('turnStatus 帧归并（QUOT-585）', () => {
  it('stage 映射阶段序号：received=1 … generating=4，未知 stage 兜底 1', () => {
    expect(stageNumber('received')).toBe(1)
    expect(stageNumber('reading_data')).toBe(2)
    expect(stageNumber('computing')).toBe(3)
    expect(stageNumber('generating')).toBe(4)
    expect(stageNumber('unknown')).toBe(1)
  })

  it('普通帧覆盖文案与阶段；heartbeat 只刷新计时基准不改文案', () => {
    const base = toDisplay({ stage: 'computing', label: '正在计算', seq: 2, elapsed_ms: 1200 }, 1000)
    const beat = mergeFrame(
      base,
      { stage: 'generating', label: '被忽略的文案', seq: 3, elapsed_ms: 2400, heartbeat: true },
      2000,
    )
    expect(beat.label).toBe('正在计算')
    expect(beat.stageNo).toBe(3)
    expect(beat.baseElapsedMs).toBe(2400)
    expect(beat.baseAt).toBe(2000)
  })

  it('崩序帧（seq 回退）丢弃，不把阶段打回去', () => {
    const base = toDisplay({ stage: 'generating', label: '正在生成', seq: 5, elapsed_ms: 9000 }, 1000)
    const stale = mergeFrame(
      base,
      { stage: 'received', label: '已收到，正在处理', seq: 4, elapsed_ms: 100 },
      2000,
    )
    expect(stale).toBe(base)
  })

  it('label 缺失时按 stage 给默认中文文案', () => {
    const display = toDisplay({ stage: 'reading_data', label: '', seq: 1, elapsed_ms: 0 }, 0)
    expect(display.label.length).toBeGreaterThan(0)
  })
})

describe('WaitStatus 组件（QUOT-585）', () => {
  it('出现即显示阶段文案与「已用时 · 第 n/m 步」，计时走本地 100ms tick', () => {
    useFakeClock()
    vi.setSystemTime(0)
    const feed = makeFeed()
    render(<WaitStatus feed={feed} startedAt={1000} compact={false} />)

    expect(screen.getByTestId('wait-status-label').textContent).toBe('已收到，正在处理')
    expect(screen.getByTestId('wait-status-meta').textContent).toContain('第 1/4 步')

    // 300ms 后（E1 死线内）数字必须已经变化（本地计时器兜底，后端零帧）
    const before = screen.getByTestId('wait-status-meta').textContent
    act(() => {
      vi.advanceTimersByTime(300)
    })
    const after = screen.getByTestId('wait-status-meta').textContent
    expect(after).not.toBe(before)
    expect(after).toMatch(/已用时 \d+\.\ds/)
  })

  it('status 帧驱动：label 与阶段序号随帧前进（seq 升序）', () => {
    useFakeClock()
    const feed = makeFeed()
    render(<WaitStatus feed={feed} startedAt={0} compact={false} />)

    act(() => {
      feed.emit({ stage: 'computing', label: '正在计算', seq: 2, elapsed_ms: 3600 })
    })
    expect(screen.getByTestId('wait-status-label').textContent).toBe('正在计算')
    expect(screen.getByTestId('wait-status-meta').textContent).toContain('第 3/4 步')

    act(() => {
      feed.emit({ stage: 'generating', label: '正在生成', seq: 3, elapsed_ms: 7200 })
    })
    expect(screen.getByTestId('wait-status-label').textContent).toBe('正在生成')
    expect(screen.getByTestId('wait-status-meta').textContent).toContain('第 4/4 步')
  })

  it('heartbeat 帧不改文案，只让已用时跳；崩序帧无效', () => {
    useFakeClock()
    const feed = makeFeed()
    render(<WaitStatus feed={feed} startedAt={0} compact={false} />)

    act(() => {
      feed.emit({ stage: 'reading_data', label: '正在取数', seq: 1, elapsed_ms: 500 })
    })
    const labelBefore = screen.getByTestId('wait-status-label').textContent

    act(() => {
      feed.emit({ stage: 'generating', label: '干扰文案', seq: 2, elapsed_ms: 1500, heartbeat: true })
    })
    expect(screen.getByTestId('wait-status-label').textContent).toBe(labelBefore)

    // 崩序：seq 0 的旧帧不能把阶段打回去
    act(() => {
      feed.emit({ stage: 'received', label: '已收到，正在处理', seq: 0, elapsed_ms: 10 })
    })
    expect(screen.getByTestId('wait-status-label').textContent).toBe(labelBefore)
  })

  it('compact 窄条：单行、仍在计时、首块后不占视觉主位', () => {
    useFakeClock()
    render(<WaitStatus feed={null} startedAt={0} compact />)
    const node = screen.getByTestId('wait-status')
    expect(node.tagName).toBe('P')
    expect(node.textContent).toContain('已用时')
    const before = node.textContent
    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(screen.getByTestId('wait-status').textContent).not.toBe(before)
  })

  it('hidden 时不渲染；卸载后注销订阅', () => {
    const feed = makeFeed()
    let unsubscribed = false
    feed.subscribe = ((original) => (listener) => {
      const off = original(listener)
      return () => {
        unsubscribed = true
        off()
      }
    })(feed.subscribe.bind(feed))
    const { unmount } = render(<WaitStatus feed={feed} startedAt={0} compact={false} hidden />)
    expect(screen.queryByTestId('wait-status')).toBeNull()
    unmount()
    expect(unsubscribed).toBe(true)
  })
})

describe('ChatPane 等待态（QUOT-585 · E1/E2/E5）', () => {
  function renderPane() {
    return render(
      <MemoryRouter initialEntries={['/']}>
        <ChatPane
          activeSessionId={null}
          onOpenResearch={() => {}}
          onAskDesk={() => {}}
          onRoundComplete={() => {}}
        />
      </MemoryRouter>,
    )
  }

  it('发送后状态条出现（≤300ms 内已渲染且计时在跳），替代静态「生成中…」', async () => {
    useFakeClock()
    renderPane()
    const textarea = screen.getByLabelText('向对话提问') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '看看茅台' } })
    fireEvent.submit(screen.getByTestId('composer'))

    // E1：状态条必须立刻出现（fake timers 下同步渲染已完成）
    expect(screen.getByTestId('wait-status')).toBeTruthy()
    expect(screen.queryByText('生成中…')).toBeNull()
    // 已用时开始跳（本地 100ms tick）
    const before = screen.getByTestId('wait-status-meta').textContent
    act(() => {
      vi.advanceTimersByTime(500)
    })
    expect(screen.getByTestId('wait-status-meta').textContent).not.toBe(before)
  })

  it('阶段序列完整覆盖四步（STATUS_STAGES 契约不变）', () => {
    expect(STATUS_STAGES).toEqual(['received', 'reading_data', 'computing', 'generating'])
    expect(fallbackDisplay(0).stageTotal).toBe(4)
  })
})

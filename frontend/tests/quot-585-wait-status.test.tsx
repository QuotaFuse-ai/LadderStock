// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPane } from '../src/screens/home/shell/ChatPane'
import { ActivityFeed } from '../src/screens/home/shell/ActivityFeed'
import {
  applyFrame,
  EMPTY_FEED,
  TICK_INTERVAL_MS,
  type StatusFrame,
  type TurnStatusFeed,
} from '../src/screens/home/shell/turnStatus'

/**
 * QUOT-585 规格修正：动作流水账 —— 一行一个动作，实时追加，
 * 同一条原地从"进行中"变"完成"。
 *
 * 验收红线：
 * - 同 item_id 原地更新不重复，行数 == 不同 item_id 数量
 * - 完成后折叠成「共 N 步 · x.xs」
 * - 零帧注入：已用时每 100ms 仍持续变化（E5）
 * - reduced-motion：running 行仍有静态符号可辨认
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

const frame = (over: Partial<StatusFrame>): StatusFrame => ({
  stage: 'context',
  action: 'context',
  item: '正在读取行情与资金数据',
  state: 'running',
  item_id: 'c1',
  seq: 0,
  elapsed_ms: 340,
  ...over,
})

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

describe('turnStatus.applyFrame（QUOT-585 流水账数据层）', () => {
  it('同 item_id 的 done 帧原地更新 running 行，不新增行', () => {
    let state = applyFrame(EMPTY_FEED, frame({ item_id: 'c1', state: 'running', seq: 0 }))
    state = applyFrame(state, frame({ item_id: 'c1', state: 'done', item: '行情与资金数据已就绪', elapsed_ms: 980, seq: 1 }))
    expect(state.items).toHaveLength(1)
    expect(state.items[0]).toMatchObject({ itemId: 'c1', state: 'done', elapsedMs: 980, item: '行情与资金数据已就绪' })
  })

  it('新 item_id 追加到底部；行数 == 不同 item_id 数量', () => {
    let state = applyFrame(EMPTY_FEED, frame({ item_id: 'c1', seq: 0 }))
    state = applyFrame(state, frame({ item_id: 'c1', state: 'done', elapsed_ms: 62, seq: 1 }))
    state = applyFrame(state, frame({ item_id: 'm1', action: 'model', item: '正在调用模型 deepseek-v4.1-flash', seq: 2 }))
    state = applyFrame(state, frame({ item_id: 'c1', state: 'done', elapsed_ms: 62, seq: 3 }))
    expect(state.items.map((item) => item.itemId)).toEqual(['c1', 'm1'])
    expect(state.items).toHaveLength(2)
  })

  it('崩序帧（seq 回退）整帧丢弃', () => {
    let state = applyFrame(EMPTY_FEED, frame({ item_id: 'm1', action: 'model', item: '正在调用模型', seq: 5 }))
    state = applyFrame(state, frame({ item_id: 'c1', item: '旧帧', seq: 4 }))
    expect(state.items).toHaveLength(1)
    expect(state.items[0].itemId).toBe('m1')
  })

  it('failed 帧保留原因', () => {
    const state = applyFrame(EMPTY_FEED, frame({ item_id: 'x1', state: 'failed', item: '取数失败', detail: '行情服务不可达', seq: 1 }))
    expect(state.items[0]).toMatchObject({ state: 'failed', detail: '行情服务不可达' })
  })

  it('本地兜底计时步长是 100ms', () => {
    expect(TICK_INTERVAL_MS).toBe(100)
  })
})

describe('ActivityFeed 组件（QUOT-585）', () => {
  it('逐条追加：running 行带指示器，done 行带 ✓ 与耗时', () => {
    useFakeClock()
    const feed = makeFeed()
    render(<ActivityFeed feed={feed} startedAt={0} compact={false} />)

    act(() => {
      feed.emit(frame({ item_id: 'h1', action: 'history', item: '正在读取会话历史…', seq: 0 }))
      feed.emit(frame({ item_id: 'h1', action: 'history', item: '会话历史 3 条', state: 'done', elapsed_ms: 62, seq: 1 }))
      feed.emit(frame({ item_id: 'm1', action: 'model', item: '正在调用模型 deepseek-v4.1-flash', seq: 2 }))
    })

    const rows = screen.getAllByTestId('feed-item')
    expect(rows).toHaveLength(2)
    expect(rows[0].dataset.state).toBe('done')
    expect(rows[0].textContent).toContain('0.1s')
    expect(rows[0].textContent).toContain('✓')
    expect(rows[1].dataset.state).toBe('running')
    expect(rows[1].getAttribute('aria-busy')).toBe('true')
    expect(screen.getByTestId('feed-spinner')).toBeTruthy()
  })

  it('同 item_id 的 done 帧原地更新：行数不变、不重复插入', () => {
    useFakeClock()
    const feed = makeFeed()
    render(<ActivityFeed feed={feed} startedAt={0} compact={false} />)
    act(() => {
      feed.emit(frame({ item_id: 'c1', state: 'running', seq: 0 }))
      feed.emit(frame({ item_id: 'c1', state: 'done', item: '行情与资金数据已就绪', elapsed_ms: 640, seq: 1 }))
    })
    const rows = screen.getAllByTestId('feed-item')
    expect(rows).toHaveLength(1)
    expect(rows[0].dataset.state).toBe('done')
    expect(screen.queryByTestId('feed-spinner')).toBeNull()
  })

  it('失败行变红显示原因，流水账保留（能看到卡在哪一步）', () => {
    useFakeClock()
    const feed = makeFeed()
    render(<ActivityFeed feed={feed} startedAt={0} compact={false} />)
    act(() => {
      feed.emit(frame({ item_id: 'h1', action: 'history', item: '正在读取会话历史…', seq: 0 }))
      feed.emit(frame({ item_id: 'h1', state: 'failed', item: '会话历史读取失败', detail: '行情服务不可达', seq: 1 }))
    })
    const row = screen.getAllByTestId('feed-item')[0]
    expect(row.dataset.state).toBe('failed')
    expect(row.getAttribute('role')).toBe('alert')
    expect(row.textContent).toContain('行情服务不可达')
    expect(screen.getByTestId('feed-list')).toBeTruthy()
  })

  it('compact 收口：整块收成一行「共 N 步 · x.xs」，点开看明细', () => {
    useFakeClock()
    const feed = makeFeed()
    render(<ActivityFeed feed={feed} startedAt={0} compact totalElapsedMs={21_400} />)
    act(() => {
      feed.emit(frame({ item_id: 'c1', seq: 0 }))
      feed.emit(frame({ item_id: 'c1', state: 'done', elapsed_ms: 640, seq: 1 }))
      feed.emit(frame({ item_id: 'm1', action: 'model', item: '正在调用模型', seq: 2 }))
      feed.emit(frame({ item_id: 'm1', state: 'done', elapsed_ms: 20_760, seq: 3 }))
    })
    const summary = screen.getByTestId('feed-summary')
    expect(summary.textContent).toBe('共 2 步 · 21.4s')
    expect(screen.getByTestId('feed-list').className).toContain('hidden')

    fireEvent.click(summary)
    expect(screen.getAllByTestId('feed-item')).toHaveLength(2)
  })

  it('compact 且未点开时默认只显前 4 条，超出折叠并标 +N', () => {
    useFakeClock()
    const feed = makeFeed()
    render(<ActivityFeed feed={feed} startedAt={0} compact={false} />)
    act(() => {
      for (let i = 0; i < 6; i += 1) {
        feed.emit(frame({ item_id: `i${i}`, item: `动作 ${i}`, seq: i }))
      }
    })
    // 展开态：6 行都在
    expect(screen.getAllByTestId('feed-item')).toHaveLength(6)
  })

  it('零帧注入：后端完全不发 status，已用时仍每 100ms 持续变化（E5 兜底）', () => {
    useFakeClock()
    render(<ActivityFeed feed={null} startedAt={0} compact={false} />)
    const summary = screen.getByTestId('feed-summary')
    const before = summary.textContent
    act(() => {
      vi.advanceTimersByTime(300)
    })
    expect(summary.textContent).not.toBe(before)
    expect(summary.textContent).toMatch(/已用时 \d+\.\ds/)
  })

  it('reduced-motion：running 行仍有静态符号 ◐ 可辨认（CSS 隐藏由媒体查询处理，DOM 两者都在）', () => {
    useFakeClock()
    const feed = makeFeed()
    render(<ActivityFeed feed={feed} startedAt={0} compact={false} />)
    act(() => {
      feed.emit(frame({ item_id: 'm1', action: 'model', item: '正在调用模型', seq: 0 }))
    })
    expect(screen.getByTestId('feed-spinner')).toBeTruthy()
    expect(screen.getByTestId('feed-static-marker').textContent).toBe('◐')
  })

  it('卸载后注销订阅；hidden 时不渲染', () => {
    const feed = makeFeed()
    let unsubscribed = false
    const original = feed.subscribe.bind(feed)
    feed.subscribe = (listener) => {
      const off = original(listener)
      return () => {
        unsubscribed = true
        off()
      }
    }
    const { unmount } = render(<ActivityFeed feed={feed} startedAt={0} compact={false} hidden />)
    expect(screen.queryByTestId('activity-feed')).toBeNull()
    unmount()
    expect(unsubscribed).toBe(true)
  })
})

describe('ChatPane 等待态（QUOT-585 · E1/E5）', () => {
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

  it('发送后流水账出现（≤300ms 内已渲染且已用时在跳），替代静态「生成中…」', async () => {
    useFakeClock()
    renderPane()
    const textarea = screen.getByLabelText('向对话提问') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '看看茅台' } })
    fireEvent.submit(screen.getByTestId('composer'))

    expect(screen.getByTestId('activity-feed')).toBeTruthy()
    expect(screen.queryByText('生成中…')).toBeNull()
    const before = screen.getByTestId('feed-summary').textContent
    act(() => {
      vi.advanceTimersByTime(500)
    })
    expect(screen.getByTestId('feed-summary').textContent).not.toBe(before)
  })
})

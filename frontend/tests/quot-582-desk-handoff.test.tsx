// @vitest-environment jsdom
/**
 * QUOT-582 / F4：「问研究台」交接回归测。
 *
 * 对话区把问题带进研究面板的契约是**只填不发**：切到「研究股票」页签、
 * 填进底部唯一输入框、聚焦，用户回车才发——与起步问题同一口径。
 * 同时钉住「连续同一问」也能再次预填（消费后 prop 回 `null`，下次仍是
 * `null → text` 的真实变化，不该被一次性守卫拦掉）。
 */

import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HomeSearch } from '../src/screens/home/HomeSearch'
import type { HomeAssistantBundle } from '../src/screens/home/homeAssistant'

const EMPTY_ASSISTANT: HomeAssistantBundle = {
  context: {
    page: '首页',
    date: '2026-11-06',
    market: null,
    watchlist: null,
    watch: null,
    validation: null,
    unavailable: [],
  },
  sources: [],
}

const QUESTION = '浦发银行分红怎么看'

function renderPanel(initialQuestion: string | null, onQuestionConsumed: () => void) {
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route
          path="/"
          element={
            <HomeSearch
              assistant={EMPTY_ASSISTANT}
              date="2026-11-06"
              incomingQuestion={initialQuestion}
              onQuestionConsumed={onQuestionConsumed}
            />
          }
        />
        <Route path="*" element={<div />} />
      </Routes>
    </MemoryRouter>,
  )
}

const input = () => screen.getByRole('textbox', { name: '搜索或提问' }) as HTMLInputElement

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('F4 问研究台交接（只填不发）', () => {
  it('带来的一问切到「研究股票」页签、填进输入框并回调消费，不发出请求', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const consumed = vi.fn()

    renderPanel(QUESTION, consumed)

    // 页签切到了「研究股票」，问句填进底部唯一输入框
    const tab = await screen.findByRole('tab', { selected: true, name: /研究股票/ })
    expect(tab).toBeTruthy()
    expect(input().value).toBe(QUESTION)
    expect(document.activeElement).toBe(input())
    expect(consumed).toHaveBeenCalledTimes(1)

    await act(async () => {
      for (let i = 0; i < 8; i += 1) await Promise.resolve()
    })
    // 只填不发：AI 对话链路（askHomeAssistant）没有任何请求
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('消费后 prop 回 null，同一问再来第二次仍会重新预填', async () => {
    vi.stubGlobal('fetch', vi.fn())
    const consumed = vi.fn()

    const { rerender } = render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route
            path="/"
            element={
              <HomeSearch
                assistant={EMPTY_ASSISTANT}
                date="2026-11-06"
                incomingQuestion={QUESTION}
                onQuestionConsumed={consumed}
              />
            }
          />
          <Route path="*" element={<div />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('tab', { selected: true, name: /研究股票/ })
    expect(input().value).toBe(QUESTION)
    expect(consumed).toHaveBeenCalledTimes(1)

    // 用户在研究台改了输入 / 发出后，外壳把 prop 清回 null
    rerender(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route
            path="/"
            element={
              <HomeSearch
                assistant={EMPTY_ASSISTANT}
                date="2026-11-06"
                incomingQuestion={null}
                onQuestionConsumed={consumed}
              />
            }
          />
          <Route path="*" element={<div />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(input().value).toBe(QUESTION)

    // 同一条问再带一次：仍然预填（不被 lastIncomingQuestion 守卫拦掉）
    rerender(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route
            path="/"
            element={
              <HomeSearch
                assistant={EMPTY_ASSISTANT}
                date="2026-11-06"
                incomingQuestion={QUESTION}
                onQuestionConsumed={consumed}
              />
            }
          />
          <Route path="*" element={<div />} />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(consumed).toHaveBeenCalledTimes(2))
    expect(input().value).toBe(QUESTION)
  })
})

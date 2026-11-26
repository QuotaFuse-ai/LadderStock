// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import { BlockRenderer } from '../src/components/answer-blocks'

afterEach(cleanup)

/** F6：`unverified` 必须渲染成「数字未核实」提示，不能掉进未知 kind 的 internal 红块。 */
describe('NoticeBlock · unverified', () => {
  it('渲染成 warn 语气并原样列出数字', () => {
    const blocks = [
      {
        type: 'notice',
        payload: {
          kind: 'unverified',
          section: '数字核对',
          message: '以下数字没对回本轮工具返回：30.42。',
        },
      },
    ]
    render(
      <MemoryRouter>
        <BlockRenderer blocks={blocks as never} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/数字未核实/)).toBeTruthy()
    expect(screen.getByText(/30\.42/)).toBeTruthy()
    expect(screen.queryByText(/未归类失败/)).toBeNull()
  })
})

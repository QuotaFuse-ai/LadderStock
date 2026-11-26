// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import { BlockRenderer } from '../src/components/answer-blocks'
import { parseBlock } from '../src/components/answer-blocks/types'

/**
 * F5 翻转条件：结论卡多一行「出现即失效」，旧载荷（无该字段）不多渲染任何东西。
 */

afterEach(cleanup)

function conclusion(falsifier?: string) {
  return {
    type: 'conclusion',
    payload: {
      text: '短线偏强',
      tone: 'positive',
      confidence: null,
      ...(falsifier ? { falsifier } : {}),
    },
  }
}

describe('F5 翻转条件', () => {
  it('载荷带条件时渲染在结论卡里', () => {
    const blocks = [parseBlock(conclusion('收盘跌破 1400 元'))]
    const { container } = render(
      <MemoryRouter>
        <BlockRenderer blocks={blocks} />
      </MemoryRouter>,
    )
    const node = container.querySelector('[data-falsifier]')
    expect(node).not.toBeNull()
    expect(node?.textContent).toContain('收盘跌破 1400 元')
    expect(node?.textContent).toContain('翻转条件')
  })

  it('载荷没有该字段时整段不渲染', () => {
    const blocks = [parseBlock(conclusion())]
    const { container } = render(
      <MemoryRouter>
        <BlockRenderer blocks={blocks} />
      </MemoryRouter>,
    )
    expect(container.querySelector('[data-falsifier]')).toBeNull()
    // 也不许出现「暂无 / 未提供」这类占位
    expect(container.textContent).not.toContain('翻转条件')
  })

  it('解析只认字符串，空串与非字符串都视为没给', () => {
    for (const raw of ['', '   ', 123, null]) {
      const parsed = parseBlock(conclusion(raw as unknown as string))
      expect(parsed.type).toBe('conclusion')
      if (parsed.type === 'conclusion') {
        expect(parsed.payload.falsifier).toBeUndefined()
      }
    }
  })
})


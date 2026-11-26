// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PlanCard } from '../src/screens/home/shell/PlanCard'

afterEach(cleanup)

/** F2：计划可编辑——删一步、加一步、原样开始，三种出口都要能把最终清单交出去。 */
describe('PlanCard', () => {
  it('删掉一步后开始，交出的清单少一条', () => {
    const onStart = vi.fn()
    render(
      <PlanCard
        question="600519 值不值得买"
        steps={['确认标的', '拉取行情', '汇总结论']}
        maxSteps={6}
        onStart={onStart}
        onSkip={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByLabelText('移除「拉取行情」'))
    fireEvent.click(screen.getByText('开始研究'))
    expect(onStart).toHaveBeenCalledWith(['确认标的', '汇总结论'])
  })

  it('追加一步后开始，清单里带上新增项', () => {
    const onStart = vi.fn()
    render(
      <PlanCard
        question="600519 值不值得买"
        steps={['确认标的']}
        maxSteps={6}
        onStart={onStart}
        onSkip={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByText('再加一步'))
    fireEvent.change(screen.getByLabelText('追加一步'), { target: { value: '查股东户数' } })
    fireEvent.click(screen.getByText('加上'))
    fireEvent.click(screen.getByText('开始研究'))
    expect(onStart).toHaveBeenCalledWith(['确认标的', '查股东户数'])
  })

  it('「不用计划，直接问」走跳过分支', () => {
    const onSkip = vi.fn()
    render(
      <PlanCard
        question="600519 值不值得买"
        steps={['确认标的']}
        maxSteps={6}
        onStart={vi.fn()}
        onSkip={onSkip}
        onCancel={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByText('不用计划，直接问'))
    expect(onSkip).toHaveBeenCalled()
  })
})

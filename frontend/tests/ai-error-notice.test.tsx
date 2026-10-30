// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AiErrorNotice } from '../src/components/kit/AiErrorNotice'

afterEach(cleanup)

describe('AiErrorNotice · 四类失败', () => {
  it('network：危险色调，原因不与标题重复，重试与 AI 状态都可点', () => {
    const onRetry = vi.fn()
    const onOpenStatus = vi.fn()
    render(
      <AiErrorNotice
        kind="network"
        message="网络请求失败：连接超时（10s）"
        onRetry={onRetry}
        onOpenStatus={onOpenStatus}
      />,
    )

    const panel = screen.getByRole('alert')
    expect(panel.textContent).toContain('网络请求失败')
    expect(panel.textContent).toContain('连接超时（10s）')
    expect(panel.textContent).not.toContain('网络请求失败：连接超时')

    fireEvent.click(screen.getByRole('button', { name: '重试该段' }))
    expect(onRetry).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: '查看 AI 状态' }))
    expect(onOpenStatus).toHaveBeenCalledTimes(1)
  })

  it('quota：主操作是切换备用模型，重试是次操作', () => {
    const onRetry = vi.fn()
    const onSwitchModel = vi.fn()
    render(
      <AiErrorNotice
        kind="quota"
        message="额度或频率受限：HTTP 429"
        onRetry={onRetry}
        onSwitchModel={onSwitchModel}
      />,
    )

    expect(screen.getByRole('status').textContent).toContain('HTTP 429')

    fireEvent.click(screen.getByRole('button', { name: '切换备用模型' }))
    expect(onSwitchModel).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: '重试该段' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('format：解析失败给「重试该段」', () => {
    const onRetry = vi.fn()
    render(
      <AiErrorNotice
        kind="format"
        message="结构化输出解析失败：模型返回不是合法 JSON"
        onRetry={onRetry}
      />,
    )

    expect(screen.getByRole('alert').textContent).toContain('模型返回不是合法 JSON')
    fireEvent.click(screen.getByRole('button', { name: '重试该段' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('truncated：即使传了 onRetry 也不出重试按钮，只提示先调参数', () => {
    const onRetry = vi.fn()
    render(
      <AiErrorNotice
        kind="truncated"
        message="输出被截断：finish_reason=length"
        onRetry={onRetry}
        onOpenStatus={vi.fn()}
      />,
    )

    expect(screen.queryByRole('button', { name: /重试/ })).toBeNull()
    expect(screen.getByText(/原样重试仍会被截断/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '查看 AI 状态' })).toBeTruthy()
    expect(onRetry).not.toHaveBeenCalled()
  })
})

describe('AiErrorNotice · 兜底与降级', () => {
  it('internal：没有重试，按钮未接入时退回后端行动建议', () => {
    const onRetry = vi.fn()
    render(
      <AiErrorNotice
        kind="internal"
        message="AI 调用未归类失败：鉴权失败"
        action="检查环境变量中的密钥"
        onRetry={onRetry}
      />,
    )

    expect(screen.queryByRole('button', { name: /重试/ })).toBeNull()
    expect(screen.getByText(/检查环境变量中的模型密钥与权限/)).toBeTruthy()
    expect(screen.getByText('建议：检查环境变量中的密钥')).toBeTruthy()
    expect(onRetry).not.toHaveBeenCalled()
  })

  it('degraded：warn 胶囊、无操作时不渲染按钮', () => {
    render(<AiErrorNotice kind="degraded" keptPrevious />)

    expect(screen.getByRole('status').textContent).toContain('AI 叙述降级')
    expect(screen.getByText('已保留上一次结果')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('AiErrorNotice · 摘要详情', () => {
  it('长摘要默认折叠两行，「查看详情」可展开且 aria-expanded 同步', () => {
    const detail = '连接失败'.repeat(30)
    render(<AiErrorNotice kind="network" detail={detail} onRetry={vi.fn()} />)

    const toggle = screen.getByRole('button', { name: '查看详情' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')

    fireEvent.click(toggle)
    const collapse = screen.getByRole('button', { name: '收起详情' })
    expect(collapse.getAttribute('aria-expanded')).toBe('true')
  })

  it('短摘要不出「查看详情」', () => {
    render(<AiErrorNotice kind="network" detail="连接超时（10s）" />)
    expect(screen.queryByRole('button', { name: /查看详情|收起详情/ })).toBeNull()
  })
})

// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useSearchParams, useParams } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import ResearchEntryScreen from '../src/screens/research/ResearchEntryScreen'

/**
 * 个股深挖落地页（QUOT-300）：侧栏点进来 → 填 6 位代码 → 到 `/research/:code`，
 * 全程不需要手打 URL；非法代码只报错，不跳转、不编造数据。
 */

function Landing() {
  const { code } = useParams()
  const [params] = useSearchParams()
  return (
    <p data-testid="landed">
      已到达 {code} · demo={params.get('demo') ?? '-'} · date={params.get('date') ?? '-'}
    </p>
  )
}

afterEach(() => {
  cleanup()
})

function renderEntry(entry = '/research') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/research" element={<ResearchEntryScreen />} />
        <Route path="/research/:code" element={<Landing />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('个股深挖落地页 /research', () => {
  it('填代码提交后跳 /research/:code', () => {
    renderEntry()
    fireEvent.change(screen.getByLabelText(/^证券代码/), { target: { value: '600000' } })
    fireEvent.click(screen.getByRole('button', { name: '进入深挖' }))
    expect(screen.getByTestId('landed').textContent).toContain('已到达 600000')
  })

  it('带后缀的代码走规范化，回车也能提交', () => {
    renderEntry()
    const input = screen.getByLabelText(/^证券代码/)
    fireEvent.change(input, { target: { value: '000001.sz' } })
    fireEvent.submit(input)
    expect(screen.getByTestId('landed').textContent).toContain('已到达 000001.SZ')
  })

  it('非法代码给错误说明，停在原页', () => {
    renderEntry()
    fireEvent.change(screen.getByLabelText(/^证券代码/), { target: { value: '6000' } })
    fireEvent.click(screen.getByRole('button', { name: '进入深挖' }))
    expect(screen.getByRole('alert').textContent).toContain('代码不合法')
    expect(screen.queryByTestId('landed')).toBeNull()
  })

  it('演示与取证参数原样带到研究页', () => {
    renderEntry('/research?demo=1&date=2026-10-09')
    fireEvent.change(screen.getByLabelText(/^证券代码/), { target: { value: '600519' } })
    fireEvent.click(screen.getByRole('button', { name: '进入深挖' }))
    expect(screen.getByTestId('landed').textContent).toBe('已到达 600519 · demo=1 · date=2026-10-09')
  })

  it('给产业研究 / 产业信号留横向入口', () => {
    renderEntry()
    expect(screen.getByRole('link', { name: '产业研究矩阵' }).getAttribute('href')).toBe('/industry')
    expect(screen.getByRole('link', { name: '产业信号' }).getAttribute('href')).toBe('/signals')
  })
})

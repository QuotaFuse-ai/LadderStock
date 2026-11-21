// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import VerifyPanel from '../src/components/research/VerifyPanel'
import type { VerifyCapabilities, VerifyResult } from '../src/api/verify'

/**
 * F10 查证面板：
 * 1. 能力行如实标不可用 + 原因（本机无 OCR / 未配 vision）；
 * 2. 上传后按字段展示「图上 / 本地 / 判定」，正确率写清分子分母；
 * 3. 识别失败（ocr_unavailable）时说明缺什么，不画数字表格。
 */

const { fetchVerifyCapabilities, fetchVerifyHistory, verifyFile } = vi.hoisted(() => ({
  fetchVerifyCapabilities: vi.fn(),
  fetchVerifyHistory: vi.fn(),
  verifyFile: vi.fn(),
}))

vi.mock('../src/api/verify', () => ({ fetchVerifyCapabilities, fetchVerifyHistory, verifyFile }))

function caps(overrides: Partial<VerifyCapabilities> = {}): VerifyCapabilities {
  return {
    pdf_text: true,
    pdf_reason: '',
    image_vision: false,
    image_reason: '未配置图片识别后端：设 LDST_VISION_BASE_URL、LDST_VISION_MODEL',
    local_ocr: false,
    local_ocr_reason: '本机未装 tesseract / pdfplumber / fitz',
    supported_image_mime: ['image/png', 'image/jpeg'],
    tolerance: { price: 0.02 },
    max_bytes: 8 * 1024 * 1024,
    ...overrides,
  }
}

function result(overrides: Partial<VerifyResult> = {}): VerifyResult {
  return {
    ok: true,
    filename: 'sample.pdf',
    mime: 'application/pdf',
    sha1: 'abc',
    bytes: 1024,
    generated_at: '2026-11-21T15:00:00+08:00',
    extract: { status: 'ok', pages: 1, chars: 191, reason: '' },
    dates: ['2026-11-17'],
    figures: [
      { field: 'price', label: '价格', value: 1457.7, unit: '元', raw: '收盘价 1457.7 元', page: null },
    ],
    reconciled: {
      code: '600519',
      rows: [
        {
          field: 'price',
          label: '价格',
          doc_value: 1457.7,
          doc_unit: '元',
          doc_raw: '收盘价 1457.7 元',
          local_value: 1457.7,
          local_date: '2026-11-17',
          local_source: 'daily_k · close',
          verdict: 'match',
        },
        {
          field: 'pe',
          label: '市盈率 PE',
          doc_value: 22.1,
          doc_unit: '倍',
          doc_raw: '市盈率 22.1',
          local_value: null,
          local_date: '',
          local_source: 'valuation_history · pe_ttm',
          verdict: 'unverifiable',
          note: '本地没有该标的的这条数据',
        },
      ],
      comparable: 1,
      matched: 1,
      mismatched: 0,
      unverifiable: 1,
      accuracy: 1,
      accuracy_formula: 'matched / comparable',
    },
    text_preview: '...',
    saved: 'D:/data/verify/2026-11-21/abc.json',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  fetchVerifyHistory.mockResolvedValue([])
})

afterEach(() => {
  cleanup()
})

describe('VerifyPanel', () => {
  it('lists capabilities with reasons when unavailable', async () => {
    fetchVerifyCapabilities.mockResolvedValue(caps())
    render(<VerifyPanel />)

    await waitFor(() => expect(screen.getByText('截图 / PDF 查证')).toBeTruthy())
    expect(screen.getByText(/PDF 文本层/)).toBeTruthy()
    expect(screen.getByText(/图片识别（vision 后端）/)).toBeTruthy()
    expect(screen.getByText(/本机未装 tesseract/)).toBeTruthy()
    expect(screen.getByText(/LDST_VISION_BASE_URL/)).toBeTruthy()
  })

  it('renders reconcile rows with verdicts after upload', async () => {
    fetchVerifyCapabilities.mockResolvedValue(caps())
    verifyFile.mockResolvedValue(result())
    const { container } = render(<VerifyPanel />)

    await waitFor(() => expect(screen.getByText('上传并对账')).toBeTruthy())
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')
    expect(input).toBeTruthy()
    const file = new File(['%PDF-1.4'], 'sample.pdf', { type: 'application/pdf' })
    fireEvent.change(input as HTMLInputElement, { target: { files: [file] } })
    fireEvent.click(screen.getByText('上传并对账'))

    await waitFor(() => expect(screen.getByText(/正确率 100.0%/)).toBeTruthy())
    expect(screen.getByText(/标的 600519/)).toBeTruthy()
    expect(screen.getByText('一致')).toBeTruthy()
    expect(screen.getByText('无法核对')).toBeTruthy()
    expect(screen.getByText(/本地没有该标的的这条数据/)).toBeTruthy()
    expect(screen.getByText(/daily_k · close/)).toBeTruthy()
  })

  it('explains ocr_unavailable instead of showing figures', async () => {
    fetchVerifyCapabilities.mockResolvedValue(caps())
    verifyFile.mockResolvedValue(
      result({
        ok: false,
        filename: 'shot.png',
        mime: 'image/png',
        extract: {
          status: 'ocr_unavailable',
          pages: null,
          chars: null,
          reason: '未配置图片识别后端：设 LDST_VISION_BASE_URL、LDST_VISION_MODEL',
        },
        figures: [],
        reconciled: {
          code: null,
          rows: [],
          comparable: 0,
          matched: 0,
          mismatched: 0,
          unverifiable: 0,
          accuracy: null,
          accuracy_formula: 'matched / comparable',
        },
        saved: null,
      }),
    )
    const { container } = render(<VerifyPanel />)

    await waitFor(() => expect(screen.getByText('上传并对账')).toBeTruthy())
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')
    const file = new File(['png'], 'shot.png', { type: 'image/png' })
    fireEvent.change(input as HTMLInputElement, { target: { files: [file] } })
    fireEvent.click(screen.getByText('上传并对账'))

    await waitFor(() => expect(screen.getByText('无 OCR 能力')).toBeTruthy())
    expect(screen.getByText(/没有可用的文本层/)).toBeTruthy()
    // 不能出现对账明细
    expect(screen.queryByLabelText('对账明细')).toBeNull()
  })
})

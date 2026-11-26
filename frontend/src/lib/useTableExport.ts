/**
 * `useTableExport`（C 包第二半）：给任何「页面已有行集」的表格加导出按钮的 hook。
 *
 * 用法（组件内）：
 *   const exportCtl = useTableExport('持仓流水', rows)
 *   <Button {...exportCtl.buttonProps('csv')}>CSV</Button>
 *
 * 口径：
 * - 行集就是页面当前渲染的行（含筛选后的视图），不重新取数；
 * - 导出中禁用全部按钮（防双击双文件）；失败把人话交给页面展示（page 层 setError）；
 * - `only` 由调用方传列白名单（防内部字段随行带走）。
 */

import { useCallback, useState } from 'react'

import { describeApiError } from '../api'
import { downloadTableExport } from '../api/tableExport'

export interface UseTableExportResult {
  /** 挂到按钮上的 props（label 文案由调用方给） */
  buttonProps: (format: 'csv' | 'xlsx') => {
    onClick: () => void
    disabled: boolean
    title: string
  }
  /** 导出中的格式（按钮自旋用；null = 空闲） */
  busyFormat: 'csv' | 'xlsx' | null
  /** 最近一次导出失败的人话；null = 无 */
  error: string | null
  clearError: () => void
}

export function useTableExport(
  name: string,
  rows: Array<Record<string, unknown>>,
  only?: string[],
): UseTableExportResult {
  const [busyFormat, setBusyFormat] = useState<'csv' | 'xlsx' | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(
    async (format: 'csv' | 'xlsx') => {
      if (rows.length === 0) {
        setError('没有可导出的行（当前表格为空）')
        return
      }
      setError(null)
      setBusyFormat(format)
      try {
        await downloadTableExport({ name, format, rows, only })
      } catch (cause) {
        setError(describeApiError(cause).detail)
      } finally {
        setBusyFormat(null)
      }
    },
    [name, rows, only],
  )

  const buttonProps = useCallback(
    (format: 'csv' | 'xlsx') => ({
      onClick: () => void run(format),
      disabled: busyFormat !== null,
      title:
        format === 'xlsx'
          ? '下载 XLSX（需要 openpyxl）'
          : '下载 CSV（Excel 可直接打开）',
    }),
    [busyFormat, run],
  )

  const clearError = useCallback(() => setError(null), [])

  return { buttonProps, busyFormat, error, clearError }
}
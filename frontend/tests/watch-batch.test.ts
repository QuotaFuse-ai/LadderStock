import { describe, expect, it } from 'vitest'

import { planWatchBatch } from '../src/lib/watchBatch'

/**
 * 批量添加解析（QUOT-431）。
 *
 * 三条硬口径：
 * - 只收 6 位 A 股；港股 / 美股 / B 股逐条给原因，不猜市场、不静默丢弃；
 * - 输入内重复只落一条，已在清单里的进 `duplicates`，两者分开报；
 * - 输出顺序 = 输入顺序。
 */

describe('planWatchBatch', () => {
  it('空格 / 逗号 / 换行 / 顿号都能分隔，前缀后缀归一化成裸 6 位', () => {
    const plan = planWatchBatch('600519, 000858\n300750、sh600000；002594.SZ')
    expect(plan.accepted).toEqual(['600519', '000858', '300750', '600000', '002594'])
    expect(plan.rejected).toEqual([])
  })

  it('输入内重复只落一条，重复项单独列出', () => {
    const plan = planWatchBatch('600519 600519 SH.600519')
    expect(plan.accepted).toEqual(['600519'])
    expect(plan.repeated).toEqual(['600519', '600519'])
    expect(plan.rejected).toEqual([])
  })

  it('已在自选里的代码进 duplicates，不再写一次', () => {
    const plan = planWatchBatch('600519 000858', ['600519'])
    expect(plan.accepted).toEqual(['000858'])
    expect(plan.duplicates).toEqual(['600519'])
  })

  it('港股给出「5 位 / 港股不支持」而不是静默丢弃', () => {
    expect(planWatchBatch('00700').rejected).toEqual([{ token: '00700', reason: '港股代码是 5 位，暂不支持' }])
    expect(planWatchBatch('09988.HK').rejected[0]).toMatchObject({
      token: '09988.HK',
      reason: '港股暂不支持，只收 6 位 A 股',
    })
  })

  it('美股（字母代码 / .US 后缀）逐条拒绝', () => {
    expect(planWatchBatch('AAPL').rejected).toEqual([{ token: 'AAPL', reason: '美股代码（字母）暂不支持' }])
    expect(planWatchBatch('AAPL.US').rejected[0].reason).toBe('美股暂不支持，只收 6 位 A 股')
  })

  it('B 股与不在沪深京 A 股段的 6 位数字分开给原因', () => {
    expect(planWatchBatch('900901').rejected[0].reason).toBe('B 股暂不支持，只收 A 股')
    expect(planWatchBatch('200002').rejected[0].reason).toBe('B 股暂不支持，只收 A 股')
    expect(planWatchBatch('123456').rejected[0].reason).toBe('不是 A 股代码（6 位，但前缀不在沪深京 A 股范围）')
  })

  it('位数不对与无法识别的格式各自一句', () => {
    expect(planWatchBatch('6000').rejected[0].reason).toBe('代码位数不足 6 位')
    expect(planWatchBatch('6000000').rejected[0].reason).toBe('代码超过 6 位')
    expect(planWatchBatch('@@@@').rejected[0].reason).toBe('无法识别的代码格式')
  })

  it('可加入项按输入顺序返回，拒绝项也按输入顺序', () => {
    const plan = planWatchBatch('AAPL 300750 60000 600519')
    expect(plan.accepted).toEqual(['300750', '600519'])
    expect(plan.rejected.map((entry) => entry.token)).toEqual(['AAPL', '60000'])
  })

  it('空输入得到一份空计划，不报错', () => {
    expect(planWatchBatch('')).toEqual({ accepted: [], duplicates: [], repeated: [], rejected: [] })
    expect(planWatchBatch('   ,\n 、 ')).toEqual({ accepted: [], duplicates: [], repeated: [], rejected: [] })
  })
})

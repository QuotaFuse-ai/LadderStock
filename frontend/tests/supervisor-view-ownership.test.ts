import { describe, expect, it } from 'vitest'

import type { SupervisorModule } from '../src/api'
import { ownershipView } from '../src/lib/supervisorView'

function mod(overrides: Partial<SupervisorModule>): SupervisorModule {
  return {
    module: 'proxypool',
    ports: [],
    pid: null,
    health: 'ok',
    state: 'unknown',
    restarts_5min: 0,
    restart_count: 0,
    stopping_by_user: false,
    origin: null,
    cmdline_hash: null,
    last_exit_reason: null,
    self_heal: { attempts: 0, max: 3, status: 'ok' },
    ...overrides,
  }
}

describe('ownershipView（按需模块口径）', () => {
  it('disabled：未启用的按需模块显示「未启用（按需）」，不是故障也不是观测', () => {
    const view = ownershipView(mod({ state: 'disabled' }))
    expect(view.label).toBe('未启用（按需）')
    expect(view.tone).toBe('neutral')
    expect(view.hint).toContain('正常')
  })

  it('unknown 且无 pid：仍按「非托管（观测）」呈现，不夸大为故障', () => {
    const view = ownershipView(mod({ state: 'unknown' }))
    expect(view.label).toBe('非托管（观测）')
  })

  it('托管运行中：主程序托管', () => {
    const view = ownershipView(mod({ module: 'watch', state: 'running', pid: 1, origin: 'spawned', ownership: 'managed' }))
    expect(view.label).toBe('主程序托管')
  })
})
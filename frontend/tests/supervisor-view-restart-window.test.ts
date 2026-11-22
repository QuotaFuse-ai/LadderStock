import { describe, expect, it } from 'vitest'

import type { SupervisorModule } from '../src/api'
import { restartWindowNote } from '../src/lib/supervisorView'

function mod(restarts5min: number, restartCount: number): SupervisorModule {
  return {
    module: 'watch',
    ports: [],
    pid: 1,
    health: 'ok',
    state: 'running',
    restarts_5min: restarts5min,
    restart_count: restartCount,
    stopping_by_user: false,
    origin: 'spawned',
    cmdline_hash: null,
    last_exit_reason: null,
    self_heal: { attempts: 0, max: 3, status: 'ok' },
  }
}

describe('restartWindowNote（重启展示口径）', () => {
  it('窗口内 0 次不给提示——即使累计值很大', () => {
    expect(restartWindowNote(mod(0, 92))).toBeNull()
  })

  it('窗口 >0 时以「近 5 分钟」呈现，且不混入生命周期累计值', () => {
    const note = restartWindowNote(mod(2, 92))
    expect(note).toBe('近 5 分钟重启 2 次')
    expect(note).not.toContain('92')
  })

  it('模块为空时安全返回 null', () => {
    expect(restartWindowNote(null)).toBeNull()
  })
})
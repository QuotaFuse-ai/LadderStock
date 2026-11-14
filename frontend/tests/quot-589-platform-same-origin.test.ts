// QUOT-589 回归：健康/重启通道同源化——前端不得再猜看门狗端口。
//
// 根因：`platformBaseUrl()` 以前按 `import.meta.env.DEV` 分流，built dist 落在
// 5951 等非 5950 端口时跨源直打 prod 看门狗 5952，被 CORS 挡成「服务离线」，
// 「重启平台」还会错杀正式服 5950。修复后恒同源 `/platform/*`，由 serving
// backend 转发到本实例自己的看门狗。
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  PLATFORM_RESTART_TIMEOUT_MS,
  platformBaseUrl,
} from '../src/lib/usePlatformHealth'

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('platformBaseUrl 同源化（QUOT-589）', () => {
  it('恒返回空串（同源），无论 DEV 与否', () => {
    vi.stubEnv('DEV', true)
    expect(platformBaseUrl()).toBe('')

    vi.stubEnv('DEV', false)
    expect(platformBaseUrl()).toBe('')
  })

  it('不再出现 prod 看门狗 5952 硬编码', () => {
    expect(platformBaseUrl()).not.toContain('5952')
    expect(platformBaseUrl()).not.toContain('http')
  })

  it('重启超时常量语义不变（90s）', () => {
    expect(PLATFORM_RESTART_TIMEOUT_MS).toBe(90_000)
  })
})

import { describe, expect, it } from 'vitest'

import {
  WORKFLOW_PIPELINE,
  eventsOfModule,
  extractStageEvents,
  hasOkEvent,
  stageHealthView,
  stageLabel,
  type WorkflowStageEvent,
} from '../src/lib/workflowStages'

/**
 * QUOT-564：统一 stage 事件契约（B1/QUOT-562 定义，B2/QUOT-563 复用）。
 *
 * 锁死三件事：
 * 1. schema 字段/枚举严格校验，非法条目丢弃、绝不补默认值（不编数）；
 * 2. 两种信封（stage_events 快照 / events 类型化事件流）都能消费，非 stage 事件忽略；
 * 3. 健康翻译与粒子驱动是纯映射：ok 来自后端才流动，前端不推断、不聚合。
 */

function event(overrides: Partial<WorkflowStageEvent> = {}): WorkflowStageEvent {
  return {
    module: 'collect',
    module_class: 'data',
    stage: 'collect_script',
    health: 'ok',
    reason: '采集脚本已就绪',
    as_of: '2026-11-11T09:30:00+08:00',
    phase: '1',
    ...overrides,
  }
}

describe('extractStageEvents 信封与校验', () => {
  it('非对象 / 无事件字段 → 空数组（如实未接入，不补默认）', () => {
    expect(extractStageEvents(null)).toEqual([])
    expect(extractStageEvents(undefined)).toEqual([])
    expect(extractStageEvents('x')).toEqual([])
    expect(extractStageEvents({ ok: true, modules: [] })).toEqual([])
  })

  it('stage_events 快照信封：合法事件原样保留全部字段', () => {
    const events = extractStageEvents({ stage_events: [event(), event({ stage: 'collect_process', health: 'down', reason: '采集进程未运行' })] })
    expect(events).toHaveLength(2)
    expect(events[0]).toEqual(event())
    expect(events[1].health).toBe('down')
    expect(events[1].reason).toBe('采集进程未运行')
  })

  it('events 类型化信封：只收 type=stage，平铺与 payload 嵌套都认', () => {
    const events = extractStageEvents({
      events: [
        { type: 'crash', module: 'collect', reason: '崩溃' },
        { type: 'stage', ...event() },
        { type: 'stage', payload: event({ module: 'debate', module_class: 'cog', stage: 'debate_speak' }) },
        { type: 'recovered', module: 'ingest' },
      ],
    })
    expect(events).toHaveLength(2)
    expect(events[0].module).toBe('collect')
    expect(events[1].module).toBe('debate')
    expect(events[1].module_class).toBe('cog')
  })

  it('非法条目逐条丢弃：缺字段 / 枚举非法 / 类型错误都不渲染', () => {
    const events = extractStageEvents({
      stage_events: [
        event(),
        { ...event(), health: 'healthy' },
        { ...event(), module_class: 'unknown' },
        { ...event(), module: '' },
        { ...event(), stage: 123 },
        { ...event(), reason: null },
        { ...event(), as_of: '' },
        { ...event(), phase: 7 },
        'not-an-object',
        null,
      ],
    })
    expect(events).toHaveLength(1)
    expect(events[0]).toEqual(event())
  })

  it('同模块同阶段重复只取第一条；不同模块同阶段互不覆盖', () => {
    const events = extractStageEvents({
      stage_events: [
        event({ reason: '第一条' }),
        event({ reason: '第二条' }),
        event({ module: 'ingest', stage: 'collect_script', reason: '另一模块' }),
      ],
    })
    expect(events).toHaveLength(2)
    expect(events[0].reason).toBe('第一条')
  })
})

describe('eventsOfModule 过滤与 phase 排序', () => {
  it('只取本模块，按 phase 数字感知排序（2 在 10 前），同 phase 按 stage 稳定排序', () => {
    const events = [
      event({ module: 'ingest', stage: 'ingest_persist', phase: '2' }),
      event({ stage: 'collect_success', phase: '10' }),
      event({ stage: 'collect_process', phase: '2' }),
      event({ stage: 'collect_script', phase: '2' }),
    ]
    const sorted = eventsOfModule(events, 'collect')
    expect(sorted.map((e) => e.stage)).toEqual(['collect_process', 'collect_script', 'collect_success'])
  })

  it('无事件模块 → 空数组（等待事件，不编造）', () => {
    expect(eventsOfModule([event()], 'debate')).toEqual([])
  })
})

describe('健康翻译与粒子驱动（纯映射，不重算）', () => {
  it('stageHealthView：ok=正常 / warn=降级 / down=异常', () => {
    expect(stageHealthView('ok')).toEqual({ tone: 'ok', label: '正常' })
    expect(stageHealthView('warn')).toEqual({ tone: 'warn', label: '降级' })
    expect(stageHealthView('down')).toEqual({ tone: 'danger', label: '异常' })
  })

  it('hasOkEvent：有后端 ok 才流动；全 warn/down 或无事件一律静止', () => {
    expect(hasOkEvent([])).toBe(false)
    expect(hasOkEvent([event({ health: 'warn' }), event({ health: 'down' })])).toBe(false)
    expect(hasOkEvent([event({ health: 'down' }), event({ health: 'ok' })])).toBe(true)
  })

  it('stageLabel：契约阶段有白话名，未知阶段原样显示 id', () => {
    expect(stageLabel('collect_script')).toBe('采集脚本')
    expect(stageLabel('ingest_watermark')).toBe('同步水位')
    expect(stageLabel('debate_converge')).toBe('收敛')
    expect(stageLabel('collect_new_stage')).toBe('collect_new_stage')
  })

  it('管线第一批：采集→入库→辩论实线，召回→结论 P2 占位；蓝数据/紫认知', () => {
    expect(WORKFLOW_PIPELINE.map((n) => n.id)).toEqual(['collect', 'ingest', 'debate', 'recall', 'conclusion'])
    expect(WORKFLOW_PIPELINE.filter((n) => n.batch === 1).map((n) => n.id)).toEqual(['collect', 'ingest', 'debate'])
    expect(WORKFLOW_PIPELINE.find((n) => n.id === 'debate')?.moduleClass).toBe('cog')
    expect(WORKFLOW_PIPELINE.find((n) => n.id === 'collect')?.moduleClass).toBe('data')
  })
})
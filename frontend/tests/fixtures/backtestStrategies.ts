/**
 * `GET /api/backtest/strategies` 夹具（QUOT-535 契约形状）。
 *
 * 策略名故意用后端不会内置的 `demo_*`：页面若把任何策略名写死，测试会立刻暴露。
 * 成本默认值是券商万分之口径（万 0.9 / 免五 0.01 起收 / 过户费万 0.1 / 印花税万 5）。
 */
export const strategiesFixture = {
  strategies: [
    {
      name: 'demo_cross',
      label: '示例双均线',
      params: [
        { key: 'short_window', label: '短均线窗口', type: 'int', default: 5, min: 1, max: 250 },
        { key: 'long_window', label: '长均线窗口', type: 'int', default: 20, min: 2, max: 500 },
      ],
    },
    {
      name: 'demo_break',
      label: '示例通道突破',
      params: [
        { key: 'lookback', label: '通道窗口', type: 'int', default: 20, min: 1, max: 250 },
      ],
    },
    {
      name: 'demo_threshold',
      label: '示例阈值策略',
      params: [
        { key: 'threshold', label: '触发阈值', type: 'float', default: 0.01, min: -0.5, max: 0.5 },
      ],
    },
  ],
  config_defaults: {
    commission: 0.00009,
    slippage: 0.001,
    stamp_duty: 0.0005,
    min_trades: 20,
    in_sample_ratio: 0.7,
    cost_tiers: [0.0002, 0.0005, 0.001],
    commission_rate_wan: 0.9,
    commission_floor_yuan: 0.01,
    transfer_fee_wan: 0.1,
    stamp_duty_wan: 5,
    presets: [
      {
        key: 'wan09_free5_001',
        label: '万0.9 免五 0.01 元起收',
        commission_rate_wan: 0.9,
        commission_floor_yuan: 0.01,
      },
      {
        key: 'wan085_free5_1',
        label: '万0.85 免五 1 元起收',
        commission_rate_wan: 0.85,
        commission_floor_yuan: 1,
      },
      {
        key: 'wan065_min5',
        label: '万0.65 不免五 5 元起收',
        commission_rate_wan: 0.65,
        commission_floor_yuan: 5,
      },
    ],
    config_sources: {
      commission: 'yaml',
      commission_rate_wan: 'yaml',
      commission_floor_yuan: 'yaml',
      transfer_fee_wan: 'yaml',
      stamp_duty_wan: 'yaml',
      slippage: 'yaml',
      min_trades: 'yaml',
      in_sample_ratio: 'yaml',
      cost_tiers: 'yaml',
    },
  },
  symbol_default: '600000',
}

/** 提交成功（202）后的任务视图：直接成功、校验门通过。 */
export const succeededJobFixture = {
  job_id: 'a1b2c3d4e5f60718293a4b5c6d7e8f90',
  status: 'ok',
  run_id: 'bt_9f00c0de',
  gate_passed: true,
  exit_code: 0,
  error: null,
  strategy: 'demo_cross',
  strategy_params: { short_window: 5, long_window: 20 },
  symbol: '600000',
  start: '2024-03-14',
  end: '2026-11-05',
  created_at: '2026-11-10T16:00:00+00:00',
  started_at: '2026-11-10T16:00:00+00:00',
  ended_at: '2026-11-10T16:00:02+00:00',
}

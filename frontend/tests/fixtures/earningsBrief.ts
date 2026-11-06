/**
 * QUOT-450 测试夹具：600000 的 `earnings-brief` 真实形状（QUOT-446 契约）。
 *
 * 数字取自 2026-11-05 本机接口原值：一致预期 2026E 1.53（1.45 ~ 1.67，12 家），
 * 2026Q3 实际 EPS 1.17 / 营收 1,381.33 亿 / 归母净利 405.72 亿；
 * 发布日、反应日、当天涨跌与历史预期当前无源（0/6 期）。
 */
export function earningsPayload() {
  return {
    code: '600000',
    name: '浦发银行',
    periods: 6,
    consensus: {
      as_of: '2026-11-05',
      name: '浦发银行',
      current_year: 2026,
      eps_current_year: 1.53,
      eps_next_year: 1.64,
      analyst_count: 12,
      ratings: { buy: 3, overweight: 5 },
      consensus: [
        { year: 2026, analyst_count: 12, min: 1.45, mean: 1.53, max: 1.67, industry_avg: 1.91 },
        { year: 2027, analyst_count: 12, min: 1.49, mean: 1.64, max: 1.88, industry_avg: 2.05 },
      ],
      history: [],
    },
    consensus_reason: null,
    earnings: [
      {
        report_date: '2026-09-30',
        report_name: '2026三季报',
        actual: {
          eps: 1.17,
          bps: 22.8,
          revenue: 138133000000,
          net_profit: 40572000000,
          roe: 5.2,
          gross_margin: null,
          net_margin: 29.774927063,
          revenue_yoy: 4.42470517085,
          profit_yoy: 4.515829877122,
        },
        forecast: null,
        forecast_reason: '本地 earnings_preview 没有该报告期的业绩预告（该期未预告或未同步）',
        publish_date: null,
        publish_date_reason: 'finance_indicators 没有公告日字段，不拿报告期末或抓取日冒充',
        reaction_date: null,
        reaction_date_reason: '缺发布日，无法定位披露后的首个交易日',
        reaction_change_pct: null,
        reaction_change_pct_reason: '缺发布日，无法定位披露后的首个交易日',
      },
      {
        report_date: '2026-06-30',
        report_name: '2026中报',
        actual: {
          eps: null,
          bps: 22.63,
          revenue: 93777000000,
          net_profit: null,
          roe: 3.96,
          gross_margin: null,
          net_margin: 33.4346374911,
          revenue_yoy: 3.553484468689,
          profit_yoy: 4.082456199348,
        },
        forecast: null,
        forecast_reason: '本地 earnings_preview 没有该报告期的业绩预告（该期未预告或未同步）',
        publish_date: null,
        publish_date_reason: 'finance_indicators 没有公告日字段，不拿报告期末或抓取日冒充',
        reaction_date: null,
        reaction_date_reason: '缺发布日，无法定位披露后的首个交易日',
        reaction_change_pct: null,
        reaction_change_pct_reason: '缺发布日，无法定位披露后的首个交易日',
      },
    ],
    earnings_reason: null,
    stats: {
      periods: 6,
      with_forecast: 0,
      with_publish_date: 0,
      with_reaction: 0,
      latest_report_date: '2026-09-30',
      latest_report_eps: 1.17,
      consensus_analyst_count: 12,
      consensus_eps_current_year: 1.53,
      consensus_eps_next_year: 1.64,
    },
    data_source_status: 'ok',
  }
}

export function earningsEnvelope(overrides: Record<string, unknown> = {}) {
  return {
    payload: { ...earningsPayload(), ...overrides },
    data_source_status: 'ok',
    data_as_of: '2026-11-05',
    meta: {
      topic: 'research_earnings_brief',
      source: 'ladderstock',
      freshness: 'cached',
      fetched_at: '2026-11-05T17:56:55+00:00',
    },
  }
}

/**
 * 阶段 E 答案块 fixture：逐字抄自 QUOT-577 后端阶段的 `attachments/*.json`
 * （`conclusion / metric / chart / table / citation / notice / action`），
 * 以及 `full_answer.json` 里 `blocks` 那一串「同一组数据经 build_blocks 产出」的整轮答案。
 *
 * 后端改协议时同步改这里，前端测试才真的钉住「服务端给什么，页面就画什么」。
 */

export const CONCLUSION_BLOCK = {
  type: 'conclusion',
  payload: {
    text: '近二十日震荡偏弱，主力资金连续净流出',
    tone: 'negative',
    confidence: 0.62,
  },
} as const

export const METRIC_BLOCK = {
  type: 'metric',
  payload: {
    items: [
      { label: '收盘价', value: 38.62, unit: '元', as_of: '2026-11-11' },
      { label: '二十日涨跌', value: -4.3, unit: '%', direction: 'down', as_of: '2026-11-11' },
    ],
  },
} as const

export const CHART_BLOCK = {
  type: 'chart',
  payload: {
    kind: 'line',
    series: [
      {
        name: '收盘价',
        points: [
          ['2026-10-15', 39.8],
          ['2026-10-16', 39.5],
          ['2026-10-17', 38.9],
        ],
      },
    ],
    x_axis: '交易日',
    y_unit: '元',
    caption: '近二十个交易日收盘价',
  },
} as const

export const TABLE_BLOCK = {
  type: 'table',
  payload: {
    columns: ['指标', '2026Q3', '2025Q3'],
    rows: [['营业收入(亿)', 78.2, 71.4]],
    rows_total: 12,
    collapsed: true,
    caption: '近四季度营收',
  },
} as const

export const CITATION_BLOCK = {
  type: 'citation',
  payload: {
    items: [
      {
        name: '腾讯行情',
        fetched_at: '2026-11-11T15:02:01+08:00',
        url: 'https://example.com/quote/600036',
        symbol: '600036',
        as_of: '2026-11-11',
      },
    ],
  },
} as const

export const NOTICE_BLOCK = {
  type: 'notice',
  payload: {
    kind: 'missing_data',
    section: '分红',
    message: '本页没有取到该标的的分红记录',
  },
} as const

export const ACTION_BLOCK = {
  type: 'action',
  payload: {
    items: [{ label: '展开完整面板', action: 'open_screen', target: '/research?code=600036' }],
  },
} as const

/** 七个块各一份：截图与「一次全渲染」用这个顺序（与 §6.4 一致：结论在前、来源在后） */
export const ALL_BLOCKS = [
  CONCLUSION_BLOCK,
  METRIC_BLOCK,
  CHART_BLOCK,
  TABLE_BLOCK,
  NOTICE_BLOCK,
  ACTION_BLOCK,
  CITATION_BLOCK,
] as const

/** `full_answer.json` 的 `blocks`：3 指标 + 降级提示 ×2 + 无 symbol/as_of 的来源 */
export const FULL_ANSWER_BLOCKS = [
  {
    type: 'conclusion',
    payload: { text: '近二十日震荡偏弱，主力资金连续净流出。', tone: 'neutral' },
  },
  {
    type: 'metric',
    payload: {
      items: [
        { label: '情绪温度', value: 62.0, unit: '', direction: 'up', as_of: '2026-11-11' },
        { label: '晋级率', value: 41.5, unit: '%', direction: 'up', as_of: '2026-11-11' },
        { label: '涨停家数', value: 53.0, unit: '家', direction: 'up', as_of: '2026-11-11' },
      ],
    },
  },
  {
    type: 'chart',
    payload: {
      kind: 'line',
      series: [
        {
          name: '断层档位',
          points: [
            ['2026-10-15', 1.0],
            ['2026-10-16', 2.0],
            ['2026-10-17', -1.0],
          ],
        },
      ],
      x_axis: '交易日',
      y_unit: '',
      caption: '断层档位走势',
    },
  },
  {
    type: 'table',
    payload: {
      columns: ['指标', '2026Q3', '2025Q3'],
      rows: [['营业收入(亿)', 78.2, 71.4]],
      rows_total: 1,
      collapsed: false,
      caption: '近四季度营收',
    },
  },
  {
    type: 'notice',
    payload: { kind: 'degraded', section: '风险提示', message: '跌破三十八元支撑' },
  },
  {
    type: 'notice',
    payload: { kind: 'degraded', section: '补充说明', message: '板块分化明显。\n关注量能变化' },
  },
  {
    type: 'citation',
    payload: {
      items: [
        {
          name: '腾讯行情',
          fetched_at: '2026-11-11T15:02:01+08:00',
          url: 'https://example.com/quote/600036',
          symbol: null,
          as_of: null,
        },
      ],
    },
  },
]

/** §8.3 折叠用例：6 行已下发、12 行真实总数 */
export const LONG_TABLE_BLOCK = {
  type: 'table',
  payload: {
    columns: ['指标', '2026Q3', '2025Q3'],
    rows: [
      ['营业收入(亿)', 78.2, 71.4],
      ['净利润(亿)', 12.4, 10.9],
      ['毛利率(%)', 31.2, 30.1],
      ['净利率(%)', 15.9, 15.3],
      ['ROE(%)', 11.4, 10.8],
      ['资产负债率(%)', 62.8, 63.5],
    ],
    rows_total: 12,
    collapsed: true,
    caption: '近四季度营收',
  },
} as const

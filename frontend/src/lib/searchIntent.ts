/**
 * 搜索输入的归一化与意图判定（QUOT-356）。
 *
 * 这里只回答「这一串是什么」，不回答「用户想要谁」：
 * 中文名既可能是个股名称也可能是行业 / 题材名，靠字符串区分不了，
 * 所以 `name` 意图同时放行「个股」和「行业 / 题材」两类源，由数据决定命中哪边。
 * 判定规则一旦写在这里，`searchCore` 就不允许再各自 re-parse 一遍。
 */

/** 全角 / 中文标点里的空白（`\s` 在 ES2022 已覆盖 U+3000 与 NBSP，这里显式列出便于回看） */
const SPACE_PATTERN = /[\s\u00a0\u3000]+/g
/** 代码写法差异：`600000.SH` / `600 000` / `600-000` 归一到同一串 */
const CODE_PUNCTUATION = /[\s._\-/]/g
const MARKET_PREFIX = /^(SH|SZ|BJ)/
const MARKET_SUFFIX = /(SH|SZ|BJ)$/
/** 6 位代码：前后都不能再粘连数字，避免把 `6000001` 切成 `600000` */
const CODE_IN_TEXT = /(?<!\d)\d{6}(?!\d)/g
const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]/

export type SearchIntent = 'empty' | 'code' | 'name' | 'keyword'

export interface NormalizedQuery {
  raw: string
  /** 归一化检索词：NFKC + 空白折叠 + 去首尾空格；大小写原样保留 */
  text: string
  /** 整串就是一个证券代码时的代码，否则 `null` */
  code: string | null
  /** 串内出现的全部 6 位代码（`600000 浦发` 这种混排也认） */
  codes: string[]
  /** 是否含中日韩文字 */
  cjk: boolean
  /** 空输入：调用方不得发任何请求 */
  empty: boolean
}

/** `normalizeQuery` 的入参声明：运行时可能是 `undefined` / 非字符串（输入框事件、URL 参数）。 */
type QueryInput = string | null | undefined

function foldSpaces(value: string): string {
  return value.replace(SPACE_PATTERN, ' ').trim()
}

function toHalfWidth(value: string): string {
  try {
    return value.normalize('NFKC')
  } catch {
    // 代理对不合法时 `normalize` 抛 RangeError：原文照用，不让搜索整条链路崩
    return value
  }
}

/**
 * `600000` / `sh600000` / `600000.SH` / ` 600 000` → `600000`；
 * 其余（含中文、含非 6 位数字）返回 `null`。
 */
function parseCodeWithMarket(value: string): string | null {
  const bare = toHalfWidth(value).toUpperCase().replace(CODE_PUNCTUATION, '')
  const stripped = bare.replace(MARKET_PREFIX, '').replace(MARKET_SUFFIX, '')
  return /^\d{6}$/.test(stripped) ? stripped : null
}

export function normalizeQuery(input: QueryInput): NormalizedQuery {
  const text = foldSpaces(toHalfWidth(String(input ?? '')))
  const code = parseCodeWithMarket(text)
  const codes = code ? [code] : [...text.matchAll(CODE_IN_TEXT)].map((match) => match[0])
  return {
    raw: String(input ?? ''),
    text,
    code,
    codes,
    cjk: CJK.test(text),
    empty: text.length === 0,
  }
}

/**
 * `code`     —— 输入就是一个 6 位代码：个股组必出一跳，资讯组改走服务端 `code=` 过滤。
 * `name`     —— 含中文：个股名称 / 行业 / 题材都可能是它，三类源一起打。
 * `keyword`  —— 其余（英文缩写、多词、非 6 位数字）：按关键词在名称与资讯正文里找。
 */
export function classifyIntent(query: NormalizedQuery): SearchIntent {
  if (query.empty) return 'empty'
  if (query.code) return 'code'
  if (query.cjk) return 'name'
  return 'keyword'
}

/**
 * 包含匹配打分：同一条记录在多个源里重复出现时，取最高分决定排序。
 * `0` 表示不匹配；大小写与空白都不敏感。
 */
export function matchScore(needle: string, haystack: string | null | undefined): number {
  const target = foldSpaces(String(haystack ?? '')).toLowerCase()
  const term = foldSpaces(needle).toLowerCase()
  if (!term || !target) return 0
  if (target === term) return 100
  if (target.startsWith(term)) return 80
  if (target.includes(term)) return 60
  // 用户输入「机器人 减速器」这类多词：全部词都出现才算命中
  const terms = term.split(' ')
  if (terms.length > 1 && terms.every((item) => target.includes(item))) return 50
  return 0
}

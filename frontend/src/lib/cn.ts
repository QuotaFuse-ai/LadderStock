/** 极简 className 合并：过滤掉 false / undefined，避免在每个组件里手写模板字符串。 */
export type ClassValue = string | false | null | undefined

export function cn(...values: ClassValue[]): string {
  return values.filter(Boolean).join(' ')
}

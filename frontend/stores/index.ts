/** 分片入口：import 一次即完成「注册到索引上报」的副作用。 */

export { useWatchSnapshot, selectFreshness, selectTrustedRows } from './watchSnapshot'
export type { SnapshotRow, WatchSnapshotState } from './watchSnapshot'

export { useReviewDraft, REVIEW_SECTIONS, EMPTY_SECTIONS, draftCharCount } from './reviewDraft'
export type { ReviewDraftState, ReviewSectionKey, ReviewSections } from './reviewDraft'

export {
  useValidationDraft,
  pendingConditionCount,
} from './validationDraft'
export type { ValidationCondition, ValidationDraftState } from './validationDraft'

export {
  STORE_META_LIST,
  STATE_BACKEND,
  STATE_INDEX_FILE,
  STATE_SCHEMA_VERSION,
  metaFor,
} from './registry'
export type { StoreMeta } from './registry'

import { useWatchSnapshot } from './watchSnapshot'
import { useReviewDraft } from './reviewDraft'
import { useValidationDraft } from './validationDraft'

/** 供启动流程 / 测试一次性拿到全部分片。 */
export const ALL_STORES = [useWatchSnapshot, useReviewDraft, useValidationDraft] as const

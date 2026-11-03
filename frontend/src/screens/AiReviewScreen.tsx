import { Link } from 'react-router-dom'
import { Sparkles } from 'lucide-react'

import { PageHeader } from '../components/PageShell'
import { Card, EmptyState } from '../components/ui'

/**
 * AI 复盘（`/review/ai`，QUOT-344 / DEC-046）。
 *
 * 复盘三屏里属于 AI 的那一屏：只放 AI 生成的段落，以及它们的降级与失败态。
 * 当日五段数据（情绪 / 资金 / 题材 / 龙虎榜 / 龙头）在每日复盘 `/review`，
 * 按周聚合的叙述在 AI 周报 `/weekly`——三屏同源，不再合成一屏。
 *
 * 本文件是 S1 的占位壳：导航与路由已可用、可深链，正文结构由同一 PR 链的 S2 落地。
 * S1 不预测内容，也不从 `/api/review` 猜渲染。
 */

export default function AiReviewScreen() {
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="AI复盘"
        subtitle="AI 生成的段落与逐段状态"
        badge={
          <span className="inline-flex h-5 items-center rounded-xs border border-border-strong px-2 text-caption text-text-secondary">
            正文待接入
          </span>
        }
      />
      <Card>
        <EmptyState
          icon={<Sparkles aria-hidden="true" />}
          title="这一屏还没有正文"
          description={
            <>
              「AI复盘」是复盘组拆出来的第三个入口，导航与地址栏已可达。逐段生成状态、降级原因与重试出口
              由同一条 PR 链的下一步交付——在此之前本页不伪造任何段落。
            </>
          }
          action={
            <Link
              to="/review"
              className="font-medium text-text underline underline-offset-2"
            >
              到每日复盘看当日五段
            </Link>
          }
        />
      </Card>
    </div>
  )
}

/** 知识制品的展示词表——标签文案与色系集中在这里，别散到各个组件里。 */
import type {
  InstanceStatus, ProductLifecycle, ReviewStatus, SubmissionOutcome, TrialVerdict,
} from '@/types/knowledgeProduct'

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

const LIFECYCLE: Record<ProductLifecycle, [string, TagType]> = {
  draft: ['草稿', 'info'],
  candidate: ['待审', 'warning'],
  trial: ['试用中', 'warning'],
  published: ['已发布', 'success'],
  superseded: ['已被替代', 'info'],
  retired: ['已退役', 'info'],
}

const REVIEW: Record<ReviewStatus, [string, TagType]> = {
  agent_submitted: ['待人审', 'warning'],
  human_confirmed: ['已确认', 'success'],
  unresolved: ['有冲突', 'danger'],
  rejected: ['已驳回', 'danger'],
}

const INSTANCE: Record<InstanceStatus, [string, TagType]> = {
  pending: ['待开始', 'info'],
  running: ['进行中', 'primary'],
  paused: ['已暂停', 'warning'],
  completed: ['已完成', 'success'],
  cancelled: ['已取消', 'info'],
  failed: ['失败', 'danger'],
}

const OUTCOME: Record<SubmissionOutcome, [string, TagType]> = {
  pending: ['处理中', 'info'],
  accepted: ['已接收', 'success'],
  rejected: ['已拒收', 'danger'],
  conflict: ['修订冲突', 'warning'],
}

const VERDICT: Record<TrialVerdict, [string, TagType]> = {
  passed: ['通过', 'success'],
  failed: ['未通过', 'danger'],
  inconclusive: ['不确定', 'warning'],
}

/** 发布门禁各项的人话标题。code 与后端 review.py 的常量一一对应。 */
export const GATE_LABELS: Record<string, string> = {
  owner_and_scope: '负责人与适用范围',
  evidence_traceable: '关键内容能回源',
  no_hidden_conflict: '冲突没有被掩盖',
  all_reviewed: '全部对象已人审',
  trial_passed: '有通过的试用记录',
  references_resolvable: '引用都能解析',
}

function pick<T extends string>(
  table: Record<T, [string, TagType]>, key: T, index: 0 | 1,
) {
  return table[key]?.[index] ?? (index === 0 ? key : 'info')
}

export const lifecycleLabel = (v: ProductLifecycle) => pick(LIFECYCLE, v, 0) as string
export const lifecycleTagType = (v: ProductLifecycle) => pick(LIFECYCLE, v, 1) as TagType
export const reviewLabel = (v: ReviewStatus) => pick(REVIEW, v, 0) as string
export const reviewTagType = (v: ReviewStatus) => pick(REVIEW, v, 1) as TagType
export const instanceLabel = (v: InstanceStatus) => pick(INSTANCE, v, 0) as string
export const instanceTagType = (v: InstanceStatus) => pick(INSTANCE, v, 1) as TagType
export const outcomeLabel = (v: SubmissionOutcome) => pick(OUTCOME, v, 0) as string
export const outcomeTagType = (v: SubmissionOutcome) => pick(OUTCOME, v, 1) as TagType
export const verdictLabel = (v: TrialVerdict) => pick(VERDICT, v, 0) as string
export const verdictTagType = (v: TrialVerdict) => pick(VERDICT, v, 1) as TagType

export const gateLabel = (code: string) => GATE_LABELS[code] ?? code

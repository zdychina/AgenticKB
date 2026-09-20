/**
 * 知识制品（52号）。后端在 mining 的 /api/knowledge-products/*。
 *
 * 制品不跨知识域（52号 D6），所以这些请求跟其它 mining 请求一样经当前域的代理走，
 * 不需要额外传 domain。
 */

export type ProductLifecycle =
  | 'draft' | 'candidate' | 'trial' | 'published' | 'superseded' | 'retired'

/** 对象的人审状态。``agent_submitted`` = 还没人看过，不可发布。 */
export type ReviewStatus = 'agent_submitted' | 'human_confirmed' | 'unresolved' | 'rejected'

export type ReviewDecision = 'approve' | 'reject'

export type TrialVerdict = 'passed' | 'failed' | 'inconclusive'

export type InstanceStatus =
  | 'pending' | 'running' | 'paused' | 'completed' | 'cancelled' | 'failed'

export type SubmissionOutcome = 'pending' | 'accepted' | 'rejected' | 'conflict'

export interface KnowledgeProduct {
  id: string
  product_type: string
  name: string
  purpose: string | null
  owner: string
  lifecycle_status: ProductLifecycle
  current_draft_revision: number | null
  released_revision: number | null
  created_at: string
  updated_at: string
}

export interface ScopeItemInput {
  document_id: string
  snapshot_id: string
  allowed_sections?: string[] | null
}

export interface ProductCreateBody {
  product_id: string
  product_type: string
  name: string
  purpose?: string | null
  fields?: Record<string, unknown>
  object_rules?: Record<string, unknown>
  examples?: unknown[]
  trial_questions?: unknown[]
  scope_items?: ScopeItemInput[]
}

export interface ProductObjectRow {
  product_id: string
  object_id: string
  revision_no: number
  type: string
  layer: string
  scope: 'product' | 'cross'
  name: string | null
  frontmatter_json: Record<string, unknown>
  storage_object_id: string | null
  review_status: ReviewStatus
}

export interface ObjectChange {
  object_id: string
  change: 'added' | 'modified' | 'removed'
  added_lines: number
  removed_lines: number
}

export interface RevisionDiff {
  added: string[]
  modified: string[]
  removed: string[]
  total: number
  changes: ObjectChange[]
}

export interface ObjectEdit {
  id: string
  object_id: string
  revision_no: number
  editor: string
  reason: string | null
  basis: string | null
  before_md: string | null
  after_md: string | null
  created_at: string
}

export interface ReviewRecord {
  id: string
  revision_no: number
  reviewer: string
  decision: 'approved' | 'rejected'
  notes: string | null
  per_object_json: Record<string, ReviewDecision>
  created_at: string
}

export interface TrialRecord {
  id: string
  revision_no: number
  question: string
  answer: string | null
  expected_points: string | null
  verdict: TrialVerdict
  comment: string | null
  tried_by: string
  created_at: string
}

/** 发布门禁的一条检查。``offenders`` 是具体卡住的对象/字段。 */
export interface GateCheck {
  code: string
  passed: boolean
  detail: string
  offenders: string[]
}

export interface PublishGate {
  passed: boolean
  checks: GateCheck[]
}

export interface CreationInstance {
  id: string
  product_id: string
  definition_revision: number
  base_draft_revision: number
  status: InstanceStatus
  harness_session_id: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface SubmissionRecord {
  submission_id: string
  instance_id: string
  based_on_draft_revision: number
  outcome: SubmissionOutcome
  written_revision: number | null
  accepted_count: number
  rejected_count: number
  receipt_json: Record<string, unknown>
  created_at: string
}

/** 起制作实例的返回。``token`` 明文**只在这一次**出现，此后任何接口都不回显。 */
export interface StartedInstance {
  instance: CreationInstance
  ticket: { ticket_id: string; token: string; expires_at: string }
}

// ── P7：制品运营 ──────────────────────────────────────────────────────────

export interface ProductDefinition {
  id: string
  product_id: string
  definition_revision: number
  fields_json: Record<string, unknown>
  object_rules_json: Record<string, unknown>
  examples_json: unknown[]
  trial_questions_json: unknown[]
  created_at: string
  created_by: string
}

export interface DefinitionUpdateBody {
  fields?: Record<string, unknown> | null
  object_rules?: Record<string, unknown> | null
  examples?: unknown[] | null
  trial_questions?: unknown[] | null
  /** 不传 = 沿用当前范围；传了即整体替换 */
  scope_items?: ScopeItemInput[] | null
}

export type IssueStatus = 'open' | 'triaged' | 'resolved' | 'rejected'

/** 处置方向而不是「已修复」一个布尔——区分开才知道下次该防哪一类。 */
export type ResolutionKind = 'source' | 'definition' | 'content' | 'agent_usage' | 'none'

export interface ProductIssue {
  id: string
  product_id: string
  used_revision: number | null
  object_id: string | null
  field_name: string | null
  task: string | null
  problem: string
  correction_basis: string | null
  reporter: string
  status: IssueStatus
  resolution_kind: ResolutionKind | null
  resolution_note: string | null
  resolved_by: string | null
  created_at: string
  updated_at: string
}

/** 资料变更的影响类别。``revoked`` 是 blocking——不能只挂待办继续暴露。 */
export type AlertKind = 'superseded' | 'deprecated' | 'revoked'

export interface AffectedField {
  product_id: string
  revision: number
  object_id: string
  field: string
  document_id: string
  snapshot_id: string
}

export interface SourceAlert {
  kind: AlertKind
  blocking: boolean
  snapshot_id: string
  document_id: string
  detail: string
  affected_count: number
  affected: AffectedField[]
}

export interface SourceAlertReport {
  alerts: SourceAlert[]
  blocking: boolean
}

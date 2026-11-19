/**
 * 本体管理类型（67号 §五/§六）——对齐 mining routes/ontology.py 响应形状。
 *
 * 字段口径：id/时间戳为后端 TEXT（UUID/ISO 字符串）；计数列是 db_ontology
 * 子查询别名（列表/详情端点附带，详情端点可能缺省 → 可选）。
 */

/** 对象来源（67号 §五 semantic_objects.source CHECK）。 */
export type OntologyObjectSource = 'manual' | 'import' | 'extract'

export interface OntologyLibrary {
  id: string
  domain: string
  name: string
  description: string | null
  created_by: string
  created_at: string
  updated_at: string
  /** 列表/详情端点附带的子查询计数。 */
  ontology_count?: number
  object_count?: number
  relation_count?: number
}

export interface OntologyAttributeDef {
  id: string
  name: string
  description: string | null
  sort_order: number
  created_at?: string
}

export interface Ontology {
  id: string
  library_id: string
  name: string
  description: string | null
  created_by?: string
  created_at: string
  updated_at?: string
  /** list_ontologies 子查询计数。 */
  attribute_count?: number
  object_count?: number
  relation_ref_count?: number
}

/** 本体详情 = 本体 + 属性定义 + 参与的关系类型（get_ontology 口径）。 */
export interface OntologyDetail extends Ontology {
  attributes: OntologyAttributeDef[]
  ontology_relations: Array<{
    id: string
    name: string
    description: string | null
    source_ontology_id: string
    target_ontology_id: string
    created_at?: string
  }>
}

export interface OntologyRelation {
  id: string
  library_id: string
  name: string
  description: string | null
  source_ontology_id: string
  target_ontology_id: string
  created_by?: string
  created_at: string
  updated_at?: string
  /** list 端点 JOIN 附带。 */
  source_ontology_name?: string
  target_ontology_name?: string
  instance_count?: number
}

export interface OntologyObjectRow {
  id: string
  library_id: string
  ontology_id: string
  name: string
  attributes_json: Record<string, unknown> | null
  source: OntologyObjectSource
  created_by: string
  created_at: string
  updated_at: string
  /** 列表 JOIN 附带。 */
  ontology_name?: string
  /** 69号抽取溯源：来源文档（LEFT JOIN asset_documents 滤软删——文档已删
   * 时 name/kb_id 为 null，id 仍在；软删恢复自动回显）。 */
  source_document_id?: string | null
  source_document_name?: string | null
  source_kb_id?: string | null
}

export interface OntologyObjectDetail extends OntologyObjectRow {
  /** 属性定义对齐（详情端点附带，编辑表单口径）。 */
  attribute_defs: OntologyAttributeDef[]
  outgoing_count: number
  incoming_count: number
}

export interface OntologyObjectsPage {
  items: OntologyObjectRow[]
  total: number
}

/** 一度邻接边（get_object_neighbors；出/入同构，other_* 为对端对象）。 */
export interface ObjectNeighborEdge {
  id: string
  ontology_relation_id: string
  relation_name: string
  other_id: string
  other_name: string
  other_ontology_id: string
  other_ontology_name: string
}

export interface ObjectNeighbors {
  outgoing: ObjectNeighborEdge[]
  incoming: ObjectNeighborEdge[]
}

export interface SemanticRelationRow {
  id: string
  library_id: string
  ontology_relation_id: string
  relation_name?: string
  source_object_id: string
  source_object_name?: string
  source_ontology_id?: string
  target_object_id: string
  target_object_name?: string
  target_ontology_id?: string
  created_at: string
}

export interface SemanticRelationsPage {
  items: SemanticRelationRow[]
  total: number
}

/** 库 schema 全量（get_library_schema：本体含属性 + 关系类型含端点名）。 */
export interface LibrarySchema {
  ontologies: Array<Ontology & {
    object_count?: number
    attributes: OntologyAttributeDef[]
  }>
  relations: Array<{
    id: string
    name: string
    description: string | null
    source_ontology_id: string
    target_ontology_id: string
    source_ontology_name?: string
    target_ontology_name?: string
  }>
}

// ── 实例图（68号 §2.2）──

/** 图节点：不带 attributes_json（点击后走 getObject 拉详情）。 */
export interface GraphNode {
  id: string
  name: string
  ontology_id: string
  ontology_name: string
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  ontology_relation_id: string
  relation_name: string
}

export interface LibraryGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** 过滤前库内对象总数（护栏横幅提示用）。 */
  total_objects: number
  truncated: boolean
}

// ── 域级对象搜索（69号 §4.1）──

/** 检索面板卡组命中：跨库按对象名匹配（只按名，不扫属性值）。 */
export interface OntologyObjectSearchHit {
  object_id: string
  object_name: string
  /** manual/import 来源为 null；extract 带抽取来源文档 id。 */
  source_document_id: string | null
  /** 截断兜底重拉用（带该本体过滤）。 */
  ontology_id: string
  library_id: string
  library_name: string
  ontology_name: string
  /** 一度邻接边数（出+入），卡片「N 关联」。 */
  degree: number
}

export interface OntologyObjectSearchResult {
  truncated: boolean
  hits: OntologyObjectSearchHit[]
}

// ── 批量导入（67号 §七 两步式）──

/** 上传解析的列元信息（首列样例值）。 */
export interface ImportColumnMeta {
  name: string
  sample: unknown
}

/** 列名与属性定义名精确匹配的自动预选（UI 可改）。 */
export interface ImportDetectedMapping {
  name_column: string
  attribute_columns: Record<string, string>
}

/** Step 1 上传解析响应（§7.2）。 */
export interface ImportUploadResult {
  import_token: string
  row_count: number
  columns: ImportColumnMeta[]
  preview_rows: Array<Record<string, unknown>>
  detected_mapping: ImportDetectedMapping
}

/** 422 逐行错误条目（§7.4：row 为 1 起数据行号）。 */
export interface ImportErrorItem {
  row: number
  code: string
  message: string
}

/** 422 校验报告（整批未导入）。 */
export interface ImportValidationReport {
  message: string
  total_rows: number
  valid_rows: number
  error_rows: number
  errors: ImportErrorItem[]
}

/** Step 2 提交成功响应。 */
export interface ImportCommitResult {
  ontology_id: string
  imported: number
}

// ── AI 辅助实例化（67号 §八）──

/** 对象候选（§8.5）：conflict=true 与库内同名对象冲突（UI 标黄）。 */
export interface ExtractObjectCandidate {
  name: string
  attributes: Record<string, unknown>
  conflict: boolean
  conflict_with: string | null
}

/** 关系候选：target 解析为候选（new）或库内对象（existing）；
 * endpoint_ok=false 仅影响 UI 提示（端点不匹配）。 */
export interface ExtractRelationCandidate {
  source: string
  relation: string
  target: string
  target_existing: boolean
  target_object_id: string | null
  endpoint_ok: boolean
}

/** done 状态的候选载荷（§8.5 输出契约）。 */
export interface ExtractCandidates {
  objects: ExtractObjectCandidate[]
  relations: ExtractRelationCandidate[]
  warnings: string[]
  input_chars: number
  truncated: boolean
}

/** 轮询响应（§8.3）：pending / done（candidates）/ failed（error）。 */
export type ExtractStatusBody =
  | { status: 'pending' }
  | { status: 'done'; candidates: ExtractCandidates }
  | { status: 'failed'; error: string }

/** 422 报告条目（§6.8：field 为 objects[i]/relations[j] 定位）。 */
export interface InstantiateErrorItem {
  field: string
  code: string
  message: string
}

/** 422 校验报告（整批未落库）。 */
export interface InstantiateValidationReport {
  message: string
  total_objects: number
  total_relations: number
  errors: InstantiateErrorItem[]
}

/** 提交成功响应。 */
export interface InstantiateResult {
  object_ids: string[]
  relation_ids: string[]
}

// ── 对象↔制品 refs（70号）──

/** refs 降级原因：target_missing=页/行已在 current revision 移除（对象侧）；
 * object_invisible=制品读者非对象域成员（制品侧反查的防御占位）。 */
export type ObjectRefDegraded = 'target_missing' | 'object_invisible' | null

/** 对象侧 refs 列表条目（GET /objects/{id}/refs）：跟随 current 现解析，
 * target_title 为 null 即整体级或降级条目。 */
export interface ObjectArtifactRef {
  id: string
  object_id: string
  artifact_id: string
  /** ''=制品整体；wiki=page_key；table=row_key（跨 revision 稳定）。 */
  target_key: string
  note: string
  ref_source: 'manual' | 'auto_compile' | 'auto_import'
  created_by: string
  created_at: string
  artifact_title: string
  kind: 'table' | 'wiki'
  artifact_status: 'draft' | 'published' | 'deprecated'
  artifact_domain_id: string
  current_revision: number
  target_title: string | null
  degraded: ObjectRefDegraded
}

export interface ObjectArtifactRefsPage {
  items: ObjectArtifactRef[]
  truncated: boolean
}

/** neighbors 对象卡 refs 摘要条目（UI 按用户可见性 / MCP 按钥匙开放子集）。 */
export interface ObjectRefSummaryEntry {
  artifact_id: string
  artifact_title: string
  kind: 'table' | 'wiki'
  target_key: string
  target_title: string | null
  artifact_status: 'draft' | 'published' | 'deprecated'
  degraded: ObjectRefDegraded
}

/** neighbors 响应（70号扩展）：邻接 + refs 摘要（上限 20 + truncated）。 */
export interface ObjectNeighborsWithRefs extends ObjectNeighbors {
  artifact_refs: ObjectRefSummaryEntry[]
  artifact_refs_truncated: boolean
}

/** 制品侧反查条目（GET /artifact-refs）：非对象域成员 object_name 为 null。 */
export interface ArtifactObjectRef {
  id: string
  object_id: string
  target_key: string
  note: string
  ref_source: 'manual' | 'auto_compile' | 'auto_import'
  created_by: string
  created_at: string
  object_name: string | null
  ontology_name: string | null
  /** 跳转库图谱聚焦用（69号 focus 链路）。 */
  library_id: string
  library_domain: string
  degraded: ObjectRefDegraded
}

export interface ArtifactObjectRefsPage {
  items: ArtifactObjectRef[]
  truncated: boolean
}

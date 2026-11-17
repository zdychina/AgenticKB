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

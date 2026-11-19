/**
 * 本体管理 API（67号 §六）—— 经 main_control 反向代理到 mining 服务。
 *
 * 走 createProxyClient('mining')：baseURL 每次请求按当前域重算
 * （/api/control-plane/api/v1/proxy/{domain}/mining）。路由自身不消费
 * domain 查询参数（域过滤走显式 domain 字段/库归属），对齐 artifacts.ts
 * 关掉自动 domain 追加；list_libraries 显式传当前域。
 * 错误文案统一由调用方用 apiErrorDetail 提取（后端 detail 为纯文本，
 * 409 InUse 消息自带阻塞计数）。
 */
import { createProxyClient } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import type {
  ArtifactObjectRef,
  ArtifactObjectRefsPage,
  ExtractStatusBody,
  ImportCommitResult,
  ImportUploadResult,
  InstantiateResult,
  LibrarySchema,
  ObjectArtifactRef,
  ObjectArtifactRefsPage,
  ObjectNeighbors,
  Ontology,
  OntologyAttributeDef,
  OntologyDetail,
  OntologyLibrary,
  OntologyObjectDetail,
  OntologyObjectRow,
  OntologyObjectsPage,
  OntologyRelation,
  ObjectNeighborsWithRefs,
  LibraryGraph,
  OntologyObjectSearchResult,
  SemanticRelationRow,
  SemanticRelationsPage,
} from '@/types/ontology'

export interface AttributeDefRequest {
  name: string
  description?: string
  sort_order?: number
}

export interface OntologyCreateRequest {
  name: string
  description?: string
  attributes?: AttributeDefRequest[]
}

export function useOntologyApi() {
  const client = createProxyClient('mining', { includeDomainQuery: false })
  const domainStore = useDomainStore()

  // ── 本体库 ──

  async function listLibraries(q?: string): Promise<OntologyLibrary[]> {
    const { data } = await client.get('/api/kb/ontology/libraries', {
      params: { domain: domainStore.currentDomain, ...(q ? { q } : {}) },
    })
    return data as OntologyLibrary[]
  }

  async function getLibrary(libraryId: string): Promise<OntologyLibrary> {
    const { data } = await client.get(`/api/kb/ontology/libraries/${libraryId}`)
    return data as OntologyLibrary
  }

  async function createLibrary(payload: {
    name: string
    description?: string
  }): Promise<OntologyLibrary> {
    const { data } = await client.post('/api/kb/ontology/libraries', {
      ...payload,
      domain: domainStore.currentDomain,
    })
    return data as OntologyLibrary
  }

  async function updateLibrary(
    libraryId: string, payload: { name?: string; description?: string },
  ): Promise<OntologyLibrary> {
    const { data } = await client.put(
      `/api/kb/ontology/libraries/${libraryId}`, payload)
    return data as OntologyLibrary
  }

  async function deleteLibrary(libraryId: string): Promise<void> {
    await client.delete(`/api/kb/ontology/libraries/${libraryId}`)
  }

  // ── 本体与属性 ──

  async function listOntologies(libraryId: string): Promise<Ontology[]> {
    const { data } = await client.get(
      `/api/kb/ontology/libraries/${libraryId}/ontologies`)
    return data as Ontology[]
  }

  async function getOntology(ontologyId: string): Promise<OntologyDetail> {
    const { data } = await client.get(`/api/kb/ontology/ontologies/${ontologyId}`)
    return data as OntologyDetail
  }

  async function createOntology(
    libraryId: string, payload: OntologyCreateRequest,
  ): Promise<Ontology> {
    const { data } = await client.post(
      `/api/kb/ontology/libraries/${libraryId}/ontologies`, payload)
    return data as Ontology
  }

  async function updateOntology(
    ontologyId: string, payload: { name?: string; description?: string },
  ): Promise<Ontology> {
    const { data } = await client.put(
      `/api/kb/ontology/ontologies/${ontologyId}`, payload)
    return data as Ontology
  }

  async function deleteOntology(ontologyId: string): Promise<void> {
    await client.delete(`/api/kb/ontology/ontologies/${ontologyId}`)
  }

  async function addAttribute(
    ontologyId: string, payload: AttributeDefRequest,
  ): Promise<OntologyAttributeDef> {
    const { data } = await client.post(
      `/api/kb/ontology/ontologies/${ontologyId}/attributes`, payload)
    return data as OntologyAttributeDef
  }

  async function updateAttribute(
    attributeId: string,
    payload: { name?: string; description?: string; sort_order?: number },
  ): Promise<OntologyAttributeDef> {
    const { data } = await client.put(
      `/api/kb/ontology/attributes/${attributeId}`, payload)
    return data as OntologyAttributeDef
  }

  async function deleteAttribute(attributeId: string): Promise<void> {
    await client.delete(`/api/kb/ontology/attributes/${attributeId}`)
  }

  // ── 本体关系（类型层）──

  async function listRelations(libraryId: string): Promise<OntologyRelation[]> {
    const { data } = await client.get(
      `/api/kb/ontology/libraries/${libraryId}/relations`)
    return data as OntologyRelation[]
  }

  async function createRelation(
    libraryId: string,
    payload: {
      name: string
      source_ontology_id: string
      target_ontology_id: string
      description?: string
    },
  ): Promise<OntologyRelation> {
    const { data } = await client.post(
      `/api/kb/ontology/libraries/${libraryId}/relations`, payload)
    return data as OntologyRelation
  }

  async function updateRelation(
    relationId: string,
    payload: {
      name?: string
      description?: string
      source_ontology_id?: string
      target_ontology_id?: string
    },
  ): Promise<OntologyRelation> {
    const { data } = await client.put(
      `/api/kb/ontology/relations/${relationId}`, payload)
    return data as OntologyRelation
  }

  async function deleteRelation(relationId: string): Promise<void> {
    await client.delete(`/api/kb/ontology/relations/${relationId}`)
  }

  // ── 语义对象（实例层）──

  async function listObjects(
    libraryId: string,
    params?: { ontology_id?: string; q?: string; limit?: number; offset?: number },
  ): Promise<OntologyObjectsPage> {
    const { data } = await client.get(
      `/api/kb/ontology/libraries/${libraryId}/objects`, { params })
    return data as OntologyObjectsPage
  }

  async function getObject(objectId: string): Promise<OntologyObjectDetail> {
    const { data } = await client.get(`/api/kb/ontology/objects/${objectId}`)
    return data as OntologyObjectDetail
  }

  async function createObject(
    ontologyId: string,
    payload: { name: string; attributes?: Record<string, unknown> },
  ): Promise<OntologyObjectRow> {
    const { data } = await client.post(
      `/api/kb/ontology/ontologies/${ontologyId}/objects`, payload)
    return data as OntologyObjectRow
  }

  async function updateObject(
    objectId: string,
    payload: { name?: string; attributes?: Record<string, unknown> },
  ): Promise<OntologyObjectRow> {
    const { data } = await client.put(
      `/api/kb/ontology/objects/${objectId}`, payload)
    return data as OntologyObjectRow
  }

  async function deleteObject(objectId: string): Promise<void> {
    await client.delete(`/api/kb/ontology/objects/${objectId}`)
  }

  /** 70号起响应附带 artifact_refs 摘要（按调用者可见性/MCP 钥匙开放子集过滤）。 */
  async function getObjectNeighbors(objectId: string): Promise<ObjectNeighborsWithRefs> {
    const { data } = await client.get(
      `/api/kb/ontology/objects/${objectId}/neighbors`)
    return data as ObjectNeighborsWithRefs
  }

  // ── 对象↔制品 refs（70号）──

  /** 对象的制品引用列表（服务端已按当前用户制品可见性整行过滤）。 */
  async function listObjectRefs(
    objectId: string, limit = 100,
  ): Promise<ObjectArtifactRefsPage> {
    const { data } = await client.get(
      `/api/kb/ontology/objects/${objectId}/refs`, { params: { limit } })
    return data as ObjectArtifactRefsPage
  }

  /** 建引用：target_key 缺省=制品整体级；422 附 valid_keys 样例/跨域码。 */
  async function createObjectRef(
    objectId: string,
    payload: { artifact_id: string; target_key?: string; note?: string },
  ): Promise<ObjectArtifactRef> {
    const { data } = await client.post(
      `/api/kb/ontology/objects/${objectId}/refs`, payload)
    return data as ObjectArtifactRef
  }

  async function deleteObjectRef(objectId: string, refId: string): Promise<void> {
    await client.delete(`/api/kb/ontology/objects/${objectId}/refs/${refId}`)
  }

  /** 制品侧反查（评审①）：该制品/页/行被哪些对象引用。
   * targetKey=''（制品整体级）是有效过滤值——须显式下发，不能按 falsy 丢弃；
   * undefined=不筛（该制品全部引用）。 */
  async function listArtifactRefs(
    artifactId: string, targetKey?: string, limit = 100,
  ): Promise<ArtifactObjectRefsPage> {
    const { data } = await client.get('/api/kb/ontology/artifact-refs', {
      params: {
        artifact_id: artifactId,
        ...(targetKey !== undefined ? { target_key: targetKey } : {}),
        limit,
      },
    })
    return data as ArtifactObjectRefsPage
  }

  // ── 批量导入（67号 §七 两步式）──

  async function uploadObjectImport(
    ontologyId: string, file: File,
  ): Promise<ImportUploadResult> {
    const form = new FormData()
    form.append('file', file)
    const { data } = await client.post(
      `/api/kb/ontology/ontologies/${ontologyId}/objects/import/upload`, form)
    return data as ImportUploadResult
  }

  async function commitObjectImport(
    ontologyId: string,
    payload: {
      import_token: string
      name_column: string
      attribute_columns?: Record<string, string>
    },
  ): Promise<ImportCommitResult> {
    const { data } = await client.post(
      `/api/kb/ontology/ontologies/${ontologyId}/objects/import/commit`, payload)
    return data as ImportCommitResult
  }

  // ── AI 辅助实例化（67号 §八）──

  /** 发起抽取（202）：同步护栏 → 后台 LLM，立即返回 extract_token（1h）。 */
  async function startObjectExtract(
    libraryId: string,
    payload: {
      ontology_id: string
      source:
        | { kind: 'text'; text: string }
        | { kind: 'kb_document'; kb_id: string; document_id: string }
      suggest_relations?: boolean
      model?: string
    },
  ): Promise<{ extract_token: string }> {
    const { data } = await client.post(
      `/api/kb/ontology/libraries/${libraryId}/extract`, payload)
    return data as { extract_token: string }
  }

  /** 轮询抽取结果（前端 2s）：pending / done / failed。 */
  async function getObjectExtract(
    libraryId: string, extractToken: string,
  ): Promise<ExtractStatusBody> {
    const { data } = await client.get(
      `/api/kb/ontology/libraries/${libraryId}/extract/${extractToken}`)
    return data as ExtractStatusBody
  }

  /** 批量实例化（§6.8）：对象 + 关系单事务全有或全无；422 带定位报告。 */
  async function instantiateObjects(
    libraryId: string,
    payload: {
      ontology_id: string
      source: 'manual' | 'extract'
      objects: Array<{ name: string; attributes?: Record<string, unknown> }>
      relations?: Array<{
        ontology_relation_id: string
        source_index?: number
        source_object_id?: string
        target_index?: number
        target_object_id?: string
      }>
    },
  ): Promise<InstantiateResult> {
    const { data } = await client.post(
      `/api/kb/ontology/libraries/${libraryId}/instantiate`, payload)
    return data as InstantiateResult
  }

  // ── 语义关系（实例层）──

  async function createSemanticRelation(payload: {
    ontology_relation_id: string
    source_object_id: string
    target_object_id: string
  }): Promise<SemanticRelationRow> {
    const { data } = await client.post('/api/kb/ontology/semantic-relations', payload)
    return data as SemanticRelationRow
  }

  async function listSemanticRelations(
    libraryId: string,
    params?: {
      source_object_id?: string
      target_object_id?: string
      ontology_relation_id?: string
      limit?: number
      offset?: number
    },
  ): Promise<SemanticRelationsPage> {
    const { data } = await client.get(
      `/api/kb/ontology/libraries/${libraryId}/semantic-relations`, { params })
    return data as SemanticRelationsPage
  }

  async function deleteSemanticRelation(relationId: string): Promise<void> {
    await client.delete(`/api/kb/ontology/semantic-relations/${relationId}`)
  }

  // ── 图查询 ──

  async function getLibrarySchema(libraryId: string): Promise<LibrarySchema> {
    const { data } = await client.get(
      `/api/kb/ontology/libraries/${libraryId}/schema`)
    return data as LibrarySchema
  }

  /** 实例图全量（68号 §2.1）：ontology_ids 过滤 + limit 节点护栏。 */
  async function getLibraryGraph(
    libraryId: string,
    params?: { ontology_ids?: string[]; limit?: number },
  ): Promise<LibraryGraph> {
    const query: Record<string, string | number> = {}
    if (params?.ontology_ids?.length) {
      query.ontology_ids = params.ontology_ids.join(',')
    }
    if (params?.limit) query.limit = params.limit
    const { data } = await client.get(
      `/api/kb/ontology/libraries/${libraryId}/graph`,
      { params: Object.keys(query).length ? query : undefined })
    return data as LibraryGraph
  }

  /** 域级对象名搜索（69号 §4.1）：跨库按名匹配，检索面板卡组数据源。 */
  async function searchObjects(
    domain: string, q: string, limit = 20,
  ): Promise<OntologyObjectSearchResult> {
    const { data } = await client.get('/api/kb/ontology/search/objects', {
      params: { domain, q, limit },
    })
    return data as OntologyObjectSearchResult
  }

  return {
    listLibraries,
    getLibrary,
    createLibrary,
    updateLibrary,
    deleteLibrary,
    listOntologies,
    getOntology,
    createOntology,
    updateOntology,
    deleteOntology,
    addAttribute,
    updateAttribute,
    deleteAttribute,
    listRelations,
    createRelation,
    updateRelation,
    deleteRelation,
    listObjects,
    getObject,
    createObject,
    updateObject,
    deleteObject,
    getObjectNeighbors,
    listObjectRefs,
    createObjectRef,
    deleteObjectRef,
    listArtifactRefs,
    uploadObjectImport,
    commitObjectImport,
    startObjectExtract,
    getObjectExtract,
    instantiateObjects,
    createSemanticRelation,
    listSemanticRelations,
    deleteSemanticRelation,
    getLibrarySchema,
    getLibraryGraph,
    searchObjects,
  }
}

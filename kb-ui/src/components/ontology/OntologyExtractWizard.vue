<template>
  <div class="ext-wiz">
    <!-- ① 本体选择 + 建议关系开关 -->
    <el-card class="ext-wiz__step" shadow="never">
      <template #header>
        <b>① 选择本体</b>
        <span class="ext-wiz__hint">（抽取的对象实例将落入选中的本体）</span>
      </template>
      <div class="ext-wiz__pick">
        <span class="ext-wiz__label">目标本体：</span>
        <el-select
          v-model="ontologyId"
          style="width: 220px"
          placeholder="选择要抽取的本体"
          data-test="extract-ontology"
          :disabled="!!candidates || extracting"
        >
          <el-option v-for="o in ontologies" :key="o.id" :value="o.id" :label="o.name" />
        </el-select>
        <el-checkbox v-model="suggestRelations" data-test="extract-suggest-relations">
          建议关系（同时抽取对象间关系候选）
        </el-checkbox>
      </div>
    </el-card>

    <!-- ② 输入源三选一：粘贴 / 上传 txt-md / KB 文档 -->
    <el-card class="ext-wiz__step" shadow="never">
      <template #header>
        <b>② 输入源</b>
        <span class="ext-wiz__hint">（粘贴或上传 ≤20000 字符；KB 文档超限自动截断）</span>
      </template>
      <el-radio-group v-model="sourceKind" data-test="extract-source-kind" :disabled="extracting">
        <el-radio value="text">粘贴文本</el-radio>
        <el-radio value="file">上传 txt / md</el-radio>
        <el-radio value="kb_document">KB 文档</el-radio>
      </el-radio-group>

      <div v-if="sourceKind === 'text'" class="ext-wiz__source">
        <el-input
          v-model="text"
          type="textarea"
          :rows="6"
          placeholder="粘贴要抽取的文本（≤20000 字符）"
          data-test="extract-text"
        />
        <p v-if="text.length" class="ext-wiz__hint" :class="{ 'ext-wiz__hint--warn': textTooLong }">
          {{ text.length }} / 20000 字符
        </p>
      </div>

      <div v-else-if="sourceKind === 'file'" class="ext-wiz__source">
        <el-upload
          accept=".txt,.md"
          :show-file-list="false"
          :http-request="uploadRequest"
          :disabled="extracting"
        >
          <el-button data-test="extract-file">
            {{ text ? '重新选择文件' : '选择 txt / md 文件' }}
          </el-button>
          <template #tip>
            <span v-if="fileName" class="ext-wiz__file">
              {{ fileName }}（{{ text.length }} 字符）
            </span>
            <span v-else class="ext-wiz__hint">文件在前端读取为文本提交，不会存入对象存储。</span>
          </template>
        </el-upload>
      </div>

      <div v-else class="ext-wiz__source">
        <div class="ext-wiz__kb-row">
          <el-select
            v-model="kbId"
            style="width: 240px"
            placeholder="选择知识库"
            data-test="extract-kb"
            :loading="kbLoading"
            @focus="loadKbs"
            @change="onKbChange"
          >
            <el-option v-for="kb in kbs" :key="kb.id" :value="kb.id" :label="kb.name" />
          </el-select>
          <el-select
            v-model="documentId"
            style="width: 280px"
            placeholder="选择文档（须已挖掘）"
            data-test="extract-document"
            :loading="documentsLoading"
            :disabled="!kbId"
          >
            <el-option
              v-for="doc in documents"
              :key="doc.id"
              :value="doc.id"
              :label="doc.document_name"
            />
          </el-select>
        </div>
        <p class="ext-wiz__hint">仅读取文档当前知识分段（normalized 优先），不回写 KB。</p>
      </div>

      <div class="ext-wiz__actions">
        <el-button
          type="primary"
          :loading="extracting"
          :disabled="!canStart"
          data-test="extract-start"
          @click="startExtract"
        >{{ extracting ? '抽取中…' : '开始抽取' }}</el-button>
        <el-button v-if="candidates" data-test="extract-restart" @click="resetWizard">
          重新抽取
        </el-button>
      </div>
    </el-card>

    <!-- ③ 抽取失败（LLM 错误原样带回，不自动重试） -->
    <el-card v-if="extractError" class="ext-wiz__step" shadow="never">
      <template #header><b>③ 抽取失败</b></template>
      <el-alert type="error" :closable="false" title="LLM 抽取失败，可重试或调整源文本" :description="extractError" />
    </el-card>

    <!-- ④ 候选确认：对象（行内编辑+勾选+冲突标黄）/ 关系（勾选+端点标红） -->
    <el-card v-if="candidates" class="ext-wiz__step" shadow="never">
      <template #header>
        <b>④ 确认候选</b>
        <span class="ext-wiz__hint">（已选 {{ selectedObjects.length }} 对象 / {{ selectedRelations.length }} 关系）</span>
      </template>
      <el-alert
        v-for="(warning, i) in candidates.warnings"
        :key="i"
        type="warning"
        :closable="false"
        :title="warning"
        class="ext-wiz__warning"
      />
      <el-table :data="objectRows" size="small" class="ext-wiz__table" row-key="_key">
        <el-table-column width="44">
          <template #default="{ row }">
            <el-checkbox v-model="row.selected" data-test="extract-object-check" />
          </template>
        </el-table-column>
        <el-table-column label="对象名称" min-width="150">
          <template #default="{ row }">
            <el-input v-model="row.name" size="small" :class="{ 'ext-wiz__conflict': row.conflict }" data-test="extract-object-name" />
          </template>
        </el-table-column>
        <el-table-column label="属性（可编辑）" min-width="260">
          <template #default="{ row }">
            <div v-if="attrKeys(row).length" class="ext-wiz__attrs">
              <div v-for="key in attrKeys(row)" :key="key" class="ext-wiz__attr">
                <span class="ext-wiz__attr-key">{{ key }}</span>
                <el-input
                  :model-value="attributeText(row.attributes[key])"
                  size="small"
                  data-test="extract-object-attr"
                  @update:model-value="(v: string) => { row.attributes[key] = v }"
                />
              </div>
            </div>
            <span v-else class="ext-wiz__hint">（无属性）</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.conflict" size="small" type="warning" effect="plain">
              与库内「{{ row.conflictWith }}」重名
            </el-tag>
            <el-tag v-else size="small" type="success" effect="plain">新建</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <template v-if="suggestRelations">
        <p class="ext-wiz__section-title">关系候选</p>
        <el-table
          v-if="candidates.relations.length"
          :data="relationRows"
          size="small"
          class="ext-wiz__table"
          row-key="_key"
        >
          <el-table-column width="44">
            <template #default="{ row }">
              <el-checkbox v-model="row.selected" :disabled="!row.relationId" data-test="extract-relation-check" />
            </template>
          </el-table-column>
          <el-table-column label="关系" min-width="220">
            <template #default="{ row }">
              {{ row.source }}
              <el-tag size="small" effect="plain" class="ext-wiz__rel-tag">{{ row.relation }}</el-tag>
              {{ row.target }}
            </template>
          </el-table-column>
          <el-table-column label="目标" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.targetExisting ? 'info' : 'success'" effect="plain">
                {{ row.targetExisting ? '库内已有' : '本批新建' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="端点预检" width="160">
            <template #default="{ row }">
              <span v-if="row.endpointOk" class="ext-wiz__ok">匹配</span>
              <el-tag v-else size="small" type="danger" effect="plain">端点不匹配</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <p v-else class="ext-wiz__hint">LLM 未给出可解析的关系候选。</p>
      </template>

      <div class="ext-wiz__actions">
        <el-button
          type="primary"
          :loading="committing"
          :disabled="!selectedObjects.length"
          data-test="extract-commit"
          @click="commitInstantiate"
        >落库 {{ selectedObjects.length }} 对象{{ suggestRelations ? ` + ${selectedRelations.length} 关系` : '' }}</el-button>
      </div>
    </el-card>

    <!-- ⑤ 结果：成功计数 / 422 定位报告 -->
    <el-card v-if="result || report" class="ext-wiz__step" shadow="never">
      <template #header><b>⑤ 落库结果</b></template>
      <template v-if="result">
        <el-alert
          type="success"
          :closable="false"
          :title="`已创建 ${result.object_ids.length} 个对象、${result.relation_ids.length} 条关系（来源：AI 抽取）`"
        />
        <div class="ext-wiz__actions">
          <el-button data-test="extract-reset" @click="resetWizard">再抽取一批</el-button>
        </div>
      </template>
      <template v-else-if="report">
        <el-alert
          type="error"
          :closable="false"
          :title="`${report.message}（${report.errors.length} 项问题，未落库任何数据）`"
        />
        <el-table :data="report.errors" size="small" max-height="260" class="ext-wiz__table">
          <el-table-column prop="field" label="位置" width="130" />
          <el-table-column label="错误类型" width="150">
            <template #default="{ row }">
              <el-tag size="small" type="danger" effect="plain">{{ codeLabel(row.code) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="message" label="说明" min-width="180" show-overflow-tooltip />
        </el-table>
        <div class="ext-wiz__actions">
          <el-button data-test="extract-report-back" @click="report = null">返回调整候选</el-button>
        </div>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { useOntologyApi } from '@/api/ontology'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import type {
  ExtractCandidates, InstantiateResult, InstantiateValidationReport, Ontology,
} from '@/types/ontology'
import type { KbDocument, KbSummary } from '@/types/kb'

const MAX_TEXT_CHARS = 20000

const props = withDefaults(defineProps<{
  libraryId: string
  ontologies: Ontology[]
  /** 轮询间隔（测试注入 1ms；默认设计口径 2s）。 */
  pollIntervalMs?: number
}>(), { pollIntervalMs: 2000 })

const emit = defineEmits<{ instantiated: [result: InstantiateResult] }>()

const api = useOntologyApi()
const kbApi = useKbApi()
const domainStore = useDomainStore()

// ── ① 本体与开关 ──
const ontologyId = ref('')
const suggestRelations = ref(true)

// ── ② 输入源 ──
const sourceKind = ref<'text' | 'file' | 'kb_document'>('text')
const text = ref('')
const fileName = ref('')
const kbId = ref('')
const documentId = ref('')
const kbs = ref<KbSummary[]>([])
const documents = ref<KbDocument[]>([])
const kbLoading = ref(false)
const documentsLoading = ref(false)

// ── ③ 发起与轮询 ──
const extracting = ref(false)
const extractError = ref('')

// ── ④ 候选确认（行对象为候选的本地可编辑副本）──
const candidates = ref<ExtractCandidates | null>(null)
const objectRows = ref<Array<{
  _key: number
  name: string
  originalName: string
  attributes: Record<string, unknown>
  conflict: boolean
  conflictWith: string | null
  selected: boolean
}>>([])
const relationRows = ref<Array<{
  _key: number
  source: string
  relation: string
  target: string
  targetExisting: boolean
  targetObjectId: string | null
  endpointOk: boolean
  relationId: string | null
  selected: boolean
}>>([])

// ── ⑤ 落库 ──
const committing = ref(false)
const result = ref<InstantiateResult | null>(null)
const report = ref<InstantiateValidationReport | null>(null)

const textTooLong = computed(() => text.value.length > MAX_TEXT_CHARS)
const canStart = computed(() => {
  if (!ontologyId.value || extracting.value || textTooLong.value) return false
  if (sourceKind.value === 'kb_document') return !!kbId.value && !!documentId.value
  return text.value.trim().length > 0
})
const selectedObjects = computed(() => objectRows.value.filter(r => r.selected))
const selectedRelations = computed(() => relationRows.value.filter(r => r.selected))

const _CODE_LABELS: Record<string, string> = {
  empty_batch: '空批',
  empty_name: '名称为空',
  invalid_name: '名称超长',
  duplicate_existing: '与库内重名',
  duplicate_in_batch: '批内重名',
  unknown_attribute: '未知属性',
  invalid_attribute_value: '属性值非标量',
  invalid_source_ref: '源端引用非法',
  invalid_target_ref: '目标端引用非法',
  unknown_source_object: '源对象不在本库',
  unknown_target_object: '目标对象不在本库',
  unknown_relation: '关系类型不存在',
  endpoint_mismatch: '端点本体不匹配',
  self_edge: '自指边',
  duplicate_edge: '重复关系',
}

function codeLabel(code: string | undefined): string {
  if (!code) return ''
  return _CODE_LABELS[code.split(':')[0]] ?? code
}

function attributeText(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

/** 候选行属性键（attributes 兜底空对象——行对象缺省时不炸渲染）。 */
function attrKeys(row: { attributes?: Record<string, unknown> | null }): string[] {
  return Object.keys(row.attributes ?? {})
}

/** ②文件源：el-upload 自定义请求 + 测试直调入口（前端读文本，走 kind=text）。 */
async function uploadRequest(options: UploadRequestOptions): Promise<void> {
  await readFileText(options.file as File)
}

async function readFileText(file: File): Promise<void> {
  try {
    text.value = await file.text()
    fileName.value = file.name
  } catch {
    ElMessage.error('文件读取失败')
  }
}

async function loadKbs(): Promise<void> {
  if (kbs.value.length || kbLoading.value) return
  kbLoading.value = true
  try {
    kbs.value = await kbApi.listKbs(domainStore.currentDomain)
  } catch (error) {
    ElMessage.error(`知识库列表加载失败：${await apiErrorDetail(error)}`)
  } finally {
    kbLoading.value = false
  }
}

async function onKbChange(): Promise<void> {
  documentId.value = ''
  documents.value = []
  if (!kbId.value) return
  documentsLoading.value = true
  try {
    documents.value = await kbApi.listDocuments(kbId.value, undefined, 200)
  } catch (error) {
    ElMessage.error(`文档列表加载失败：${await apiErrorDetail(error)}`)
  } finally {
    documentsLoading.value = false
  }
}

function sourcePayload():
    | { kind: 'text'; text: string }
    | { kind: 'kb_document'; kb_id: string; document_id: string } {
  if (sourceKind.value === 'kb_document') {
    return { kind: 'kb_document', kb_id: kbId.value, document_id: documentId.value }
  }
  return { kind: 'text', text: text.value }
}

/** ③ 发起：202 拿 token → 2s 轮询至 done/failed（加载态由 extracting 承载）。 */
async function startExtract(): Promise<void> {
  if (!canStart.value) return
  extracting.value = true
  extractError.value = ''
  candidates.value = null
  result.value = null
  report.value = null
  try {
    const { extract_token: token } = await api.startObjectExtract(props.libraryId, {
      ontology_id: ontologyId.value,
      source: sourcePayload(),
      suggest_relations: suggestRelations.value,
    })
    const body = await pollExtract(token)
    if (body.status === 'done') await seedRows(body.candidates)
    else if (body.status === 'failed') extractError.value = body.error
  } catch (error) {
    const detail = await apiErrorDetail(error)
    ElMessage.error(`抽取失败：${friendlyExtractError(detail)}`)
  } finally {
    extracting.value = false
  }
}

async function pollExtract(token: string): Promise<
  { status: 'pending' } | { status: 'done'; candidates: ExtractCandidates } | { status: 'failed'; error: string }
> {
  for (;;) {
    const body = await api.getObjectExtract(props.libraryId, token)
    if (body.status !== 'pending') return body
    await sleep(props.pollIntervalMs)
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => { setTimeout(resolve, ms) })
}

function friendlyExtractError(detail: string): string {
  if (detail.includes('document_not_mined')) return '文档尚未挖掘（无当前知识），请先完成挖掘'
  if (detail.includes('empty_text')) return '源文本为空'
  return detail
}

/** ④ 候选落表：对象默认全选（冲突默认不选）；关系默认选端点匹配且类型存在的。 */
async function seedRows(data: ExtractCandidates): Promise<void> {
  candidates.value = data
  objectRows.value = data.objects.map((o, i) => ({
    _key: i,
    name: o.name,
    originalName: o.name,
    attributes: { ...o.attributes },
    conflict: o.conflict,
    conflictWith: o.conflict_with,
    selected: !o.conflict,
  }))
  const relationIdByName = new Map<string, string>()
  if (data.relations.length) {
    try {
      for (const rel of await api.listRelations(props.libraryId)) {
        if (!relationIdByName.has(rel.name)) relationIdByName.set(rel.name, rel.id)
      }
    } catch {
      ElMessage.warning('关系类型列表加载失败，关系候选将不可提交')
    }
  }
  relationRows.value = data.relations.map((r, i) => ({
    _key: i,
    source: r.source,
    relation: r.relation,
    target: r.target,
    targetExisting: r.target_existing,
    targetObjectId: r.target_object_id,
    endpointOk: r.endpoint_ok,
    relationId: relationIdByName.get(r.relation) ?? null,
    selected: r.endpoint_ok && relationIdByName.has(r.relation),
  }))
}

/** ⑤ 提交负载：勾选对象（编辑后名称/属性生效）+ 两端可解析的勾选关系。 */
function buildInstantiatePayload(): {
  ontology_id: string
  source: 'extract'
  objects: Array<{ name: string; attributes: Record<string, unknown> }>
  relations: Array<{
    ontology_relation_id: string
    source_index: number
    target_index?: number
    target_object_id?: string
  }>
} {
  const chosen = selectedObjects.value
  const rowIndex = new Map(chosen.map((row, index) => [row, index]))
  // 关系按名称解析端点：先当前名（编辑后）再原始名（LLM 输出口径）
  const byName = new Map<string, typeof chosen[number]>()
  for (const row of chosen) {
    if (!byName.has(row.name)) byName.set(row.name, row)
    if (!byName.has(row.originalName)) byName.set(row.originalName, row)
  }
  const relations: Array<{
    ontology_relation_id: string
    source_index: number
    target_index?: number
    target_object_id?: string
  }> = []
  for (const row of selectedRelations.value) {
    if (!row.relationId) continue
    const sourceRow = byName.get(row.source)
    if (!sourceRow) continue
    if (row.targetExisting) {
      if (!row.targetObjectId) continue
      relations.push({
        ontology_relation_id: row.relationId,
        source_index: rowIndex.get(sourceRow)!,
        target_object_id: row.targetObjectId,
      })
    } else {
      const targetRow = byName.get(row.target)
      if (!targetRow) continue
      relations.push({
        ontology_relation_id: row.relationId,
        source_index: rowIndex.get(sourceRow)!,
        target_index: rowIndex.get(targetRow)!,
      })
    }
  }
  return {
    ontology_id: ontologyId.value,
    source: 'extract',
    objects: chosen.map(row => ({
      name: row.name.trim(),
      attributes: Object.fromEntries(
        Object.entries(row.attributes).filter(([, v]) => v !== '' && v !== null && v !== undefined),
      ),
    })),
    relations,
  }
}

/** 422 结构化报告提取（detail={message, report}；apiErrorDetail 只出文案）。 */
function extractReport(error: unknown): InstantiateValidationReport | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail
  if (detail && typeof detail === 'object' && 'report' in detail) {
    const candidate = (detail as { report?: unknown }).report
    if (candidate && typeof candidate === 'object'
        && Array.isArray((candidate as InstantiateValidationReport).errors)) {
      return candidate as InstantiateValidationReport
    }
  }
  return null
}

async function commitInstantiate(): Promise<void> {
  if (!selectedObjects.value.length) return
  committing.value = true
  try {
    const data = await api.instantiateObjects(
      props.libraryId, buildInstantiatePayload())
    result.value = data
    report.value = null
    emit('instantiated', data)
    ElMessage.success(`落库完成：${data.object_ids.length} 个对象`)
  } catch (error) {
    const validation = extractReport(error)
    if (validation) {
      report.value = validation
    } else {
      ElMessage.error(`落库失败：${await apiErrorDetail(error)}`)
    }
  } finally {
    committing.value = false
  }
}

function resetWizard(): void {
  candidates.value = null
  objectRows.value = []
  relationRows.value = []
  extractError.value = ''
  result.value = null
  report.value = null
}

// 供 ObjectTab 抽屉挂载后父组件/测试直调（对齐 OntologyImportWizard 模式）
defineExpose({
  ontologyId, suggestRelations, sourceKind, text, fileName, kbId, documentId,
  extracting, extractError, candidates, objectRows, relationRows, result, report,
  readFileText, loadKbs, onKbChange, startExtract, seedRows,
  buildInstantiatePayload, commitInstantiate, resetWizard,
})
</script>

<style scoped>
.ext-wiz { display: flex; flex-direction: column; gap: 12px; }
.ext-wiz__step :deep(.el-card__header) { padding: 10px 16px; }
.ext-wiz__hint { font-size: 12px; color: var(--kb-text-tertiary); font-weight: normal; }
.ext-wiz__hint--warn { color: var(--el-color-warning); }
.ext-wiz__file { margin-left: 10px; font-size: 12.5px; color: var(--kb-text-tertiary); }
.ext-wiz__pick { display: flex; align-items: center; gap: 14px; }
.ext-wiz__label { font-size: 13px; }
.ext-wiz__source { margin-top: 10px; }
.ext-wiz__kb-row { display: flex; gap: 10px; }
.ext-wiz__actions { margin-top: 12px; display: flex; justify-content: flex-end; gap: 8px; }
.ext-wiz__warning { margin-bottom: 8px; }
.ext-wiz__table :deep(.el-input__wrapper) { box-shadow: none; background: var(--el-fill-color-light); }
.ext-wiz__conflict :deep(.el-input__wrapper) { background: var(--el-color-warning-light-9); }
.ext-wiz__attrs { display: flex; flex-direction: column; gap: 4px; }
.ext-wiz__attr { display: flex; align-items: center; gap: 6px; }
.ext-wiz__attr-key { min-width: 72px; font-size: 12px; color: var(--kb-text-tertiary); }
.ext-wiz__section-title { margin: 12px 0 6px; font-size: 13px; font-weight: 600; }
.ext-wiz__rel-tag { margin: 0 4px; }
.ext-wiz__ok { font-size: 12.5px; color: var(--el-color-success); }
</style>

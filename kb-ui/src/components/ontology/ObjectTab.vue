<template>
  <div class="obj-tab">
    <div class="obj-tab__toolbar">
      <el-select
        v-model="filterOntologyId"
        class="obj-tab__filter"
        clearable
        placeholder="本体：全部"
        data-test="filter-object-ontology"
        @change="reload"
      >
        <el-option v-for="o in ontologies" :key="o.id" :value="o.id" :label="o.name" />
      </el-select>
      <el-input
        v-model="keyword"
        class="obj-tab__search"
        placeholder="对象名称搜索"
        clearable
        data-test="filter-object-name"
        @keyup.enter="reload"
        @clear="reload"
      />
      <el-button :loading="loading" @click="reload">查询</el-button>
      <div class="obj-tab__spacer" />
      <el-button
        v-if="canWrite"
        :disabled="ontologies.length < 1"
        data-test="import-objects"
        @click="importVisible = true"
      >批量导入</el-button>
      <el-button
        v-if="canWrite"
        :disabled="ontologies.length < 1"
        data-test="extract-objects"
        @click="extractVisible = true"
      >AI 抽取</el-button>
      <el-button
        v-if="canWrite"
        type="primary"
        :disabled="ontologies.length < 1"
        data-test="create-object"
        @click="openCreate"
      >新建对象</el-button>
    </div>

    <el-table v-loading="loading" :data="items">
      <el-table-column label="名称" min-width="200">
        <template #default="{ row }">
          <button class="obj-tab__link" type="button" @click="openDetail(row)">{{ row.name }}</button>
        </template>
      </el-table-column>
      <el-table-column label="本体" width="140">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ row.ontology_name || ontologyName(row.ontology_id) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="来源" width="90" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="sourceTagType(row.source)" effect="plain">{{ sourceLabel(row.source) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="属性摘要" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">{{ attributesSummary(row.attributes_json) }}</template>
      </el-table-column>
      <el-table-column label="更新时间" width="160">
        <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column v-if="canWrite" label="操作" width="130" align="center">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState :text="ontologies.length < 1
          ? '先在「本体」Tab 定义本体，再创建对象'
          : '暂无语义对象——点右上角「新建对象」手工实例化'" />
      </template>
    </el-table>

    <el-pagination
      v-if="total > pageSize"
      class="obj-tab__pager"
      layout="total, prev, pager, next"
      :total="total"
      :page-size="pageSize"
      :current-page="page"
      @current-change="onPageChange"
    />

    <!-- 新建 / 编辑对象：动态属性表单（属性定义驱动，值=自由标量） -->
    <el-dialog v-model="formVisible" :title="editingId ? '编辑对象' : '新建对象'" width="560px">
      <el-form label-width="90px">
        <el-form-item v-if="!editingId" label="本体">
          <el-select
            v-model="form.ontologyId"
            placeholder="选择本体"
            style="width: 100%"
            data-test="object-ontology"
            @change="onFormOntologyChange"
          >
            <el-option v-for="o in ontologies" :key="o.id" :value="o.id" :label="o.name" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="本体">
          <el-input :model-value="form.ontologyName" disabled />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：阿司匹林" maxlength="200" data-test="object-name" />
        </el-form-item>
        <template v-if="form.attributeDefs.length > 0">
          <el-form-item
            v-for="def in form.attributeDefs"
            :key="def.id"
            :label="def.name"
          >
            <div class="obj-tab__attr-value">
              <el-switch
                v-if="isBooleanSlot(def.name)"
                v-model="boolSlots[def.name]"
              />
              <el-input-number
                v-else-if="isNumberSlot(def.name)"
                v-model="numberSlots[def.name]"
                :controls="false"
                style="width: 200px"
              />
              <el-input
                v-else
                v-model="textSlots[def.name]"
                :placeholder="def.description || '自由文本'"
                maxlength="500"
              />
              <span v-if="def.description" class="obj-tab__attr-hint">{{ def.description }}</span>
            </div>
          </el-form-item>
        </template>
        <p v-else class="obj-tab__hint">该本体尚未定义属性，仅创建命名对象。</p>
      </el-form>
      <p class="obj-tab__hint">属性无类型：按存量值形态渲染（布尔/数字/文本）；留空 = 不写该键。</p>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" data-test="submit-object" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 对象详情抽屉：属性对齐 + 一度邻接（可解除）+ 建边 -->
    <el-drawer v-model="detailVisible" size="480px" :title="detail?.name || '对象详情'">
      <div v-if="detail" v-loading="detailLoading" class="obj-tab__detail">
        <div class="obj-tab__detail-meta">
          <el-tag size="small" effect="plain">{{ detail.ontology_name || ontologyName(detail.ontology_id) }}</el-tag>
          <el-tag size="small" :type="sourceTagType(detail.source)" effect="plain">{{ sourceLabel(detail.source) }}</el-tag>
          <el-tag size="small" effect="plain" type="info">出 {{ detail.outgoing_count }} · 入 {{ detail.incoming_count }}</el-tag>
        </div>

        <h4 class="obj-tab__section-title">属性</h4>
        <el-descriptions v-if="Object.keys(detail.attributes_json || {}).length > 0" :column="1" size="small" border>
          <el-descriptions-item
            v-for="(value, key) in detail.attributes_json"
            :key="key"
            :label="String(key)"
          >{{ formatAttributeValue(value) }}</el-descriptions-item>
        </el-descriptions>
        <p v-else class="obj-tab__hint">未填写属性值。</p>

        <h4 class="obj-tab__section-title">出边（指向他人）</h4>
        <NeighborList
          :edges="neighbors.outgoing"
          direction-label="→"
          :can-write="canWrite"
          @remove="removeEdge"
          @jump="openDetailById"
        />
        <h4 class="obj-tab__section-title">入边（他人指向）</h4>
        <NeighborList
          :edges="neighbors.incoming"
          direction-label="←"
          :can-write="canWrite"
          @remove="removeEdge"
          @jump="openDetailById"
        />
        <div v-if="canWrite" class="obj-tab__edge-actions">
          <el-button type="primary" :disabled="relations.length < 1" data-test="add-edge" @click="openEdgeCreate">
            建立关系
          </el-button>
        </div>
      </div>
    </el-drawer>

    <!-- 建立语义关系：方向 + 关系类型（端点过滤）+ 对端对象（远程搜索） -->
    <el-dialog v-model="edgeVisible" title="建立语义关系" width="520px" append-to-body>
      <el-form label-width="90px">
        <el-form-item label="方向">
          <el-radio-group v-model="edgeForm.direction">
            <el-radio value="out">出边（{{ detail?.name }} → 对端）</el-radio>
            <el-radio value="in">入边（对端 → {{ detail?.name }}）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="关系类型">
          <el-select
            v-model="edgeForm.relationId"
            placeholder="选择关系类型"
            style="width: 100%"
            data-test="edge-type"
            @change="edgeForm.otherObjectId = ''"
          >
            <el-option
              v-for="r in candidateRelations"
              :key="r.id"
              :value="r.id"
              :label="`${r.name}（${ontologyName(r.source_ontology_id)} → ${ontologyName(r.target_ontology_id)}）`"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="对端对象">
          <el-select
            v-model="edgeForm.otherObjectId"
            placeholder="输入名称搜索"
            style="width: 100%"
            filterable
            remote
            :remote-method="searchOtherObjects"
            :loading="edgeSearching"
            data-test="edge-other"
          >
            <el-option
              v-for="o in otherObjects"
              :key="o.id"
              :value="o.id"
              :label="o.name"
            />
          </el-select>
          <div v-if="otherOntologyId" class="obj-tab__hint">
            对端限定本体：{{ ontologyName(otherOntologyId) }}
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="edgeVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" data-test="submit-edge" @click="saveEdge">建立</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入向导（67号 §七 两步式：上传解析 → 列映射 → 提交/报告） -->
    <el-drawer v-model="importVisible" size="680px" title="批量导入对象" destroy-on-close>
      <OntologyImportWizard :ontologies="ontologies" @imported="onImported" />
    </el-drawer>

    <!-- AI 抽取向导（67号 §八 五步：本体 → 输入源 → 轮询 → 候选确认 → 落库） -->
    <el-drawer v-model="extractVisible" size="720px" title="AI 抽取对象" destroy-on-close>
      <OntologyExtractWizard
        :library-id="libraryId"
        :ontologies="ontologies"
        @instantiated="onImported"
      />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useOntologyApi } from '@/api/ontology'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import NeighborList from './NeighborList.vue'
import OntologyImportWizard from './OntologyImportWizard.vue'
import OntologyExtractWizard from './OntologyExtractWizard.vue'
import type {
  ObjectNeighborEdge, Ontology, OntologyAttributeDef, OntologyObjectDetail,
  OntologyObjectRow, OntologyRelation,
} from '@/types/ontology'

const props = defineProps<{
  libraryId: string
  canWrite: boolean
  ontologies: Ontology[]
  relations: OntologyRelation[]
}>()

const emit = defineEmits<{ refresh: [] }>()

const api = useOntologyApi()

// ── 列表（后端分页 + 筛选）──
const loading = ref(false)
const items = ref<OntologyObjectRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const filterOntologyId = ref('')
const keyword = ref('')

async function reload() {
  loading.value = true
  try {
    const result = await api.listObjects(props.libraryId, {
      ontology_id: filterOntologyId.value || undefined,
      q: keyword.value.trim() || undefined,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    })
    items.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(`加载对象失败：${await apiErrorDetail(error)}`)
  } finally {
    loading.value = false
  }
}

function onPageChange(next: number) {
  page.value = next
  void reload()
}

function ontologyName(ontologyId: string): string {
  return props.ontologies.find(o => o.id === ontologyId)?.name || '—'
}

function sourceLabel(source: string): string {
  if (source === 'import') return '导入'
  if (source === 'extract') return 'AI 抽取'
  return '手工'
}

function sourceTagType(source: string): 'info' | 'success' | 'warning' {
  if (source === 'import') return 'success'
  if (source === 'extract') return 'warning'
  return 'info'
}

function attributesSummary(attrs: Record<string, unknown> | null): string {
  if (!attrs) return '—'
  const keys = Object.keys(attrs)
  if (keys.length === 0) return '—'
  return keys.map(k => `${k}=${formatAttributeValue(attrs[k])}`).join('；')
}

function formatAttributeValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function formatTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}

// ── 新建 / 编辑（动态属性表单）──
const formVisible = ref(false)
const saving = ref(false)
const editingId = ref('')
const form = ref<{
  ontologyId: string
  ontologyName: string
  name: string
  attributeDefs: OntologyAttributeDef[]
}>({ ontologyId: '', ontologyName: '', name: '', attributeDefs: [] })

/** 三类值槽分开存（按存量值形态决定渲染控件；新建默认文本）。 */
const textSlots = reactive<Record<string, string>>({})
const numberSlots = reactive<Record<string, number | undefined>>({})
const boolSlots = reactive<Record<string, boolean>>({})
const numberKeys = new Set<string>()
const boolKeys = new Set<string>()

function isNumberSlot(name: string): boolean {
  return numberKeys.has(name)
}

function isBooleanSlot(name: string): boolean {
  return boolKeys.has(name)
}

function resetSlots(defs: OntologyAttributeDef[], existing: Record<string, unknown> | null) {
  for (const key of [...Object.keys(textSlots), ...Object.keys(numberSlots), ...Object.keys(boolSlots)]) {
    delete textSlots[key]
    delete numberSlots[key]
    delete boolSlots[key]
  }
  numberKeys.clear()
  boolKeys.clear()
  for (const def of defs) {
    const value = existing?.[def.name]
    if (typeof value === 'number') {
      numberKeys.add(def.name)
      numberSlots[def.name] = value
    } else if (typeof value === 'boolean') {
      boolKeys.add(def.name)
      boolSlots[def.name] = value
    } else if (value !== undefined && value !== null) {
      textSlots[def.name] = String(value)
    }
  }
}

async function openCreate() {
  editingId.value = ''
  form.value = { ontologyId: '', ontologyName: '', name: '', attributeDefs: [] }
  resetSlots([], null)
  formVisible.value = true
}

async function onFormOntologyChange(ontologyId: string) {
  if (!ontologyId) return
  try {
    const detail = await api.getOntology(ontologyId)
    // 换本体 = 换属性定义集，已填值全部作废
    resetSlots(detail.attributes, null)
    form.value.attributeDefs = detail.attributes
  } catch (error) {
    ElMessage.error(`加载属性定义失败：${await apiErrorDetail(error)}`)
  }
}

async function openEdit(row: OntologyObjectRow) {
  editingId.value = row.id
  try {
    const detail = await api.getObject(row.id)
    form.value = {
      ontologyId: detail.ontology_id,
      ontologyName: detail.ontology_name || ontologyName(detail.ontology_id),
      name: detail.name,
      attributeDefs: detail.attribute_defs,
    }
    resetSlots(detail.attribute_defs, detail.attributes_json)
    formVisible.value = true
  } catch (error) {
    ElMessage.error(`加载对象失败：${await apiErrorDetail(error)}`)
  }
}

/** 收集非空值（空串/未选 = 不写键；数字/布尔槽一旦激活即写入）。 */
function collectAttributes(): Record<string, unknown> {
  const attrs: Record<string, unknown> = {}
  for (const def of form.value.attributeDefs) {
    if (boolKeys.has(def.name)) {
      attrs[def.name] = boolSlots[def.name] ?? false
    } else if (numberKeys.has(def.name)) {
      const num = numberSlots[def.name]
      if (num !== undefined && num !== null) attrs[def.name] = num
    } else {
      const text = (textSlots[def.name] ?? '').trim()
      if (text) attrs[def.name] = text
    }
  }
  return attrs
}

async function save() {
  const name = form.value.name.trim()
  if (!name) {
    ElMessage.warning('请填写对象名称')
    return
  }
  if (!editingId.value && !form.value.ontologyId) {
    ElMessage.warning('请选择本体')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await api.updateObject(editingId.value, { name, attributes: collectAttributes() })
    } else {
      await api.createObject(form.value.ontologyId, { name, attributes: collectAttributes() })
    }
    formVisible.value = false
    ElMessage.success('已保存')
    await reload()
    emit('refresh')
  } catch (error) {
    ElMessage.error(`保存失败：${await apiErrorDetail(error)}`)
  } finally {
    saving.value = false
  }
}

async function remove(row: OntologyObjectRow) {
  try {
    await ElMessageBox.confirm(
      `确定删除对象「${row.name}」？它参与的关系会阻止删除。`, { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteObject(row.id)
    ElMessage.success('已删除')
    await reload()
    emit('refresh')
  } catch (error) {
    ElMessage.error(`删除失败：${await apiErrorDetail(error)}`)
  }
}

// ── 批量导入（向导在抽屉内自管状态；导入成功后刷新列表与库计数）──
const importVisible = ref(false)
const extractVisible = ref(false)

function onImported() {
  void reload()
  emit('refresh')
}

// ── 详情抽屉 + 邻接 ──
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<OntologyObjectDetail | null>(null)
const neighbors = ref<{ outgoing: ObjectNeighborEdge[]; incoming: ObjectNeighborEdge[] }>({
  outgoing: [], incoming: [],
})

async function openDetail(row: OntologyObjectRow) {
  await loadDetail(row.id)
  detailVisible.value = true
}

async function openDetailById(objectId: string) {
  await loadDetail(objectId)
}

async function loadDetail(objectId: string) {
  detailLoading.value = true
  try {
    const [object, neighborResult] = await Promise.all([
      api.getObject(objectId),
      api.getObjectNeighbors(objectId),
    ])
    detail.value = object
    neighbors.value = neighborResult
  } catch (error) {
    ElMessage.error(`加载详情失败：${await apiErrorDetail(error)}`)
  } finally {
    detailLoading.value = false
  }
}

async function removeEdge(edge: ObjectNeighborEdge) {
  try {
    await ElMessageBox.confirm(`确定解除关系「${edge.relation_name}」？`, { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteSemanticRelation(edge.id)
    ElMessage.success('已解除')
    if (detail.value) await loadDetail(detail.value.id)
    emit('refresh')
  } catch (error) {
    ElMessage.error(`解除失败：${await apiErrorDetail(error)}`)
  }
}

// ── 建立语义关系 ──
const edgeVisible = ref(false)
const edgeSearching = ref(false)
const otherObjects = ref<OntologyObjectRow[]>([])
const edgeForm = ref<{ direction: 'out' | 'in'; relationId: string; otherObjectId: string }>({
  direction: 'out', relationId: '', otherObjectId: '',
})

/** 当前对象可用的关系类型：按方向过滤端点本体匹配。 */
const candidateRelations = computed(() => {
  if (!detail.value) return []
  const ontologyId = detail.value.ontology_id
  return props.relations.filter(r =>
    edgeForm.value.direction === 'out'
      ? r.source_ontology_id === ontologyId
      : r.target_ontology_id === ontologyId,
  )
})

/** 选定关系类型后对端必须落在的本体。 */
const otherOntologyId = computed(() => {
  const relation = props.relations.find(r => r.id === edgeForm.value.relationId)
  if (!relation || !detail.value) return ''
  return edgeForm.value.direction === 'out'
    ? relation.target_ontology_id
    : relation.source_ontology_id
})

function openEdgeCreate() {
  edgeForm.value = { direction: 'out', relationId: '', otherObjectId: '' }
  otherObjects.value = []
  edgeVisible.value = true
}

async function searchOtherObjects(query: string) {
  if (!otherOntologyId.value) return
  edgeSearching.value = true
  try {
    const result = await api.listObjects(props.libraryId, {
      ontology_id: otherOntologyId.value,
      q: query.trim() || undefined,
      limit: 20,
    })
    // 自指边由后端拒绝；前端先过滤掉自己，避免明显必败选项
    otherObjects.value = result.items.filter(o => o.id !== detail.value?.id)
  } catch (error) {
    ElMessage.error(`搜索对象失败：${await apiErrorDetail(error)}`)
  } finally {
    edgeSearching.value = false
  }
}

async function saveEdge() {
  if (!detail.value) return
  if (!edgeForm.value.relationId) {
    ElMessage.warning('请选择关系类型')
    return
  }
  if (!edgeForm.value.otherObjectId) {
    ElMessage.warning('请选择对端对象')
    return
  }
  saving.value = true
  try {
    await api.createSemanticRelation({
      ontology_relation_id: edgeForm.value.relationId,
      source_object_id: edgeForm.value.direction === 'out'
        ? detail.value.id
        : edgeForm.value.otherObjectId,
      target_object_id: edgeForm.value.direction === 'out'
        ? edgeForm.value.otherObjectId
        : detail.value.id,
    })
    edgeVisible.value = false
    ElMessage.success('关系已建立')
    await loadDetail(detail.value.id)
    emit('refresh')
  } catch (error) {
    ElMessage.error(`建立失败：${await apiErrorDetail(error)}`)
  } finally {
    saving.value = false
  }
}

defineExpose({ reload })

onMounted(reload)
</script>

<style scoped>
.obj-tab__toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.obj-tab__filter { width: 160px; }
.obj-tab__search { width: 220px; }
.obj-tab__spacer { flex: 1; }
.obj-tab__pager { margin-top: 12px; justify-content: flex-end; }
.obj-tab__link { border: 0; background: none; padding: 0; color: var(--kb-accent, #2563eb); font-weight: 650; cursor: pointer; }
.obj-tab__attr-value { width: 100%; display: flex; flex-direction: column; gap: 4px; }
.obj-tab__attr-hint { color: var(--kb-text-tertiary); font-size: 12px; }
.obj-tab__hint { color: var(--kb-text-tertiary); font-size: 12px; margin: 6px 0 0; }
.obj-tab__detail { display: flex; flex-direction: column; }
.obj-tab__detail-meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.obj-tab__section-title { margin: 16px 0 8px; font-size: 14px; }
.obj-tab__edge-actions { margin-top: 16px; }
</style>

<template>
  <div class="onto-tab">
    <div class="onto-tab__toolbar">
      <el-input
        v-model="keyword"
        class="onto-tab__search"
        placeholder="按名称过滤"
        clearable
      />
      <el-button
        v-if="canWrite"
        type="primary"
        data-test="create-ontology"
        @click="openCreate"
      >新建本体</el-button>
    </div>

    <el-table :data="filteredOntologies">
      <el-table-column label="名称" min-width="180">
        <template #default="{ row }">
          <button class="onto-tab__link" type="button" @click="openDetail(row)">
            {{ row.name }}
          </button>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">{{ row.description || '—' }}</template>
      </el-table-column>
      <el-table-column label="属性" width="80" align="center">
        <template #default="{ row }">{{ row.attribute_count ?? 0 }}</template>
      </el-table-column>
      <el-table-column label="对象" width="80" align="center">
        <template #default="{ row }">{{ row.object_count ?? 0 }}</template>
      </el-table-column>
      <el-table-column label="关系引用" width="90" align="center">
        <template #default="{ row }">{{ row.relation_ref_count ?? 0 }}</template>
      </el-table-column>
      <el-table-column label="创建时间" width="160">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column v-if="canWrite" label="操作" width="130" align="center">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState :text="emptyText" />
      </template>
    </el-table>

    <!-- 新建本体：基本信息 + 初始属性（可动态增删行） -->
    <el-dialog v-model="createVisible" title="新建本体" width="560px">
      <el-form label-width="70px">
        <el-form-item label="名称">
          <el-input v-model="createForm.name" placeholder="如：设备" maxlength="80" data-test="ontology-name" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="createForm.description" type="textarea" :rows="2" placeholder="这个本体代表什么（可选）" />
        </el-form-item>
        <el-form-item label="初始属性">
          <div class="onto-tab__attrs">
            <div v-for="(attr, i) in createForm.attributes" :key="i" class="onto-tab__attr-row">
              <el-input v-model="attr.name" placeholder="属性名" maxlength="80" />
              <el-input v-model="attr.description" placeholder="说明（可选）" maxlength="2000" />
              <el-button link type="danger" @click="createForm.attributes.splice(i, 1)">移除</el-button>
            </div>
            <el-button link type="primary" @click="createForm.attributes.push({ name: '', description: '' })">
              + 添加属性
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" data-test="submit-ontology" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <!-- 编辑基本信息（属性在详情对话框管理） -->
    <el-dialog v-model="editVisible" title="编辑本体" width="480px">
      <el-form label-width="70px">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" maxlength="80" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 本体详情：基本信息 + 属性管理（增删改 + 上移下移）+ 参与的关系类型 -->
    <el-dialog v-model="detailVisible" width="720px" top="6vh">
      <template #title>
        <span class="onto-tab__detail-title">本体：{{ detail?.name }}</span>
      </template>
      <div v-if="detail" v-loading="detailLoading" class="onto-tab__detail">
        <div class="onto-tab__detail-meta">
          <el-tag size="small" effect="plain">{{ detail.attributes.length }} 个属性</el-tag>
          <el-tag size="small" effect="plain">{{ detail.ontology_relations.length }} 个关系引用</el-tag>
          <span v-if="detail.description" class="onto-tab__detail-desc">{{ detail.description }}</span>
        </div>

        <h4 class="onto-tab__section-title">属性定义</h4>
        <el-table :data="detail.attributes" size="small">
          <el-table-column label="排序" width="110" align="center">
            <template #default="{ $index }">
              <template v-if="canWrite">
                <el-button link :disabled="$index === 0" @click="moveAttribute($index, -1)">↑</el-button>
                <el-button link :disabled="$index === detail.attributes.length - 1" @click="moveAttribute($index, 1)">↓</el-button>
              </template>
              <template v-else>{{ $index + 1 }}</template>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column label="说明" min-width="180">
            <template #default="{ row }">{{ row.description || '—' }}</template>
          </el-table-column>
          <el-table-column v-if="canWrite" label="操作" width="130" align="center">
            <template #default="{ row }">
              <el-button link type="primary" @click="openAttrEdit(row)">编辑</el-button>
              <el-button link type="danger" @click="removeAttribute(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <EmptyState text="尚未定义属性——对象属性值将无处安放" />
          </template>
        </el-table>
        <div v-if="canWrite" class="onto-tab__attr-add">
          <el-input v-model="newAttrName" placeholder="新属性名" maxlength="80" class="onto-tab__attr-add-name" />
          <el-input v-model="newAttrDescription" placeholder="说明（可选）" maxlength="2000" />
          <el-button type="primary" :loading="saving" data-test="add-attribute" @click="addAttribute">添加属性</el-button>
        </div>
        <p class="onto-tab__hint">属性无类型系统：值为自由文本/数字/布尔；改名会同步迁移对象存量键，删除会清理存量键（同事务）。</p>

        <h4 class="onto-tab__section-title">参与的关系类型</h4>
        <el-table :data="detail.ontology_relations" size="small">
          <el-table-column prop="name" label="名称" width="140" />
          <el-table-column label="方向" min-width="220">
            <template #default="{ row }">
              <template v-if="row.source_ontology_id === row.target_ontology_id">
                自环（{{ relationRole(row) }}）
              </template>
              <template v-else>{{ relationRole(row) }}</template>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="160">
            <template #default="{ row }">{{ row.description || '—' }}</template>
          </el-table-column>
          <template #empty>
            <EmptyState text="未参与任何关系类型" />
          </template>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 属性编辑（改名迁移键 / 改说明） -->
    <el-dialog v-model="attrEditVisible" title="编辑属性" width="440px" append-to-body>
      <el-form label-width="70px">
        <el-form-item label="名称">
          <el-input v-model="attrEditForm.name" maxlength="80" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="attrEditForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <p class="onto-tab__hint">改名将在同一事务里迁移对象的存量属性键，值保持不变。</p>
      <template #footer>
        <el-button @click="attrEditVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveAttribute">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useOntologyApi } from '@/api/ontology'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import type { Ontology, OntologyAttributeDef, OntologyDetail } from '@/types/ontology'

const props = defineProps<{
  libraryId: string
  canWrite: boolean
  ontologies: Ontology[]
}>()

const emit = defineEmits<{ refresh: [] }>()

const api = useOntologyApi()

const saving = ref(false)
const keyword = ref('')

const filteredOntologies = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return props.ontologies
  return props.ontologies.filter(o => o.name.toLowerCase().includes(kw))
})

const emptyText = computed(() => keyword.value.trim()
  ? '无匹配的本体——试试调整关键字'
  : '还没有本体，点右上角「新建本体」定义类型层')

// ── 新建 ──
const createVisible = ref(false)
const createForm = ref<{ name: string; description: string; attributes: Array<{ name: string; description: string }> }>({
  name: '', description: '', attributes: [],
})

function openCreate() {
  createForm.value = { name: '', description: '', attributes: [] }
  createVisible.value = true
}

async function create() {
  const name = createForm.value.name.trim()
  if (!name) {
    ElMessage.warning('请填写本体名称')
    return
  }
  const attributes = createForm.value.attributes
    .map(a => ({ name: a.name.trim(), description: a.description.trim() || undefined }))
    .filter(a => a.name)
  if (attributes.length !== createForm.value.attributes.length) {
    ElMessage.warning('存在未填名称的属性行，请补全或移除')
    return
  }
  saving.value = true
  try {
    await api.createOntology(props.libraryId, {
      name,
      description: createForm.value.description.trim() || undefined,
      attributes: attributes.length > 0 ? attributes : undefined,
    })
    createVisible.value = false
    ElMessage.success('本体已创建')
    keyword.value = ''  // 清掉名称过滤，保证新本体立即可见（对齐对象页回归）
    emit('refresh')
  } catch (error) {
    ElMessage.error(`创建失败：${await apiErrorDetail(error)}`)
  } finally {
    saving.value = false
  }
}

// ── 编辑基本信息 ──
const editVisible = ref(false)
const editForm = ref({ name: '', description: '' })
let editingId = ''

function openEdit(row: Ontology) {
  editingId = row.id
  editForm.value = { name: row.name, description: row.description || '' }
  editVisible.value = true
}

async function saveEdit() {
  const name = editForm.value.name.trim()
  if (!name) {
    ElMessage.warning('名称不能为空')
    return
  }
  saving.value = true
  try {
    await api.updateOntology(editingId, {
      name,
      description: editForm.value.description.trim() || undefined,
    })
    editVisible.value = false
    ElMessage.success('已保存')
    emit('refresh')
  } catch (error) {
    ElMessage.error(`保存失败：${await apiErrorDetail(error)}`)
  } finally {
    saving.value = false
  }
}

async function remove(row: Ontology) {
  try {
    await ElMessageBox.confirm(
      `确定删除本体「${row.name}」？其下对象与引用它的关系类型会阻止删除。`, { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteOntology(row.id)
    ElMessage.success('已删除')
    emit('refresh')
  } catch (error) {
    ElMessage.error(`删除失败：${await apiErrorDetail(error)}`)
  }
}

// ── 详情（属性管理）──
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<OntologyDetail | null>(null)

async function openDetail(row: Ontology) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await api.getOntology(row.id)
  } catch (error) {
    ElMessage.error(`加载详情失败：${await apiErrorDetail(error)}`)
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

const newAttrName = ref('')
const newAttrDescription = ref('')

async function addAttribute() {
  if (!detail.value) return
  const name = newAttrName.value.trim()
  if (!name) {
    ElMessage.warning('请填写属性名')
    return
  }
  saving.value = true
  try {
    await api.addAttribute(detail.value.id, {
      name,
      description: newAttrDescription.value.trim() || undefined,
    })
    newAttrName.value = ''
    newAttrDescription.value = ''
    detail.value = await api.getOntology(detail.value.id)
    emit('refresh')
  } catch (error) {
    ElMessage.error(`添加失败：${await apiErrorDetail(error)}`)
  } finally {
    saving.value = false
  }
}

const attrEditVisible = ref(false)
const attrEditForm = ref({ name: '', description: '' })
let editingAttrId = ''

function openAttrEdit(row: OntologyAttributeDef) {
  editingAttrId = row.id
  attrEditForm.value = { name: row.name, description: row.description || '' }
  attrEditVisible.value = true
}

async function saveAttribute() {
  if (!detail.value) return
  const name = attrEditForm.value.name.trim()
  if (!name) {
    ElMessage.warning('名称不能为空')
    return
  }
  saving.value = true
  try {
    await api.updateAttribute(editingAttrId, {
      name,
      description: attrEditForm.value.description.trim() || undefined,
    })
    attrEditVisible.value = false
    detail.value = await api.getOntology(detail.value.id)
    emit('refresh')
  } catch (error) {
    ElMessage.error(`保存失败：${await apiErrorDetail(error)}`)
  } finally {
    saving.value = false
  }
}

async function removeAttribute(row: OntologyAttributeDef) {
  if (!detail.value) return
  try {
    await ElMessageBox.confirm(
      `确定删除属性「${row.name}」？对象的该属性存量值将被同步清理。`, { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteAttribute(row.id)
    detail.value = await api.getOntology(detail.value.id)
    emit('refresh')
  } catch (error) {
    ElMessage.error(`删除失败：${await apiErrorDetail(error)}`)
  }
}

/** 上移/下移：与相邻属性交换 sort_order（创建时即递增，相邻必不等）。 */
async function moveAttribute(index: number, delta: -1 | 1) {
  if (!detail.value) return
  const list = detail.value.attributes
  const other = index + delta
  if (other < 0 || other >= list.length) return
  const a = list[index]
  const b = list[other]
  try {
    await api.updateAttribute(a.id, { sort_order: b.sort_order })
    await api.updateAttribute(b.id, { sort_order: a.sort_order })
    detail.value = await api.getOntology(detail.value.id)
  } catch (error) {
    ElMessage.error(`移动失败：${await apiErrorDetail(error)}`)
  }
}

/** 关系类型在本体视角的方向文案（源端/目标端/自环两端）。 */
function relationRole(row: OntologyDetail['ontology_relations'][number]): string {
  if (!detail.value) return ''
  if (row.source_ontology_id === detail.value.id && row.target_ontology_id === detail.value.id) {
    return `${row.name}（自身 → 自身）`
  }
  if (row.source_ontology_id === detail.value.id) return `${row.name}（源端）`
  return `${row.name}（目标端）`
}

function formatTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}
</script>

<style scoped>
.onto-tab__toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; gap: 10px; }
.onto-tab__search { width: 240px; }
.onto-tab__link { border: 0; background: none; padding: 0; color: var(--kb-accent, #2563eb); font-weight: 650; cursor: pointer; }
.onto-tab__attrs { width: 100%; display: flex; flex-direction: column; gap: 8px; }
.onto-tab__attr-row { display: flex; gap: 8px; align-items: center; }
.onto-tab__attr-row .el-input:first-child { width: 180px; flex: none; }
.onto-tab__detail { max-height: 64vh; overflow-y: auto; }
.onto-tab__detail-title { font-weight: 650; }
.onto-tab__detail-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.onto-tab__detail-desc { color: var(--kb-text-tertiary); font-size: 13px; }
.onto-tab__section-title { margin: 16px 0 8px; font-size: 14px; }
.onto-tab__attr-add { display: flex; gap: 8px; margin-top: 10px; }
.onto-tab__attr-add-name { width: 180px; flex: none; }
.onto-tab__hint { color: var(--kb-text-tertiary); font-size: 12px; margin: 8px 0 0; }
</style>

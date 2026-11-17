<template>
  <div class="onto-list">
    <header class="onto-list__header">
      <div>
        <p>本体库 = 域内的人工类型层与实例层：本体（类）→ 语义对象（实例），关系分类型/实例两级。库随当前知识域切换。</p>
      </div>
      <el-button
        v-if="canWrite"
        type="primary"
        data-test="create-library"
        @click="openCreate"
      >新建本体库</el-button>
    </header>

    <div class="onto-list__filters">
      <el-input
        v-model="keyword"
        class="onto-list__search"
        placeholder="库名关键字"
        clearable
        data-test="filter-library-name"
        @keyup.enter="load"
        @clear="load"
      />
      <el-button :loading="loading" @click="load">查询</el-button>
    </div>

    <el-table v-loading="loading" :data="libraries">
      <el-table-column label="名称" min-width="200">
        <template #default="{ row }">
          <button class="onto-list__link" type="button" @click="open(row.id)">{{ row.name }}</button>
        </template>
      </el-table-column>
      <el-table-column label="描述" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">{{ row.description || '—' }}</template>
      </el-table-column>
      <el-table-column label="本体" width="80" align="center">
        <template #default="{ row }">{{ row.ontology_count ?? 0 }}</template>
      </el-table-column>
      <el-table-column label="对象" width="80" align="center">
        <template #default="{ row }">{{ row.object_count ?? 0 }}</template>
      </el-table-column>
      <el-table-column label="关系实例" width="90" align="center">
        <template #default="{ row }">{{ row.relation_count ?? 0 }}</template>
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
        <EmptyState :text="emptyText" />
      </template>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑本体库' : '新建本体库'" width="500px">
      <el-form label-width="70px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：药品知识本体" maxlength="80" data-test="library-name" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="这个库覆盖什么主题（可选）" />
        </el-form-item>
      </el-form>
      <p v-if="!editingId" class="onto-list__hint">库将创建到当前知识域：{{ domainStore.currentDomain || '未选择' }}（域内名称唯一）。</p>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" data-test="submit-library" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useOntologyApi } from '@/api/ontology'
import { apiErrorDetail } from '@/api/proxyClient'
import { useAuthStore } from '@/stores/auth'
import { useDomainStore } from '@/stores/domain'
import { canManageDomainOntology } from '@/utils/domainPermissions'
import EmptyState from '@/components/common/EmptyState.vue'
import type { OntologyLibrary } from '@/types/ontology'

const api = useOntologyApi()
const router = useRouter()
const auth = useAuthStore()
const domainStore = useDomainStore()

const canWrite = computed(() =>
  canManageDomainOntology(auth.siteRole, domainStore.currentDomainInfo))

const libraries = ref<OntologyLibrary[]>([])
const loading = ref(false)
const saving = ref(false)
const keyword = ref('')

const emptyText = computed(() => keyword.value.trim()
  ? '无匹配的本体库——试试调整关键字'
  : canWrite.value
    ? '还没有本体库，点右上角「新建本体库」开始定义类型层'
    : '当前知识域还没有本体库（由域管理员创建）')

async function load() {
  if (!domainStore.currentDomain) return
  loading.value = true
  try {
    libraries.value = await api.listLibraries(keyword.value.trim() || undefined)
  } catch (error) {
    ElMessage.error(`加载本体库失败：${await apiErrorDetail(error)}`)
  } finally {
    loading.value = false
  }
}

const dialogVisible = ref(false)
const editingId = ref('')
const form = ref({ name: '', description: '' })

function openCreate() {
  editingId.value = ''
  form.value = { name: '', description: '' }
  dialogVisible.value = true
}

function openEdit(row: OntologyLibrary) {
  editingId.value = row.id
  form.value = { name: row.name, description: row.description || '' }
  dialogVisible.value = true
}

async function save() {
  const name = form.value.name.trim()
  if (!name) {
    ElMessage.warning('请填写名称')
    return
  }
  saving.value = true
  try {
    const payload = { name, description: form.value.description.trim() || undefined }
    if (editingId.value) {
      await api.updateLibrary(editingId.value, payload)
    } else {
      await api.createLibrary(payload)
    }
    dialogVisible.value = false
    ElMessage.success('已保存')
    await load()
  } catch (error) {
    ElMessage.error(`保存失败：${await apiErrorDetail(error)}`)
  } finally {
    saving.value = false
  }
}

async function remove(row: OntologyLibrary) {
  try {
    await ElMessageBox.confirm(
      `确定删除本体库「${row.name}」？库内还有 ${row.ontology_count ?? 0} 个本体时会阻止删除。`,
      { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteLibrary(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    ElMessage.error(`删除失败：${await apiErrorDetail(error)}`)
  }
}

function open(libraryId: string) {
  router.push({ name: 'ontology-library-detail', params: { libraryId } })
}

function formatTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}

onMounted(load)
// 域切换即换库清单（数据按域隔离，网关代理天然保证）
watch(() => domainStore.currentDomain, load)
</script>

<style scoped>
.onto-list { padding: 4px; }
.onto-list__header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; }
.onto-list__header p { margin: 5px 0 0; color: var(--kb-text-tertiary); font-size: 13px; }
.onto-list__filters { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.onto-list__search { width: 240px; }
.onto-list__link { border: 0; background: none; padding: 0; color: var(--kb-accent, #2563eb); font-weight: 650; cursor: pointer; }
.onto-list__hint { color: var(--kb-text-tertiary); font-size: 12px; margin: 6px 0 0 70px; }
</style>

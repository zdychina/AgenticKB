<template>
  <div class="onto-list">
    <header class="onto-list__header">
      <div>
        <h2 class="onto-list__title">本体库</h2>
        <p class="onto-list__intro">域内的人工类型层与实例层：本体（类）→ 语义对象（实例），关系分类型/实例两级。库随当前知识域切换。</p>
      </div>
      <el-button
        v-if="canWrite"
        type="primary"
        data-test="create-library"
        @click="openCreate"
      >
        <el-icon class="onto-list__btn-icon"><Plus /></el-icon>新建本体库
      </el-button>
    </header>

    <div class="onto-list__filters">
      <el-input
        v-model="keyword"
        class="onto-list__search"
        placeholder="库名关键字"
        clearable
        :prefix-icon="Search"
        data-test="filter-library-name"
        @keyup.enter="load"
        @clear="load"
      />
      <el-button :loading="loading" @click="load">查询</el-button>
    </div>

    <div v-if="libraries.length > 0" v-loading="loading" class="onto-list__grid">
      <article
        v-for="row in libraries"
        :key="row.id"
        class="onto-card"
        data-test="library-card"
        @click="open(row.id)"
      >
        <div class="onto-card__head">
          <span class="onto-card__avatar" aria-hidden="true">{{ initial(row.name) }}</span>
          <div class="onto-card__title-wrap">
            <h3 class="onto-card__name" :title="row.name">{{ row.name }}</h3>
            <span class="onto-card__time">更新于 {{ formatTime(row.updated_at) }}</span>
          </div>
        </div>
        <p class="onto-card__desc">{{ row.description || '暂无描述' }}</p>
        <div class="onto-card__stats">
          <span class="onto-card__stat"><b>{{ row.ontology_count ?? 0 }}</b>本体</span>
          <span class="onto-card__stat"><b>{{ row.object_count ?? 0 }}</b>对象</span>
          <span class="onto-card__stat"><b>{{ row.relation_count ?? 0 }}</b>关系</span>
        </div>
        <div v-if="canWrite" class="onto-card__actions" @click.stop>
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </div>
      </article>
    </div>
    <EmptyState v-else :text="emptyText">
      <template v-if="canWrite && !keyword.trim()" #action>
        <el-button type="primary" data-test="create-library-empty" @click="openCreate">新建本体库</el-button>
      </template>
    </EmptyState>

    <el-dialog v-model="dialogVisible" width="500px">
      <template #header>
        <div class="kb-dialog-title">
          <el-icon><component :is="editingId ? Edit : FolderAdd" /></el-icon>
          {{ editingId ? '编辑本体库' : '新建本体库' }}
        </div>
      </template>
      <el-form label-width="70px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：核心网知识本体" maxlength="80" data-test="library-name" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="这个库覆盖什么主题（可选）" />
        </el-form-item>
      </el-form>
      <p v-if="!editingId" class="kb-note">库将创建到当前知识域：<b>{{ domainStore.currentDomain || '未选择' }}</b>（域内名称唯一）。</p>
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
import { Edit, FolderAdd, Plus, Search } from '@element-plus/icons-vue'
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
      keyword.value = ''  // 清掉搜索词，新建的库立即可见（对齐对象页回归）
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

/** 卡片头像字：取库名首个字符，让一排卡片有视觉区分度。 */
function initial(name: string): string {
  const trimmed = name.trim()
  return trimmed ? trimmed[0] : '库'
}

onMounted(load)
// 域切换即换库清单（数据按域隔离，网关代理天然保证）
watch(() => domainStore.currentDomain, load)
</script>

<style scoped>
.onto-list { padding: 4px; }

.onto-list__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;
}

.onto-list__title { margin: 0; font-size: 18px; }
.onto-list__intro { margin: 5px 0 0; color: var(--kb-text-tertiary); font-size: 13px; }
.onto-list__btn-icon { margin-right: 4px; }

.onto-list__filters { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.onto-list__search { width: 260px; }

/* ── 库卡片网格（对齐仪表盘 KbCard 的卡片语言）── */
.onto-list__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
  min-height: 60px;
}

.onto-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius);
  padding: 16px;
  cursor: pointer;
  transition: border-color var(--kb-duration) var(--kb-ease),
    box-shadow var(--kb-duration) var(--kb-ease),
    transform var(--kb-duration) var(--kb-ease);
}

.onto-card:hover {
  border-color: var(--kb-accent);
  box-shadow: var(--kb-shadow-card);
  transform: translateY(-1px);
}

.onto-card__head { display: flex; align-items: center; gap: 10px; }

.onto-card__avatar {
  width: 38px;
  height: 38px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--kb-radius);
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
  font-size: 16px;
  font-weight: 700;
}

.onto-card__title-wrap { min-width: 0; }

.onto-card__name {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--kb-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.onto-card__time { font-size: 12px; color: var(--kb-text-tertiary); }

.onto-card__desc {
  margin: 0;
  min-height: 20px;
  font-size: 13px;
  color: var(--kb-text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.onto-card__stats { display: flex; gap: 16px; }

.onto-card__stat { font-size: 12px; color: var(--kb-text-tertiary); }

.onto-card__stat b {
  margin-right: 3px;
  font-size: 15px;
  font-weight: 650;
  color: var(--kb-text-primary);
  font-variant-numeric: tabular-nums;
}

.onto-card__actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  margin-top: -2px;
  padding-top: 8px;
  border-top: 1px dashed var(--kb-border-light);
}

</style>

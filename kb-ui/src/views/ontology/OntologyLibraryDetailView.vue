<template>
  <div class="onto-detail">
    <header class="onto-detail__header">
      <div class="onto-detail__title-wrap">
        <el-button link class="onto-detail__back" @click="router.push({ name: 'ontology-libraries' })">
          <el-icon><ArrowLeft /></el-icon>
        </el-button>
        <span v-if="library" class="onto-detail__avatar" aria-hidden="true">{{ library.name.trim()[0] || '库' }}</span>
        <div class="onto-detail__title-block">
          <h2 class="onto-detail__title">{{ library?.name || '…' }}</h2>
          <div v-if="library" class="onto-detail__meta">
            <span class="onto-detail__stat"><b>{{ library.ontology_count ?? 0 }}</b>本体</span>
            <span class="onto-detail__sep">·</span>
            <span class="onto-detail__stat"><b>{{ library.object_count ?? 0 }}</b>对象</span>
            <span class="onto-detail__sep">·</span>
            <span class="onto-detail__stat"><b>{{ library.relation_count ?? 0 }}</b>关系实例</span>
          </div>
        </div>
      </div>
      <div v-if="canWrite" class="onto-detail__actions">
        <el-button data-test="edit-library" @click="openEdit">
          <el-icon class="onto-detail__btn-icon"><Edit /></el-icon>编辑
        </el-button>
        <el-button type="danger" plain data-test="delete-library" @click="remove">
          <el-icon class="onto-detail__btn-icon"><Delete /></el-icon>删除库
        </el-button>
      </div>
    </header>

    <p v-if="library?.description" class="onto-detail__desc">{{ library.description }}</p>

    <el-tabs v-model="activeTab" class="onto-detail__tabs">
      <el-tab-pane name="ontologies">
        <template #label><el-icon class="onto-detail__tab-icon"><Collection /></el-icon>本体</template>
        <OntologyTab
          :library-id="libraryId"
          :can-write="canWrite"
          :ontologies="ontologies"
          @refresh="refreshAll"
        />
      </el-tab-pane>
      <el-tab-pane name="relations">
        <template #label><el-icon class="onto-detail__tab-icon"><Connection /></el-icon>关系类型</template>
        <RelationTab
          :library-id="libraryId"
          :can-write="canWrite"
          :ontologies="ontologies"
          :relations="relations"
          @refresh="refreshAll"
        />
      </el-tab-pane>
      <el-tab-pane name="objects">
        <template #label><el-icon class="onto-detail__tab-icon"><Box /></el-icon>语义对象</template>
        <ObjectTab
          :library-id="libraryId"
          :can-write="canWrite"
          :ontologies="ontologies"
          :relations="relations"
          @refresh="refreshAll"
        />
      </el-tab-pane>
      <el-tab-pane name="graph" lazy>
        <template #label><el-icon class="onto-detail__tab-icon"><Share /></el-icon>图谱</template>
        <GraphTab
          :library-id="libraryId"
          :ontologies="ontologies"
          :active="activeTab === 'graph'"
          :focus="focusObjectId"
        />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="editVisible" title="编辑本体库" width="500px">
      <el-form label-width="70px">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" maxlength="80" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Box, Collection, Connection, Delete, Edit, Share } from '@element-plus/icons-vue'
import { useOntologyApi } from '@/api/ontology'
import { apiErrorDetail } from '@/api/proxyClient'
import { useAuthStore } from '@/stores/auth'
import { useDomainStore } from '@/stores/domain'
import { canManageDomainOntology } from '@/utils/domainPermissions'
import OntologyTab from '@/components/ontology/OntologyTab.vue'
import RelationTab from '@/components/ontology/RelationTab.vue'
import ObjectTab from '@/components/ontology/ObjectTab.vue'
import GraphTab from '@/components/ontology/GraphTab.vue'
import type { Ontology, OntologyLibrary, OntologyRelation } from '@/types/ontology'

const props = defineProps<{ libraryId: string }>()

const api = useOntologyApi()
const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const domainStore = useDomainStore()

const canWrite = computed(() =>
  canManageDomainOntology(auth.siteRole, domainStore.currentDomainInfo))

const library = ref<OntologyLibrary | null>(null)
const ontologies = ref<Ontology[]>([])
const relations = ref<OntologyRelation[]>([])
// 69号 §4.3：setup 一次性读 route.query.tab（同 KbDetailView 先例）——
// 之后用户手动切 Tab 是纯本地状态，不被残留 query 重置
const TAB_NAMES = new Set(['ontologies', 'relations', 'objects', 'graph'])
const initialTab = typeof route.query.tab === 'string' && TAB_NAMES.has(route.query.tab)
  ? route.query.tab
  : 'ontologies'
const activeTab = ref(initialTab)
// focus 走 computed（响应式）：页面已开时再跳入同库不同对象也能换目标；
// GraphTab 消费后清 query，这里随之归空，无副作用
const focusObjectId = computed(() =>
  typeof route.query.focus === 'string' && route.query.focus ? route.query.focus : '')

/** Tab 子组件写操作后的统一刷新：库计数 + 本体/关系类型共享数据。 */
async function refreshAll() {
  try {
    ;[library.value, ontologies.value, relations.value] = await Promise.all([
      api.getLibrary(props.libraryId),
      api.listOntologies(props.libraryId),
      api.listRelations(props.libraryId),
    ])
  } catch (error) {
    ElMessage.error(`刷新失败：${await apiErrorDetail(error)}`)
  }
}

async function initialLoad() {
  try {
    await refreshAll()
  } catch {
    // refreshAll 已提示错误；标题区保持 '…' 占位即可
  }
}

// ── 库编辑 / 删除 ──
const editVisible = ref(false)
const saving = ref(false)
const editForm = ref({ name: '', description: '' })

function openEdit() {
  editForm.value = { name: library.value?.name || '', description: library.value?.description || '' }
  editVisible.value = true
}

async function save() {
  const name = editForm.value.name.trim()
  if (!name) {
    ElMessage.warning('名称不能为空')
    return
  }
  saving.value = true
  try {
    library.value = await api.updateLibrary(props.libraryId, {
      name,
      description: editForm.value.description.trim() || undefined,
    })
    editVisible.value = false
    ElMessage.success('已保存')
  } catch (error) {
    ElMessage.error(`保存失败：${await apiErrorDetail(error)}`)
  } finally {
    saving.value = false
  }
}

async function remove() {
  try {
    await ElMessageBox.confirm(
      `确定删除本体库「${library.value?.name}」及其全部内容？库内还有本体时会阻止删除。`,
      { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteLibrary(props.libraryId)
    ElMessage.success('已删除')
    await router.push({ name: 'ontology-libraries' })
  } catch (error) {
    ElMessage.error(`删除失败：${await apiErrorDetail(error)}`)
  }
}

onMounted(initialLoad)
// 域切换后库归属变化（跨域 id 直访会被后端 403/404），直接回列表
watch(() => domainStore.currentDomain, () => {
  void router.push({ name: 'ontology-libraries' })
})
</script>

<style scoped>
.onto-detail { padding: 4px; }

.onto-detail__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 8px;
}

.onto-detail__title-wrap { display: flex; align-items: center; gap: 10px; min-width: 0; }

.onto-detail__back { font-size: 18px; color: var(--kb-text-secondary); }

.onto-detail__avatar {
  width: 44px;
  height: 44px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--kb-radius);
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
  font-size: 19px;
  font-weight: 700;
}

.onto-detail__title { margin: 0; font-size: 18px; line-height: 1.3; }

.onto-detail__meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-top: 4px;
  flex-wrap: wrap;
}

.onto-detail__stat { font-size: 12px; color: var(--kb-text-tertiary); }

.onto-detail__stat b {
  margin-right: 3px;
  font-size: 14px;
  font-weight: 650;
  color: var(--kb-text-primary);
  font-variant-numeric: tabular-nums;
}

.onto-detail__sep { color: var(--kb-border); }

.onto-detail__desc {
  margin: 0 0 8px 58px;
  color: var(--kb-text-secondary);
  font-size: 13px;
}

.onto-detail__actions { display: flex; gap: 8px; }
.onto-detail__btn-icon { margin-right: 4px; }

.onto-detail__tabs { margin-top: 4px; }
.onto-detail__tab-icon { margin-right: 5px; vertical-align: -2px; }
</style>

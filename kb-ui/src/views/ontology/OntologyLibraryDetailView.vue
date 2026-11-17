<template>
  <div class="onto-detail">
    <header class="onto-detail__header">
      <div class="onto-detail__title-wrap">
        <el-button link @click="router.push({ name: 'ontology-libraries' })">
          <el-icon><ArrowLeft /></el-icon>
        </el-button>
        <div>
          <h2 class="onto-detail__title">{{ library?.name || '…' }}</h2>
          <div class="onto-detail__meta">
            <el-tag v-if="library" size="small" effect="plain">{{ library.ontology_count ?? 0 }} 本体</el-tag>
            <el-tag v-if="library" size="small" effect="plain">{{ library.object_count ?? 0 }} 对象</el-tag>
            <el-tag v-if="library" size="small" effect="plain">{{ library.relation_count ?? 0 }} 关系实例</el-tag>
            <span v-if="library?.description" class="onto-detail__desc">{{ library.description }}</span>
          </div>
        </div>
      </div>
      <div v-if="canWrite" class="onto-detail__actions">
        <el-button data-test="edit-library" @click="openEdit">编辑</el-button>
        <el-button type="danger" data-test="delete-library" @click="remove">删除库</el-button>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="onto-detail__tabs">
      <el-tab-pane label="本体" name="ontologies">
        <OntologyTab
          :library-id="libraryId"
          :can-write="canWrite"
          :ontologies="ontologies"
          @refresh="refreshAll"
        />
      </el-tab-pane>
      <el-tab-pane label="关系类型" name="relations">
        <RelationTab
          :library-id="libraryId"
          :can-write="canWrite"
          :ontologies="ontologies"
          :relations="relations"
          @refresh="refreshAll"
        />
      </el-tab-pane>
      <el-tab-pane label="语义对象" name="objects">
        <ObjectTab
          :library-id="libraryId"
          :can-write="canWrite"
          :ontologies="ontologies"
          :relations="relations"
          @refresh="refreshAll"
        />
      </el-tab-pane>
      <el-tab-pane label="图谱" name="graph" lazy>
        <GraphTab :library-id="libraryId" :ontologies="ontologies" :active="activeTab === 'graph'" />
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
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
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
const auth = useAuthStore()
const domainStore = useDomainStore()

const canWrite = computed(() =>
  canManageDomainOntology(auth.siteRole, domainStore.currentDomainInfo))

const library = ref<OntologyLibrary | null>(null)
const ontologies = ref<Ontology[]>([])
const relations = ref<OntologyRelation[]>([])
const activeTab = ref('ontologies')

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
.onto-detail__header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 6px; }
.onto-detail__title-wrap { display: flex; align-items: flex-start; gap: 6px; }
.onto-detail__title { margin: 0 0 6px; font-size: 18px; }
.onto-detail__meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.onto-detail__desc { color: var(--kb-text-tertiary); font-size: 13px; }
.onto-detail__tabs { margin-top: 4px; }
</style>

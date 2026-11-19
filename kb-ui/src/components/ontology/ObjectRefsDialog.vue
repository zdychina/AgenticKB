<template>
  <el-dialog
    :model-value="modelValue"
    width="680px"
    append-to-body
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="kb-dialog-title"><el-icon><Link /></el-icon>制品引用 · {{ objectName }}</div>
    </template>

    <div v-loading="loading" class="refs-dialog__list">
      <div
        v-for="ref in items"
        :key="ref.id"
        class="refs-dialog__row"
        :class="{ 'is-degraded': ref.degraded === 'target_missing' }"
        data-test="ref-row"
      >
        <div class="refs-dialog__main">
          <button class="kb-link" type="button" title="打开制品" @click="openArtifact(ref)">
            {{ ref.artifact_title }}
          </button>
          <el-tag size="small" effect="plain">{{ ref.kind === 'wiki' ? 'Wiki' : '表格' }}</el-tag>
          <el-tag size="small" :type="statusTagType(ref.artifact_status)" effect="plain">
            {{ statusLabel(ref.artifact_status) }}
          </el-tag>
          <span class="refs-dialog__target">{{ targetLabel(ref) }}</span>
          <el-tag v-if="ref.degraded === 'target_missing'" size="small" type="info" effect="plain">
            目标已不存在
          </el-tag>
        </div>
        <div class="refs-dialog__sub">
          <span v-if="ref.note" class="refs-dialog__note" :title="ref.note">{{ ref.note }}</span>
          <span class="refs-dialog__meta">{{ formatTime(ref.created_at) }}</span>
          <el-button v-if="canWrite" link type="danger" size="small" @click="removeRef(ref)">删除</el-button>
        </div>
      </div>
      <p v-if="!loading && items.length === 0" class="refs-dialog__empty">
        {{ canWrite ? '暂无制品引用——点「新建引用」关联 wiki 页 / 表格行 / 制品整体' : '暂无制品引用' }}
      </p>
      <p v-if="truncated" class="refs-dialog__truncated">仅显示前 {{ items.length }} 条</p>
    </div>

    <!-- 新建（写者专属）：制品 → 目标（wiki 页清单 / 表格行关键字搜索）→ 备注 -->
    <div v-if="canWrite" class="refs-dialog__actions">
      <el-button v-if="!createVisible" type="primary" plain data-test="open-ref-create" @click="openCreate">
        新建引用
      </el-button>
      <el-form v-else label-width="90px" class="refs-dialog__form" data-test="ref-create-form">
        <el-form-item label="制品">
          <el-select
            v-model="form.artifactId"
            filterable
            :loading="artifactsLoading"
            placeholder="选择同域制品（跨域会被拒绝）"
            style="width: 100%"
            data-test="ref-artifact"
            @change="onArtifactChange"
          >
            <el-option
              v-for="a in artifacts"
              :key="a.artifact_id"
              :value="a.artifact_id"
              :label="`${a.title}（${a.kind === 'wiki' ? 'Wiki' : '表格'}）`"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="目标">
          <el-select
            v-model="form.targetKey"
            :disabled="!form.artifactId"
            filterable
            :remote="isTableTarget"
            :remote-method="searchRows"
            :loading="targetsLoading"
            placeholder="制品整体 / 页 / 行"
            style="width: 100%"
            data-test="ref-target"
          >
            <el-option value="" label="制品整体" data-test="ref-target-whole" />
            <el-option v-for="t in targetOptions" :key="t.value" :value="t.value" :label="t.label" />
          </el-select>
          <div v-if="isTableTarget" class="kb-note">
            行按 row_key / 列值关键字搜索（跟随制品当前版本 r{{ selectedRevision }}）
          </div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" maxlength="500" show-word-limit placeholder="为什么关联（可选）" data-test="ref-note" />
        </el-form-item>
        <el-form-item>
          <el-button @click="createVisible = false">收起</el-button>
          <el-button type="primary" :loading="saving" data-test="submit-ref" @click="submit">创建</el-button>
        </el-form-item>
      </el-form>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Link } from '@element-plus/icons-vue'
import { useOntologyApi } from '@/api/ontology'
import { useArtifactsApi } from '@/api/artifacts'
import { apiErrorDetail } from '@/api/proxyClient'
import type { ObjectArtifactRef } from '@/types/ontology'
import type { Artifact } from '@/types/artifacts'

const props = withDefaults(defineProps<{
  modelValue: boolean
  objectId: string
  objectName: string
  /** 目标选择器按库域过滤制品（同域约束的前端镜像）。 */
  libraryId: string
  /** 写者=canManageDomainOntology；false 时只读列表（图谱抽屉对象卡）。 */
  canWrite?: boolean
}>(), { canWrite: false })

const emit = defineEmits<{ 'update:modelValue': [value: boolean]; changed: [] }>()

const api = useOntologyApi()
const artifactsApi = useArtifactsApi()
const router = useRouter()

// ── 引用列表（服务端已按当前用户制品可见性整行过滤）──
const loading = ref(false)
const items = ref<ObjectArtifactRef[]>([])
const truncated = ref(false)

async function reload(): Promise<void> {
  loading.value = true
  try {
    const page = await api.listObjectRefs(props.objectId, 200)
    items.value = page.items
    truncated.value = page.truncated
  } catch (error) {
    ElMessage.error(`加载制品引用失败：${await apiErrorDetail(error)}`)
  } finally {
    loading.value = false
  }
}

/** 打开即拉列表（对话框复用实例，objectId 随行切换——每次开都重查；
 * immediate 兜底以 true 挂载的调用方）。 */
watch(() => props.modelValue, visible => { if (visible) void reload() }, { immediate: true })

// ── 新建：制品（同域）→ 目标（跟随 current revision）→ 备注 ──
const createVisible = ref(false)
const saving = ref(false)
const artifacts = ref<Artifact[]>([])
const artifactsLoading = ref(false)
let artifactsLoaded = false
const form = ref<{ artifactId: string; targetKey: string; note: string }>({
  artifactId: '', targetKey: '', note: '',
})
const selectedArtifact = ref<Artifact | null>(null)
const targetOptions = ref<Array<{ value: string; label: string }>>([])
const targetsLoading = ref(false)

const isTableTarget = computed(() => selectedArtifact.value?.kind === 'table')
const selectedRevision = computed(() => selectedArtifact.value?.current_revision ?? 0)

async function openCreate(): Promise<void> {
  createVisible.value = true
  if (artifactsLoaded) return
  artifactsLoading.value = true
  try {
    const library = await api.getLibrary(props.libraryId)
    // 同域约束（后端 422 兜底）：选择器只给本库所属域的制品，避免必败选项
    artifacts.value = (await artifactsApi.list({ domain_id: library.domain }))
      .filter(a => a.kind === 'table' || a.kind === 'wiki')
    artifactsLoaded = true
  } catch (error) {
    ElMessage.error(`加载制品清单失败：${await apiErrorDetail(error)}`)
  } finally {
    artifactsLoading.value = false
  }
}

/** 换制品 = 换目标空间：清已选目标并按 kind 预载（wiki 页清单 / 表格行首屏）。 */
async function onArtifactChange(artifactId: string): Promise<void> {
  form.value.targetKey = ''
  targetOptions.value = []
  selectedArtifact.value = artifacts.value.find(a => a.artifact_id === artifactId) ?? null
  if (!selectedArtifact.value) return
  if (selectedArtifact.value.kind === 'wiki') await loadPages()
  else await searchRows('')
}

async function loadPages(): Promise<void> {
  const selected = selectedArtifact.value
  if (!selected) return
  targetsLoading.value = true
  try {
    // 目标校验口径 = current revision（含从未发布的 rev0），与后端一致
    const envelope = await artifactsApi.listWikiPages(selected.artifact_id, {
      revision: selected.current_revision,
    })
    targetOptions.value = envelope.pages.map(p => ({
      value: p.page_key,
      label: p.title || p.page_key,
    }))
  } catch (error) {
    ElMessage.error(`加载页面清单失败：${await apiErrorDetail(error)}`)
  } finally {
    targetsLoading.value = false
  }
}

/** 表格行远程搜索（评审⑦：万行级不翻页选取；row_key/列值包含匹配）。 */
async function searchRows(query: string): Promise<void> {
  const selected = selectedArtifact.value
  if (!selected || selected.kind !== 'table') return
  targetsLoading.value = true
  try {
    const page = await artifactsApi.listRows(selected.artifact_id, {
      revision: selected.current_revision,
      q: query.trim() || undefined,
      size: 50,
    })
    targetOptions.value = page.rows.map(r => ({ value: r.row_key, label: r.row_key }))
  } catch (error) {
    ElMessage.error(`搜索行失败：${await apiErrorDetail(error)}`)
  } finally {
    targetsLoading.value = false
  }
}

async function submit(): Promise<void> {
  if (!form.value.artifactId) {
    ElMessage.warning('请选择制品')
    return
  }
  saving.value = true
  try {
    await api.createObjectRef(props.objectId, {
      artifact_id: form.value.artifactId,
      target_key: form.value.targetKey || undefined,
      note: form.value.note.trim() || undefined,
    })
    ElMessage.success('引用已创建')
    // 创建成功路径重置视图状态：残留的制品/目标选择不带入下一条
    form.value = { artifactId: '', targetKey: '', note: '' }
    selectedArtifact.value = null
    targetOptions.value = []
    createVisible.value = false
    await reload()
    emit('changed')
  } catch (error) {
    ElMessage.error(`创建失败：${await refCreateError(error)}`)
  } finally {
    saving.value = false
  }
}

/** 422 目标不存在时后端附 valid_keys 样例（前 20）——拼进文案帮用户改选。 */
async function refCreateError(error: unknown): Promise<string> {
  const base = await apiErrorDetail(error)
  const detail = (error as {
    response?: { data?: { detail?: { valid_keys?: unknown } } }
  })?.response?.data?.detail
  const keys = Array.isArray(detail?.valid_keys) ? detail.valid_keys.map(String) : []
  return keys.length ? `${base}（可引用键样例：${keys.slice(0, 10).join('、')}）` : base
}

async function removeRef(ref: ObjectArtifactRef): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除对制品「${ref.artifact_title}」的引用？`, { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteObjectRef(props.objectId, ref.id)
    ElMessage.success('已删除')
    await reload()
    emit('changed')
  } catch (error) {
    ElMessage.error(`删除失败：${await apiErrorDetail(error)}`)
  }
}

/** refs 列表项 → 制品页（最小互通：锚点能力按制品页参数面后续再扩）。 */
function openArtifact(ref: ObjectArtifactRef): void {
  void router.push({ name: 'artifact-detail', params: { artifactId: ref.artifact_id } })
}

// ── 展示口径 ──
function targetLabel(ref: ObjectArtifactRef): string {
  if (ref.target_key === '') return '制品整体'
  if (ref.degraded === 'target_missing') return ref.target_key
  return ref.target_title || ref.target_key
}

function statusTagType(status: string): 'info' | 'success' | 'danger' {
  if (status === 'published') return 'success'
  if (status === 'deprecated') return 'danger'
  return 'info'
}

function statusLabel(status: string): string {
  if (status === 'published') return '已发布'
  if (status === 'deprecated') return '已停服'
  return '草稿'
}

function formatTime(value: string): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}
</script>

<style scoped>
.refs-dialog__list { display: flex; flex-direction: column; gap: 8px; max-height: 46vh; overflow-y: auto; }

.refs-dialog__row {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px 10px;
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
  background: var(--kb-bg-card);
}
/* 降级条目（页/行已被移除）：灰显但保留可删（70号 §五） */
.refs-dialog__row.is-degraded { opacity: 0.55; }

.refs-dialog__main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.refs-dialog__target { font-size: 13px; color: var(--kb-text-secondary); }
.refs-dialog__sub { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.refs-dialog__note {
  flex: 1; min-width: 0;
  font-size: 12px; color: var(--kb-text-tertiary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.refs-dialog__meta { font-size: 12px; color: var(--kb-text-tertiary); flex-shrink: 0; }
.refs-dialog__empty { color: var(--kb-text-tertiary); font-size: 13px; margin: 0; }
.refs-dialog__truncated { color: var(--kb-text-tertiary); font-size: 12px; margin: 0; }
.refs-dialog__actions { margin-top: 12px; }
.refs-dialog__form { border-top: 1px solid var(--kb-border-light); padding-top: 12px; }
</style>

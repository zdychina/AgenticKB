<template>
  <div class="pc">
    <div class="pc__toolbar">
      <span class="pc__count">
        草稿修订 {{ product.current_draft_revision ?? '—' }} · 共 {{ objects.length }} 个对象
      </span>
      <div class="pc__actions">
        <el-button
          size="small" :disabled="!selected.length || reviewing"
          @click="review('approve')"
        >
          确认选中（{{ selected.length }}）
        </el-button>
        <el-button
          size="small" type="danger" plain :disabled="!selected.length || reviewing"
          @click="review('reject')"
        >
          驳回选中
        </el-button>
        <el-button size="small" text @click="reload">刷新</el-button>
      </div>
    </div>

    <p v-if="hasConflict" class="pc__warn">
      有对象仍存在未定论的冲突。冲突项不能直接确认——需要先人工改掉那一版定下口径，
      否则等于把冲突掩盖掉，发布门禁也会挡住。
    </p>

    <div class="pc__body">
      <el-table
        v-loading="loading" :data="objects" class="pc__table" highlight-current-row
        row-key="object_id" @current-change="onPick" @selection-change="onSelect"
      >
        <el-table-column type="selection" width="44" :selectable="isSelectable" />
        <el-table-column label="对象" min-width="240">
          <template #default="{ row }">
            <div class="pc__name">{{ row.name || row.object_id }}</div>
            <div class="pc__oid">{{ row.object_id }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="type" label="类型" width="130" />
        <el-table-column label="人审" width="100">
          <template #default="{ row }">
            <el-tag :type="reviewTagType(row.review_status)" size="small" effect="light">
              {{ reviewLabel(row.review_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState text="草稿里还没有对象——先在「制作」页起一次制作" />
        </template>
      </el-table>

      <div class="pc__detail">
        <div v-if="!current" class="pc__placeholder">
          <EmptyState text="选一个对象查看正文与出处" />
        </div>
        <template v-else>
          <div class="pc__detail-head">
            <span class="pc__detail-title">{{ current.object_id }}</span>
            <div>
              <el-button size="small" text @click="showEdits">编辑记录</el-button>
              <el-button v-if="!editing" size="small" @click="startEdit">人工修改</el-button>
              <template v-else>
                <el-button size="small" @click="editing = false">取消</el-button>
                <el-button size="small" type="primary" :loading="saving" @click="saveEdit">
                  保存
                </el-button>
              </template>
            </div>
          </div>

          <template v-if="editing">
            <el-input v-model="draftMd" type="textarea" :rows="18" class="pc__editor" />
            <el-input v-model="editReason" size="small" placeholder="为什么改（会留痕）" />
            <el-input
              v-model="editBasis" size="small" class="pc__basis"
              placeholder="依据是什么（原文位置 / 专家判断 / 口径决定）"
            />
            <p class="pc__edit-hint">
              保存后这个对象会标为「已确认」，此后 Agent 再提交同一对象不会覆盖它，
              而是转为待合并。
            </p>
          </template>
          <pre v-else v-loading="mdLoading" class="pc__md">{{ md }}</pre>
        </template>
      </div>
    </div>

    <el-dialog v-model="editsVisible" title="编辑记录" width="720px">
      <el-table :data="edits">
        <el-table-column prop="created_at" label="时间" width="200" />
        <el-table-column prop="editor" label="谁" width="110" />
        <el-table-column prop="reason" label="为什么" min-width="160" />
        <el-table-column prop="basis" label="依据" min-width="180" />
      </el-table>
      <EmptyState v-if="!edits.length" text="这个对象还没有人工编辑记录" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import EmptyState from '@/components/common/EmptyState.vue'
import { useKnowledgeProductApi } from '@/api/knowledgeProduct'
import { apiErrorDetail } from '@/api/proxyClient'
import { reviewLabel, reviewTagType } from '@/views/product/productMeta'
import type {
  KnowledgeProduct, ObjectEdit, ProductObjectRow, ReviewDecision,
} from '@/types/knowledgeProduct'

const props = defineProps<{ product: KnowledgeProduct; active: boolean }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const api = useKnowledgeProductApi()

const objects = ref<ProductObjectRow[]>([])
const loading = ref(false)
const current = ref<ProductObjectRow | null>(null)
const md = ref('')
const mdLoading = ref(false)
const selected = ref<ProductObjectRow[]>([])
const reviewing = ref(false)

const editing = ref(false)
const draftMd = ref('')
const editReason = ref('')
const editBasis = ref('')
const saving = ref(false)

const edits = ref<ObjectEdit[]>([])
const editsVisible = ref(false)

const hasConflict = computed(() => objects.value.some((o) => o.review_status === 'unresolved'))

/** 冲突项不可勾选——批量确认里混进一个冲突项会让整批裁决被拒。 */
function isSelectable(row: ProductObjectRow) {
  return row.review_status !== 'unresolved'
}

async function reload() {
  loading.value = true
  try {
    objects.value = await api.listObjects(props.product.id)
    if (current.value) {
      const still = objects.value.find((o) => o.object_id === current.value?.object_id)
      current.value = still ?? null
      if (!still) md.value = ''
    }
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    loading.value = false
  }
}

async function onPick(row: ProductObjectRow | null) {
  current.value = row
  editing.value = false
  if (!row) { md.value = ''; return }
  mdLoading.value = true
  try {
    md.value = await api.getObjectMd(props.product.id, row.object_id)
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
    md.value = ''
  } finally {
    mdLoading.value = false
  }
}

function onSelect(rows: ProductObjectRow[]) {
  selected.value = rows
}

async function review(decision: ReviewDecision) {
  reviewing.value = true
  try {
    const decisions: Record<string, ReviewDecision> = {}
    for (const row of selected.value) decisions[row.object_id] = decision
    await api.review(props.product.id, decisions)
    ElMessage.success(decision === 'approve' ? '已确认' : '已驳回')
    selected.value = []
    await reload()
    emit('changed')
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    reviewing.value = false
  }
}

function startEdit() {
  draftMd.value = md.value
  editReason.value = ''
  editBasis.value = ''
  editing.value = true
}

async function saveEdit() {
  if (!current.value) return
  saving.value = true
  try {
    await api.editObject(props.product.id, current.value.object_id, {
      raw_md: draftMd.value,
      reason: editReason.value.trim() || undefined,
      basis: editBasis.value.trim() || undefined,
    })
    editing.value = false
    ElMessage.success('已保存，写入新的草稿修订')
    await reload()
    await onPick(objects.value.find((o) => o.object_id === current.value?.object_id) ?? null)
    emit('changed')
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    saving.value = false
  }
}

async function showEdits() {
  if (!current.value) return
  try {
    edits.value = await api.listObjectEdits(props.product.id, current.value.object_id)
    editsVisible.value = true
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

watch(() => props.active, (on) => { if (on) reload() }, { immediate: true })
watch(() => props.product.current_draft_revision, () => { if (props.active) reload() })
</script>

<style scoped>
.pc__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.pc__count {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.pc__warn {
  margin: 0 0 10px;
  padding: 8px 12px;
  border-radius: 4px;
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning-dark-2);
  font-size: 13px;
}

.pc__body {
  display: grid;
  grid-template-columns: minmax(320px, 1fr) minmax(380px, 1.2fr);
  gap: 16px;
  align-items: start;
}

.pc__name {
  font-size: 13px;
}

.pc__oid {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  word-break: break-all;
}

.pc__detail {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  padding: 12px;
  min-height: 320px;
}

.pc__detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.pc__detail-title {
  font-size: 13px;
  word-break: break-all;
}

.pc__md {
  margin: 0;
  max-height: 460px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.6;
}

.pc__editor {
  margin-bottom: 8px;
}

.pc__basis {
  margin-top: 6px;
}

.pc__edit-hint {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.pc__placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 280px;
}
</style>

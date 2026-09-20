<template>
  <div class="pi">
    <div class="pi__head">
      <div>
        <h4 class="pi__title">报告的问题</h4>
        <p class="pi__hint">
          调用次数只能说明用过，不能说明有用——所以人工补查和修正要记下来。
          处置时记的是**方向**（改资料／改定义／改内容／改 Agent 用法），
          不是一个「已修复」，这样下次才知道该防哪一类。
        </p>
      </div>
      <div class="pi__actions">
        <el-radio-group v-model="filter" size="small" @change="reload">
          <el-radio-button label="">全部</el-radio-button>
          <el-radio-button label="open">待处理</el-radio-button>
          <el-radio-button label="resolved">已处置</el-radio-button>
        </el-radio-group>
        <el-button size="small" type="primary" @click="reportVisible = true">报告问题</el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="issues" size="small">
      <el-table-column label="状态" width="96">
        <template #default="{ row }">
          <el-tag :type="issueStatusTagType(row.status)" size="small" effect="light">
            {{ issueStatusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="problem" label="问题" min-width="220" show-overflow-tooltip />
      <el-table-column label="位置" min-width="180">
        <template #default="{ row }">
          <span v-if="row.object_id" class="pi__where">
            {{ row.object_id }}<template v-if="row.field_name"> · {{ row.field_name }}</template>
          </span>
          <span v-else class="pi__muted">整个制品</span>
        </template>
      </el-table-column>
      <el-table-column prop="used_revision" label="用的版本" width="90" />
      <el-table-column prop="reporter" label="报告人" width="100" />
      <el-table-column label="处置" width="120">
        <template #default="{ row }">
          <span v-if="row.resolution_kind">{{ resolutionLabel(row.resolution_kind) }}</span>
          <span v-else class="pi__muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'open' || row.status === 'triaged'"
            size="small" text @click="openResolve(row)"
          >
            处置
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState text="还没有报告过问题" />
      </template>
    </el-table>

    <el-dialog v-model="reportVisible" title="报告问题" width="600px">
      <el-form :model="form" label-width="88px" label-position="left">
        <el-form-item label="哪里不对" required>
          <el-input v-model="form.problem" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="对象">
          <el-select
            v-model="form.object_id" clearable filterable
            placeholder="不选 = 整个制品" class="pi__select"
          >
            <el-option
              v-for="o in objects" :key="o.object_id"
              :label="o.name || o.object_id" :value="o.object_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="字段">
          <el-input v-model="form.field_name" placeholder="可选" />
        </el-form-item>
        <el-form-item label="当时的任务">
          <el-input v-model="form.task" placeholder="你在做什么的时候发现的" />
        </el-form-item>
        <el-form-item label="修正依据">
          <el-input
            v-model="form.correction_basis" type="textarea" :rows="2"
            placeholder="原文位置 / 专家判断 / 口径决定"
          />
        </el-form-item>
      </el-form>
      <p class="pi__hint">
        默认记在当前发布版本
        <template v-if="product.released_revision != null">
          （第 {{ product.released_revision }} 版）
        </template>
        ——报告问题的人用的是对外那一份，不是负责人的草稿。
      </p>
      <template #footer>
        <el-button @click="reportVisible = false">取消</el-button>
        <el-button type="primary" :loading="reporting" @click="submitReport">提交</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resolveVisible" title="处置问题" width="560px">
      <p class="pi__problem">{{ current?.problem }}</p>
      <el-form :model="resolution" label-width="88px" label-position="left">
        <el-form-item label="结论" required>
          <el-radio-group v-model="resolution.status">
            <el-radio value="triaged">已分诊</el-radio>
            <el-radio value="resolved">已处置</el-radio>
            <el-radio value="rejected">不予处理</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="处置方向">
          <el-select v-model="resolution.resolution_kind" clearable class="pi__select">
            <el-option
              v-for="opt in RESOLUTION_OPTIONS" :key="opt.value"
              :label="opt.label" :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="resolution.resolution_note" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resolveVisible = false">取消</el-button>
        <el-button type="primary" :loading="resolving" @click="submitResolve">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import EmptyState from '@/components/common/EmptyState.vue'
import { useKnowledgeProductApi } from '@/api/knowledgeProduct'
import { apiErrorDetail } from '@/api/proxyClient'
import {
  RESOLUTION_OPTIONS, issueStatusLabel, issueStatusTagType, resolutionLabel,
} from '@/views/product/productMeta'
import type {
  IssueStatus, KnowledgeProduct, ProductIssue, ProductObjectRow, ResolutionKind,
} from '@/types/knowledgeProduct'

const props = defineProps<{ product: KnowledgeProduct; active: boolean }>()

const api = useKnowledgeProductApi()

const issues = ref<ProductIssue[]>([])
const objects = ref<ProductObjectRow[]>([])
const loading = ref(false)
const filter = ref<'' | IssueStatus>('')

const reportVisible = ref(false)
const reporting = ref(false)
const form = reactive({
  problem: '', object_id: '', field_name: '', task: '', correction_basis: '',
})

const resolveVisible = ref(false)
const resolving = ref(false)
const current = ref<ProductIssue | null>(null)
const resolution = reactive<{
  status: IssueStatus; resolution_kind: ResolutionKind | ''; resolution_note: string
}>({ status: 'resolved', resolution_kind: '', resolution_note: '' })

async function reload() {
  loading.value = true
  try {
    issues.value = await api.listIssues(
      props.product.id, filter.value === '' ? undefined : filter.value,
    )
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    loading.value = false
  }
  try {
    objects.value = await api.listObjects(props.product.id)
  } catch {
    objects.value = []
  }
}

async function submitReport() {
  if (!form.problem.trim()) {
    ElMessage.warning('先说清楚哪里不对')
    return
  }
  reporting.value = true
  try {
    await api.reportIssue(props.product.id, {
      problem: form.problem.trim(),
      object_id: form.object_id || undefined,
      field_name: form.field_name.trim() || undefined,
      task: form.task.trim() || undefined,
      correction_basis: form.correction_basis.trim() || undefined,
    })
    reportVisible.value = false
    form.problem = ''
    form.object_id = ''
    form.field_name = ''
    form.task = ''
    form.correction_basis = ''
    await reload()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    reporting.value = false
  }
}

function openResolve(issue: ProductIssue) {
  current.value = issue
  resolution.status = 'resolved'
  resolution.resolution_kind = ''
  resolution.resolution_note = ''
  resolveVisible.value = true
}

async function submitResolve() {
  if (!current.value) return
  resolving.value = true
  try {
    await api.resolveIssue(props.product.id, current.value.id, {
      status: resolution.status,
      resolution_kind: resolution.resolution_kind || undefined,
      resolution_note: resolution.resolution_note.trim() || undefined,
    })
    resolveVisible.value = false
    await reload()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    resolving.value = false
  }
}

watch(() => props.active, (on) => { if (on) reload() }, { immediate: true })
</script>

<style scoped>
.pi__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
}

.pi__title {
  margin: 0 0 4px;
  font-size: 14px;
}

.pi__hint {
  margin: 0;
  max-width: 640px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.pi__actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.pi__where {
  font-size: 12px;
  word-break: break-all;
}

.pi__muted {
  color: var(--el-text-color-secondary);
}

.pi__select {
  width: 100%;
}

.pi__problem {
  margin: 0 0 12px;
  padding: 8px 12px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
  font-size: 13px;
}
</style>

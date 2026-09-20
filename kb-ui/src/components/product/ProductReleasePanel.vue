<template>
  <div class="pr">
    <section class="pr__section">
      <div class="pr__head">
        <h4 class="pr__title">发布门禁</h4>
        <el-button size="small" text :loading="gateLoading" @click="loadGate">重新检查</el-button>
      </div>
      <p class="pr__hint">
        针对当前草稿修订 {{ product.current_draft_revision ?? '—' }}。
        逐项列出——被挡住时得看得见卡在哪一条。
      </p>

      <div v-if="gate" class="pr__checks">
        <div
          v-for="check in gate.checks" :key="check.code"
          class="pr__check" :class="{ 'pr__check--bad': !check.passed }"
        >
          <el-icon class="pr__check-icon">
            <CircleCheck v-if="check.passed" />
            <CircleClose v-else />
          </el-icon>
          <div class="pr__check-body">
            <div class="pr__check-label">{{ gateLabel(check.code) }}</div>
            <div class="pr__check-detail">{{ check.detail }}</div>
            <div v-if="check.offenders.length" class="pr__offenders">
              <el-tag
                v-for="item in check.offenders.slice(0, 8)" :key="item"
                size="small" type="danger" effect="plain"
              >
                {{ item }}
              </el-tag>
              <span v-if="check.offenders.length > 8" class="pr__more">
                等 {{ check.offenders.length }} 项
              </span>
            </div>
          </div>
        </div>
      </div>

      <div class="pr__publish">
        <el-button
          type="primary" :disabled="!gate?.passed" :loading="publishing"
          @click="doPublish(false)"
        >
          发布
        </el-button>
        <el-button
          v-if="gate && !gate.passed" type="warning" plain :loading="publishing"
          @click="confirmForce"
        >
          带缺口强制发布
        </el-button>
        <span v-if="product.released_revision != null" class="pr__released">
          当前对外服务的是修订 {{ product.released_revision }}
        </span>
      </div>
    </section>

    <section class="pr__section">
      <div class="pr__head">
        <h4 class="pr__title">试用记录</h4>
        <el-button size="small" @click="trialVisible = true">登记试用</el-button>
      </div>
      <p class="pr__hint">
        试用绑定具体修订——换了修订不自动继承。调用次数只能说明用过，不能说明有用。
      </p>
      <el-table :data="trials" size="small">
        <el-table-column prop="question" label="问题" min-width="220" show-overflow-tooltip />
        <el-table-column label="结论" width="90">
          <template #default="{ row }">
            <el-tag :type="verdictTagType(row.verdict)" size="small" effect="light">
              {{ verdictLabel(row.verdict) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="comment" label="评语" min-width="180" show-overflow-tooltip />
        <el-table-column prop="tried_by" label="试用人" width="110" />
        <template #empty>
          <EmptyState text="本修订还没有试用记录" />
        </template>
      </el-table>
    </section>

    <section class="pr__section">
      <h4 class="pr__title">审核记录</h4>
      <el-table :data="reviews" size="small">
        <el-table-column prop="created_at" label="时间" width="200" />
        <el-table-column prop="reviewer" label="审核人" width="110" />
        <el-table-column label="结论" width="90">
          <template #default="{ row }">
            <el-tag :type="row.decision === 'approved' ? 'success' : 'danger'" size="small">
              {{ row.decision === 'approved' ? '通过' : '驳回' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="涉及对象" width="100">
          <template #default="{ row }">
            {{ Object.keys(row.per_object_json || {}).length }}
          </template>
        </el-table-column>
        <el-table-column prop="notes" label="意见" min-width="220" show-overflow-tooltip />
        <template #empty>
          <EmptyState text="本修订还没有审核记录" />
        </template>
      </el-table>
    </section>

    <el-dialog v-model="trialVisible" title="登记一条试用" width="560px">
      <el-form :model="trial" label-width="88px" label-position="left">
        <el-form-item label="问题" required>
          <el-input v-model="trial.question" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="回答">
          <el-input v-model="trial.answer" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="期望要点">
          <el-input v-model="trial.expected_points" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="结论" required>
          <el-radio-group v-model="trial.verdict">
            <el-radio value="passed">通过</el-radio>
            <el-radio value="failed">未通过</el-radio>
            <el-radio value="inconclusive">不确定</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="评语">
          <el-input v-model="trial.comment" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="trialVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingTrial" @click="saveTrial">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { CircleCheck, CircleClose } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import EmptyState from '@/components/common/EmptyState.vue'
import { useKnowledgeProductApi } from '@/api/knowledgeProduct'
import { apiErrorDetail } from '@/api/proxyClient'
import { gateLabel, verdictLabel, verdictTagType } from '@/views/product/productMeta'
import type {
  KnowledgeProduct, PublishGate, ReviewRecord, TrialRecord, TrialVerdict,
} from '@/types/knowledgeProduct'

const props = defineProps<{ product: KnowledgeProduct; active: boolean }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const api = useKnowledgeProductApi()

const gate = ref<PublishGate | null>(null)
const gateLoading = ref(false)
const trials = ref<TrialRecord[]>([])
const reviews = ref<ReviewRecord[]>([])
const publishing = ref(false)

const trialVisible = ref(false)
const savingTrial = ref(false)
const trial = reactive({
  question: '', answer: '', expected_points: '',
  verdict: 'passed' as TrialVerdict, comment: '',
})

function escapeHtml(value: string): string {
  const map: Record<string, string> = {
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }
  return value.replace(/[&<>"']/g, (ch) => map[ch])
}

async function loadGate() {
  gateLoading.value = true
  try {
    gate.value = await api.publishGate(props.product.id)
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    gateLoading.value = false
  }
}

async function loadRest() {
  try {
    ;[trials.value, reviews.value] = await Promise.all([
      api.listTrials(props.product.id),
      api.listReviews(props.product.id),
    ])
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

async function reload() {
  await Promise.all([loadGate(), loadRest()])
}

async function saveTrial() {
  if (!trial.question.trim()) {
    ElMessage.warning('问题要填')
    return
  }
  savingTrial.value = true
  try {
    await api.recordTrial(props.product.id, {
      question: trial.question.trim(),
      verdict: trial.verdict,
      answer: trial.answer.trim() || undefined,
      expected_points: trial.expected_points.trim() || undefined,
      comment: trial.comment.trim() || undefined,
    })
    trialVisible.value = false
    trial.question = ''
    trial.answer = ''
    trial.expected_points = ''
    trial.comment = ''
    await reload()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    savingTrial.value = false
  }
}

async function confirmForce() {
  const blocking = gate.value?.checks.filter((c) => !c.passed) ?? []
  // 逐条列出卡住的项：强制发布是个需要知情的动作，不能只给一句「确定吗」。
  // detail 里嵌着对象 ID，而对象 ID 来自用户提交的 md —— 用 HTML 渲染就必须转义。
  const lines = blocking
    .map((c) => `<li>${escapeHtml(gateLabel(c.code))}：${escapeHtml(c.detail)}</li>`)
    .join('')
  try {
    await ElMessageBox.confirm(
      `<p>以下 ${blocking.length} 项未通过：</p><ul>${lines}</ul>` +
        '<p>强制发布会把这份门禁结果原样留痕，事后查得到是谁放的行。</p>',
      '带缺口发布',
      {
        type: 'warning',
        dangerouslyUseHTMLString: true,
        confirmButtonText: '仍然发布',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  await doPublish(true)
}

async function doPublish(force: boolean) {
  publishing.value = true
  try {
    const result = await api.publish(props.product.id, { force })
    ElMessage.success(`已发布修订 ${result.released_revision}`)
    emit('changed')
    await reload()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    publishing.value = false
  }
}

watch(() => props.active, (on) => { if (on) reload() }, { immediate: true })
watch(() => props.product.current_draft_revision, () => { if (props.active) reload() })
</script>

<style scoped>
.pr__section {
  margin-bottom: 24px;
}

.pr__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.pr__title {
  margin: 0 0 4px;
  font-size: 14px;
}

.pr__hint {
  margin: 0 0 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.pr__checks {
  display: grid;
  gap: 8px;
}

.pr__check {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.pr__check--bad {
  border-color: var(--el-color-danger-light-5);
  background: var(--el-color-danger-light-9);
}

.pr__check-icon {
  margin-top: 2px;
  color: var(--el-color-success);
}

.pr__check--bad .pr__check-icon {
  color: var(--el-color-danger);
}

.pr__check-label {
  font-size: 13px;
}

.pr__check-detail {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.pr__offenders {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.pr__more,
.pr__released {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.pr__publish {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 14px;
}
</style>

<template>
  <div class="pn">
    <div class="pn__head">
      <div>
        <h4 class="pn__title">制作实例</h4>
        <p class="pn__hint">
          起一次制作会签发一张短期任务票据，平台把它发给外部 Agent 会话。
          <strong>票据明文只出现一次</strong>，此后任何页面都不回显。
        </p>
      </div>
      <div class="pn__actions">
        <el-button size="small" type="primary" :loading="starting" @click="start">
          起一次制作
        </el-button>
        <el-button size="small" plain @click="revokeAll">撤销全部票据</el-button>
        <el-button size="small" text @click="reload">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="!harnessReady" type="info" show-icon :closable="false" class="pn__notice"
      title="外部 Agent 服务尚未接入"
    >
      起制作实例与签票已可用，但把票据送进 Agent 会话、跟随多轮过程这一段还没接。
      当前能看到的是实例状态与提交回执；完整的对话与工具活动要等适配器接上。
    </el-alert>

    <el-table v-loading="loading" :data="instances" size="small">
      <el-table-column prop="id" label="实例" min-width="200" show-overflow-tooltip />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="instanceTagType(row.status)" size="small" effect="light">
            {{ instanceLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="base_draft_revision" label="起始修订" width="90" />
      <el-table-column prop="created_by" label="发起人" width="110" />
      <el-table-column prop="created_at" label="创建时间" width="200" />
      <el-table-column label="操作" width="90">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'pending' || row.status === 'running'"
            size="small" text type="danger" @click="cancel(row.id)"
          >
            取消
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState text="还没有起过制作" />
      </template>
    </el-table>

    <h4 class="pn__title pn__title--spaced">提交回执</h4>
    <p class="pn__hint">
      成果只能经 <code>submit_creation_result</code> 进入草稿。被拒的提交同样留痕——
      否则「Agent 说交了但草稿里没有」无从对账。
    </p>
    <el-table :data="submissions" size="small">
      <el-table-column prop="created_at" label="时间" width="200" />
      <el-table-column label="结果" width="100">
        <template #default="{ row }">
          <el-tag :type="outcomeTagType(row.outcome)" size="small" effect="light">
            {{ outcomeLabel(row.outcome) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="based_on_draft_revision" label="基于" width="70" />
      <el-table-column prop="written_revision" label="写入" width="70" />
      <el-table-column prop="accepted_count" label="接收" width="70" />
      <el-table-column prop="rejected_count" label="拒收" width="70" />
      <el-table-column label="说明" min-width="220">
        <template #default="{ row }">
          <span class="pn__msg">{{ receiptMessage(row) }}</span>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState text="还没有提交回执" />
      </template>
    </el-table>

    <el-dialog v-model="ticketVisible" title="任务票据已签发" width="620px">
      <el-alert type="warning" show-icon :closable="false" class="pn__notice">
        这串票据**只显示这一次**。它决定 Agent 能读哪些资料、能写到哪个草稿——
        按最小权限对待，不要贴进聊天记录或日志。
      </el-alert>
      <el-input :model-value="issuedToken" readonly type="textarea" :rows="3" />
      <p class="pn__hint">有效期至 {{ issuedExpiry }}</p>
      <template #footer>
        <el-button @click="copyToken">复制</el-button>
        <el-button type="primary" @click="closeTicket">我已保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import EmptyState from '@/components/common/EmptyState.vue'
import { useKnowledgeProductApi } from '@/api/knowledgeProduct'
import { apiErrorDetail } from '@/api/proxyClient'
import {
  instanceLabel, instanceTagType, outcomeLabel, outcomeTagType,
} from '@/views/product/productMeta'
import type {
  CreationInstance, KnowledgeProduct, SubmissionRecord,
} from '@/types/knowledgeProduct'

const props = defineProps<{ product: KnowledgeProduct; active: boolean }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const api = useKnowledgeProductApi()

/** 真适配器接上之前，过程回流那一段还没有后端——不要假装有。 */
const harnessReady = ref(false)

const instances = ref<CreationInstance[]>([])
const submissions = ref<SubmissionRecord[]>([])
const loading = ref(false)
const starting = ref(false)

const ticketVisible = ref(false)
const issuedToken = ref('')
const issuedExpiry = ref('')

function receiptMessage(row: SubmissionRecord): string {
  const receipt = row.receipt_json as { message?: string } | null
  return receipt?.message ?? ''
}

async function reload() {
  loading.value = true
  try {
    ;[instances.value, submissions.value] = await Promise.all([
      api.listInstances(props.product.id),
      api.listSubmissions(props.product.id),
    ])
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    loading.value = false
  }
}

async function start() {
  starting.value = true
  try {
    const started = await api.startInstance(props.product.id)
    issuedToken.value = started.ticket.token
    issuedExpiry.value = started.ticket.expires_at
    ticketVisible.value = true
    await reload()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    starting.value = false
  }
}

async function cancel(instanceId: string) {
  try {
    const { value } = await ElMessageBox.prompt('取消原因', '取消制作', {
      inputValue: '负责人取消',
    })
    await api.cancelInstance(props.product.id, instanceId, value || '负责人取消')
    ElMessage.success('已取消并撤票')
    await reload()
  } catch {
    // 用户取消对话框
  }
}

async function revokeAll() {
  try {
    await ElMessageBox.confirm(
      '资料范围收缩或制品定义变更后，旧票据不能按旧范围继续提交。撤销后在用的票据立即失效。',
      '撤销全部票据',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const result = await api.revokeTickets(props.product.id, '资料范围或定义已变更')
    ElMessage.success(`已撤销 ${result.revoked} 张票据`)
    await reload()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

async function copyToken() {
  try {
    await navigator.clipboard.writeText(issuedToken.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选中')
  }
}

function closeTicket() {
  ticketVisible.value = false
  issuedToken.value = ''
  emit('changed')
}

watch(() => props.active, (on) => { if (on) reload() }, { immediate: true })
</script>

<style scoped>
.pn__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.pn__title {
  margin: 0 0 4px;
  font-size: 14px;
}

.pn__title--spaced {
  margin-top: 26px;
}

.pn__hint {
  margin: 0 0 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.pn__actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.pn__notice {
  margin-bottom: 12px;
}

.pn__msg {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>

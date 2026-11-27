<template>
  <section class="validation-panel">
    <div class="validation-panel__title">
      校验结果
      <span :class="titleClass">{{ titleText }}</span>
    </div>
    <p v-if="validating" class="validation-panel__empty">服务端校验中…</p>
    <ul v-else-if="allIssues.length">
      <li v-for="(item, index) in allIssues" :key="`${item.code}-${index}`">
        <code>{{ item.code }}</code>
        <span>{{ item.message }}</span>
        <small v-if="item.nodeId">{{ item.nodeId }}</small>
      </li>
    </ul>
    <template v-else-if="serverPassed">
      <p class="validation-panel__ok">✓ 服务端校验通过<span v-if="serverCheckedAt"> · {{ serverCheckedAt }}</span></p>
      <p v-if="planSummary" class="validation-panel__plan">{{ planSummary }}</p>
    </template>
    <p v-else class="validation-panel__empty">本地规则通过；发布前仍需服务端校验。</p>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { MiningWorkflowValidationResult, WorkflowValidationIssue } from '@/types/miningWorkflow'

const props = defineProps<{
  localIssues: WorkflowValidationIssue[]
  serverResult?: MiningWorkflowValidationResult | null
  validating?: boolean
  serverCheckedAt?: string | null
}>()

const allIssues = computed(() => [
  ...props.localIssues,
  ...(props.serverResult?.errors ?? []).map(error => ({
    code: error.kind,
    message: error.message,
    nodeId: error.nodeId,
    severity: 'error' as const,
  })),
])

// BUG-10：此前面板只看 issues 数，不读 serverResult.valid——服务端校验通过后
// 文案仍是"仍需服务端校验"，用户无法判断校验是否真的执行过。serverPassed
// 单独区分出 server-pass 态（有 issues 时优先列 issues，通过态才展示）。
const serverPassed = computed(() => !!props.serverResult?.valid && !(props.serverResult?.errors?.length))

// 执行顺序与编译器/运行时一致：input → document → global 三段 zone 顺序拼接（节点 id）。
const planSummary = computed(() => {
  const plan = props.serverResult?.executionPlan
  if (!plan) return ''
  const order = [...(plan.inputOrder ?? []), ...(plan.documentOrder ?? []), ...(plan.globalOrder ?? [])]
  return order.length ? `执行顺序：${order.join(' → ')}` : ''
})

const titleClass = computed(() => {
  if (props.validating) return 'is-running'
  return allIssues.value.length ? 'is-error' : 'is-ok'
})

const titleText = computed(() => {
  if (props.validating) return '校验中…'
  if (allIssues.value.length) return `${allIssues.value.length} 项问题`
  return serverPassed.value ? '服务端通过' : '本地通过'
})
</script>

<style scoped>
.validation-panel { font-size: 12px; }
.validation-panel__title { display: flex; justify-content: space-between; font-weight: 700; }
.validation-panel__title .is-error { color: #dc2626; }
.validation-panel__title .is-ok { color: #16a34a; }
.validation-panel__title .is-running { color: #d97706; }
.validation-panel__ok { color: #16a34a; line-height: 1.5; }
.validation-panel__plan { color: var(--kb-text-tertiary); font-size: 11px; line-height: 1.6; word-break: break-all; }
.validation-panel__empty { color: var(--kb-text-tertiary); line-height: 1.5; }
.validation-panel ul { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 7px; }
.validation-panel li { display: grid; grid-template-columns: auto 1fr; gap: 3px 8px; padding: 7px; border-radius: 6px; background: #fef2f2; }
.validation-panel li code { color: #b91c1c; }
.validation-panel li small { grid-column: 2; color: var(--kb-text-tertiary); }
</style>

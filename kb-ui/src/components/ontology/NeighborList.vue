<template>
  <div class="neighbor-list">
    <div v-for="edge in edges" :key="edge.id" class="neighbor-list__row">
      <span class="neighbor-list__arrow" :title="directionLabel === '→' ? '出边' : '入边'">{{ directionLabel }}</span>
      <button class="kb-link neighbor-list__name" type="button" title="查看对端对象" @click="$emit('jump', edge.other_id)">
        {{ edge.other_name }}
      </button>
      <el-tag size="small" effect="plain" round>{{ edge.relation_name }}</el-tag>
      <span class="neighbor-list__ontology">{{ edge.other_ontology_name }}</span>
      <el-button
        v-if="canWrite"
        link
        type="danger"
        size="small"
        class="neighbor-list__remove"
        @click="$emit('remove', edge)"
      >解除</el-button>
    </div>
    <p v-if="edges.length === 0" class="neighbor-list__empty">无</p>
  </div>
</template>

<script setup lang="ts">
import type { ObjectNeighborEdge } from '@/types/ontology'

defineProps<{
  edges: ObjectNeighborEdge[]
  /** 出边 → / 入边 ←（行首方向符号）。 */
  directionLabel: string
  canWrite: boolean
}>()

defineEmits<{
  remove: [edge: ObjectNeighborEdge]
  jump: [objectId: string]
}>()
</script>

<style scoped>
/* 邻接行做成轻卡片：抽屉里一眼一圈边，悬停浮出可点感 */
.neighbor-list { display: flex; flex-direction: column; gap: 6px; }

.neighbor-list__row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 7px 10px;
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
  background: var(--kb-bg-card);
  transition: border-color var(--kb-duration) var(--kb-ease);
}

.neighbor-list__row:hover { border-color: var(--kb-accent-medium, rgba(8, 145, 178, 0.15)); }

.neighbor-list__arrow {
  color: var(--kb-accent);
  font-weight: 700;
}

.neighbor-list__ontology { color: var(--kb-text-tertiary); font-size: 12px; }
.neighbor-list__remove { margin-left: auto; }
.neighbor-list__empty { color: var(--kb-text-tertiary); font-size: 13px; margin: 0; }
</style>

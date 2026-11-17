<template>
  <div class="neighbor-list">
    <div v-for="edge in edges" :key="edge.id" class="neighbor-list__row">
      <span class="neighbor-list__arrow">{{ directionLabel }}</span>
      <button class="neighbor-list__name" type="button" title="查看对端对象" @click="$emit('jump', edge.other_id)">
        {{ edge.other_name }}
      </button>
      <el-tag size="small" effect="plain">{{ edge.relation_name }}</el-tag>
      <span class="neighbor-list__ontology">{{ edge.other_ontology_name }}</span>
      <el-button
        v-if="canWrite"
        link
        type="danger"
        size="small"
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
.neighbor-list { display: flex; flex-direction: column; gap: 6px; }
.neighbor-list__row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.neighbor-list__arrow { color: var(--kb-text-tertiary); }
.neighbor-list__name { border: 0; background: none; padding: 0; color: var(--kb-accent, #2563eb); font-weight: 600; cursor: pointer; }
.neighbor-list__ontology { color: var(--kb-text-tertiary); font-size: 12px; }
.neighbor-list__empty { color: var(--kb-text-tertiary); font-size: 13px; margin: 0; }
</style>

<template>
  <div v-if="items.length > 0" class="art-refs" data-test="artifact-refs">
    <span class="art-refs__label">{{ label }}</span>
    <template v-for="ref in items" :key="ref.id">
      <!-- object_invisible（防御占位）：灰显不可点——正常流程到不了这里 -->
      <button
        v-if="ref.object_name"
        class="art-refs__chip"
        type="button"
        :title="ref.ontology_name ? `${ref.ontology_name} · 点击在库图谱中聚焦` : '点击在库图谱中聚焦'"
        data-test="artifact-refs-chip"
        @click="jump(ref)"
      >{{ ref.object_name }}<span v-if="ref.ontology_name" class="art-refs__onto">· {{ ref.ontology_name }}</span></button>
      <span v-else class="art-refs__chip is-muted" data-test="artifact-refs-invisible">对象不可见</span>
    </template>
    <span v-if="truncated" class="art-refs__more">等 {{ items.length }}+ 个</span>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useOntologyApi } from '@/api/ontology'
import type { ArtifactObjectRef } from '@/types/ontology'

/**
 * 制品侧「相关对象」chips（70号 §六）：wiki 页头（target_key=page_key）、
 * 表格行详情（target_key=row_key）、制品整体级（target_key=''）三面共用。
 * 反查走 GET /ontology/artifact-refs；chips 是增强信息——拉取失败静默隐藏
 * （与 ArtifactDetailView 检索状态条同口径），不留空白占位。
 */
const props = withDefaults(defineProps<{
  artifactId: string
  /** ''=制品整体级；wiki=page_key；table=row_key。 */
  targetKey: string
  label?: string
  /** 拉取上限（默认 20，与对象卡摘要口径一致）。 */
  limit?: number
}>(), { label: '相关对象：', limit: 20 })

const api = useOntologyApi()
const router = useRouter()

const items = ref<ArtifactObjectRef[]>([])
const truncated = ref(false)

watch(
  () => [props.artifactId, props.targetKey] as const,
  async ([artifactId, targetKey]) => {
    // targetKey='' 是有效的整体级过滤——不可当 falsy 跳过；挂载方用 v-if
    // 控制未选页/未选行时不渲染本组件
    if (!artifactId) {
      items.value = []
      truncated.value = false
      return
    }
    try {
      const page = await api.listArtifactRefs(artifactId, targetKey, props.limit)
      items.value = page.items
      truncated.value = page.truncated
    } catch {
      items.value = []
      truncated.value = false
    }
  },
  { immediate: true },
)

/** chips → 库图谱聚焦（69号 §4.3 focus 链路复用，含截断兜底由图谱侧处理）。 */
function jump(ref: ArtifactObjectRef): void {
  void router.push({
    name: 'ontology-library-detail',
    params: { libraryId: ref.library_id },
    query: { tab: 'graph', focus: ref.object_id },
  })
}
</script>

<style scoped>
.art-refs {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.art-refs__label {
  color: var(--kb-text-tertiary);
  font-size: 12.5px;
  flex-shrink: 0;
}

.art-refs__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid var(--kb-border-light);
  background: var(--kb-accent-soft, rgba(8, 145, 178, 0.06));
  color: var(--kb-accent, #0891b2);
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color var(--kb-duration) var(--kb-ease);
}

.art-refs__chip:hover { border-color: var(--kb-accent, #0891b2); }

.art-refs__chip.is-muted {
  color: var(--kb-text-tertiary);
  background: transparent;
  cursor: default;
}

.art-refs__onto { color: var(--kb-text-tertiary); font-size: 11px; }
.art-refs__more { color: var(--kb-text-tertiary); font-size: 12px; }
</style>

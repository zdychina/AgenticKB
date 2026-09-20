<template>
  <div class="pd">
    <div class="pd__header">
      <router-link to="/products" class="pd__back">
        <el-icon><ArrowLeft /></el-icon>
        <span>知识制品</span>
      </router-link>
      <h2 class="pd__title">{{ product?.name || '加载中…' }}</h2>
      <el-tag
        v-if="product" :type="lifecycleTagType(product.lifecycle_status)"
        size="small" effect="light"
      >
        {{ lifecycleLabel(product.lifecycle_status) }}
      </el-tag>
      <el-tag v-if="product" size="small" effect="plain">
        草稿 {{ product.current_draft_revision ?? '—' }}
      </el-tag>
      <el-tag v-if="product?.released_revision != null" size="small" type="success" effect="plain">
        已发布 {{ product.released_revision }}
      </el-tag>
    </div>

    <p v-if="product?.purpose" class="pd__purpose">{{ product.purpose }}</p>

    <el-alert
      v-if="sourceReport && sourceReport.alerts.length"
      :type="sourceReport.blocking ? 'error' : 'warning'"
      show-icon :closable="false" class="pd__source-alert"
      :title="sourceReport.blocking
        ? `引用的资料已失效或被收回（${sourceReport.alerts.length} 项）——这份内容不应再被当作依据`
        : `引用的资料有 ${sourceReport.alerts.length} 项已更新或废弃，建议复核`"
    >
      <el-button size="small" text @click="activeTab = 'setup'">到「制作设置」看明细</el-button>
    </el-alert>

    <div v-if="!loading && loadError" class="pd__missing">
      <EmptyState :text="loadError" />
      <el-button type="primary" @click="reload">重试</el-button>
    </div>

    <el-tabs v-else-if="product" v-model="activeTab" class="pd__tabs">
      <el-tab-pane label="制作设置" name="setup">
        <dl class="pd__meta">
          <dt>制品 ID</dt><dd>{{ product.id }}</dd>
          <dt>类型</dt><dd>{{ product.product_type }}</dd>
          <dt>负责人</dt><dd>{{ product.owner }}</dd>
          <dt>用途</dt><dd>{{ product.purpose || '—' }}</dd>
          <dt>创建</dt><dd>{{ product.created_at }}</dd>
          <dt>更新</dt><dd>{{ product.updated_at }}</dd>
        </dl>
        <ProductSetupPanel
          :product="product" :active="activeTab === 'setup'" @changed="reload"
        />
      </el-tab-pane>

      <el-tab-pane label="内容" name="content">
        <ProductContentPanel
          :product="product" :active="activeTab === 'content'" @changed="reload"
        />
      </el-tab-pane>

      <el-tab-pane label="制作" name="runs">
        <ProductRunsPanel
          :product="product" :active="activeTab === 'runs'" @changed="reload"
        />
      </el-tab-pane>

      <el-tab-pane label="试用与发布" name="release">
        <ProductReleasePanel
          :product="product" :active="activeTab === 'release'" @changed="reload"
        />
      </el-tab-pane>

      <el-tab-pane name="issues">
        <template #label>
          <span>问题</span>
          <el-badge v-if="openIssues" :value="openIssues" class="pd__badge" type="warning" />
        </template>
        <ProductIssuesPanel :product="product" :active="activeTab === 'issues'" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ProductContentPanel from '@/components/product/ProductContentPanel.vue'
import ProductIssuesPanel from '@/components/product/ProductIssuesPanel.vue'
import ProductReleasePanel from '@/components/product/ProductReleasePanel.vue'
import ProductRunsPanel from '@/components/product/ProductRunsPanel.vue'
import ProductSetupPanel from '@/components/product/ProductSetupPanel.vue'
import { useKnowledgeProductApi } from '@/api/knowledgeProduct'
import { apiErrorDetail } from '@/api/proxyClient'
import { lifecycleLabel, lifecycleTagType } from '@/views/product/productMeta'
import type { KnowledgeProduct, SourceAlertReport } from '@/types/knowledgeProduct'

const props = defineProps<{ productId: string }>()

const api = useKnowledgeProductApi()
const product = ref<KnowledgeProduct | null>(null)
const loading = ref(false)
const loadError = ref('')
const activeTab = ref('content')
const sourceReport = ref<SourceAlertReport | null>(null)
const openIssues = ref(0)

async function reload() {
  loading.value = true
  loadError.value = ''
  try {
    product.value = await api.get(props.productId)
  } catch (e) {
    loadError.value = await apiErrorDetail(e)
  } finally {
    loading.value = false
  }
  await loadBadges()
}

/** 资料告警与待处理问题数是整页的抬头信息——不该只有切到某个 tab 才看得到。 */
async function loadBadges() {
  try {
    sourceReport.value = await api.sourceAlerts(props.productId)
  } catch {
    sourceReport.value = null
  }
  try {
    openIssues.value = (await api.listIssues(props.productId, 'open')).length
  } catch {
    openIssues.value = 0
  }
}

onMounted(reload)
</script>

<style scoped>
.pd__header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pd__back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--el-text-color-secondary);
  text-decoration: none;
  font-size: 13px;
}

.pd__title {
  margin: 0;
  font-size: 20px;
}

.pd__purpose {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.pd__tabs {
  margin-top: 12px;
}

.pd__meta {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 6px 16px;
  margin: 0 0 16px;
  font-size: 13px;
}

.pd__meta dt {
  color: var(--el-text-color-secondary);
}

.pd__meta dd {
  margin: 0;
  word-break: break-all;
}

.pd__missing {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 48px 0;
}

.pd__source-alert {
  margin-top: 12px;
}

.pd__badge {
  margin-left: 6px;
}
</style>

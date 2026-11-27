<template>
  <div class="not-found-view">
    <EmptyState :icon="WarningFilled" :text="t('common.notFound.text')">
      <template #action>
        <!-- BUG-09：从登录页 redirect 回跳落到 404 时补一句解释，其余场景不出现 -->
        <p v-if="badRedirect" class="not-found-view__hint">{{ t('common.notFound.badRedirect') }}</p>
        <div class="not-found-view__actions">
          <el-button type="primary" @click="goHome">{{ t('common.notFound.backHome') }}</el-button>
          <!-- 直接地址栏进入（无内部来路）时没有「上一页」，隐藏按钮只留返回首页 -->
          <el-button v-if="canGoBack" @click="goBack">{{ t('common.notFound.backPrev') }}</el-button>
        </div>
      </template>
    </EmptyState>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { WarningFilled } from '@element-plus/icons-vue'
import { t } from '@/locales/t'
import EmptyState from '@/components/common/EmptyState.vue'

const route = useRoute()
const router = useRouter()

// 两种来路都算「登录后跳转目标无效」：URL 带 redirect 且指向当前路径（文档建议的检测），
// 或内部来路是 /login（redirectAfterLogin 原样 push 后 query 已清空，靠 history.state 辨别）
const badRedirect = computed(() => {
  if (typeof route.query.redirect === 'string' && route.query.redirect === route.path) return true
  const back = router.options.history.state.back
  return typeof back === 'string' && back.split('?')[0] === '/login'
})

const canGoBack = computed(() => router.options.history.state.back != null)

function goHome(): void {
  void router.push('/')
}

function goBack(): void {
  router.back()
}
</script>

<style scoped>
.not-found-view {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
}

.not-found-view__hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--kb-text-tertiary);
}

.not-found-view__actions {
  display: flex;
  gap: 8px;
  justify-content: center;
}
</style>

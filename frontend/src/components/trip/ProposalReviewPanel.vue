<template>
          <section class="proposal-review" data-testid="proposal-diff">
            <h3>本次改排的变化与确认条件</h3>
            <a-alert v-if="detailsError" type="warning" :message="detailsError" />
            <a-alert v-if="confirmError" data-testid="proposal-confirm-error" type="error" show-icon :message="confirmError" />
            <template v-if="details">
              <a-alert v-if="details.stale" data-testid="proposal-stale" type="error" :message="proposalConflictMessage(details.invalidation_reason)" />
              <p v-if="details.diff?.summary">{{ details.diff.summary }}</p>
              <p v-if="details.diff?.cost_delta_cents != null">费用估算变化：{{ details.diff.cost_delta_cents >= 0 ? '+' : '' }}¥{{ (details.diff.cost_delta_cents / 100).toFixed(2) }}（未知费用仍单列）</p>
              <ul v-if="changes.length"><li v-for="(change, i) in changes" :key="i">{{ change }}</li></ul>
              <p v-else>没有活动增删记录；请核对每日时间和路线变化。</p>
              <p v-if="details.dependencies?.planning_revision != null">基于规划输入修订 {{ details.dependencies.planning_revision }}</p>
              <p v-if="details.dependencies?.version != null || details.dependencies?.trip_version != null">基于原行程版本 {{ details.dependencies.trip_version ?? details.dependencies.version }}</p>
              <p v-if="details.revision_scope?.day_indices?.length">允许修改第 {{ details.revision_scope.day_indices.map((d: number) => d + 1).join('、') }} 天</p>
              <p v-for="note in details.diff?.unresolved || []" :key="note">待解决：{{ note }}</p>
            </template>
            <a-button size="small" :loading="busy" @click="emit('refresh')">刷新提案依赖</a-button>
          </section>
</template>
<script setup lang="ts">
import { proposalConflictMessage } from '@/utils/proposalConflict'
defineProps<{ details: any; detailsError: string; confirmError: string; changes: string[]; busy: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
</script>
<style scoped>
.proposal-review { background: #fff; color: #25324a; border: 1px solid #dfe5ed; border-radius: 10px; padding: 18px; margin: 12px 0; }
</style>

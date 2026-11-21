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
              <section v-for="day in comparisons" :key="day.index" class="day-comparison">
                <h4>{{ day.date }} · 第 {{ day.index + 1 }} 天</h4>
                <p>计划接驳 {{ day.beforeTravel }} → {{ day.afterTravel }}</p>
                <ul v-if="day.times.length"><li v-for="item in day.times" :key="item">{{ item }}</li></ul>
                <template v-if="day.fixed.length">
                  <p><strong>固定安排</strong> · {{ day.status }}</p>
                  <ul>
                    <li v-for="item in day.fixed" :key="item.label">
                      {{ item.label }}<span v-if="item.preserved"> · 时间保留</span>
                      <p v-if="item.connection">{{ item.connection }}</p>
                    </li>
                  </ul>
                </template>
              </section>
              <details v-if="details.dependencies || details.revision_scope?.day_indices?.length">
                <summary>查看改排范围与版本</summary>
                <p v-if="details.dependencies?.planning_revision != null">基于规划输入修订 {{ details.dependencies.planning_revision }}</p>
                <p v-if="details.dependencies?.version != null || details.dependencies?.trip_version != null">基于原行程版本 {{ details.dependencies.trip_version ?? details.dependencies.version }}</p>
                <p v-if="details.revision_scope?.day_indices?.length">允许修改第 {{ details.revision_scope.day_indices.map((d: number) => d + 1).join('、') }} 天</p>
              </details>
              <p v-for="note in details.diff?.unresolved || []" :key="note">待解决：{{ note }}</p>
            </template>
            <a-button size="small" :loading="busy" @click="emit('refresh')">刷新提案依赖</a-button>
          </section>
</template>
<script setup lang="ts">
import { Alert as AAlert, Button as AButton } from 'ant-design-vue'

import { computed } from 'vue'
import { proposalConflictMessage } from '@/utils/proposalConflict'
import { orderedTimeline, tripTime } from '@/utils/tripDay'
import { scheduleCurrent } from '@/utils/scheduleValidity'
import type { AssistantProposal, DayPlan, TimelineItem } from '@/types'
const props = defineProps<{ details: AssistantProposal | null; detailsError: string; confirmError: string; changes: string[]; busy: boolean; zone: string }>()
const emit = defineEmits<{ refresh: [] }>()
function time(value?: string | null) {
  const stamp = Date.parse(value || '')
  return Number.isFinite(stamp) ? tripTime(stamp, props.zone) : '待核验'
}
function travel(day?: DayPlan) {
  const legs = day?.timeline?.filter(item => item.kind === 'travel') || []
  if (!legs.length || legs.some(item => !item.start_at || !item.end_at)) return '待核验'
  const sum = legs.reduce((total, item) => total + Date.parse(item.end_at!) - Date.parse(item.start_at!), 0)
  return Number.isFinite(sum) ? Math.round(sum / 60000) + ' 分钟' +
    (!day || !scheduleCurrent(day) || legs.some(item => item.status !== 'confirmed') || props.details?.stale ? '（待核验）' : '') : '待核验'
}
const stamp = (value?: string | null) => value ? Date.parse(value) : 0
const stableTime = (left: TimelineItem, right?: TimelineItem) =>
  !!right && stamp(left.start_at) === stamp(right.start_at) && stamp(left.end_at) === stamp(right.end_at)
function stationConnection(day: DayPlan, fixed: TimelineItem) {
  const rows = orderedTimeline(day)
  const sameBooking = (row: TimelineItem) => row.evidence_refs?.some(ref => fixed.evidence_refs?.includes(ref))
  const buffers = rows.filter(row => row.kind === 'waiting' && row.activity_id?.startsWith('boarding:'))
  const boarding = buffers.find(row => sameBooking(row) && row.status === 'confirmed' && stamp(row.end_at) === stamp(fixed.start_at))
  if (!boarding || props.details?.stale || !scheduleCurrent(day)) return undefined
  const route = [...rows].reverse().find(row => row.kind === 'travel' && row.status === 'confirmed' &&
    ['前往出发站', '住宿前往跨城出发站'].includes(row.title) && (sameBooking(row) || buffers.length === 1) &&
    stamp(row.end_at) <= stamp(boarding.start_at))
  if (!route?.end_at || !boarding.start_at || !boarding.end_at) return '赴站接驳待核验'
  const minutes = Math.round((stamp(boarding.end_at) - stamp(boarding.start_at)) / 60000)
  return '计划 ' + time(route.end_at) + ' 到站，满足已确认的提前 ' + minutes + ' 分钟要求。'
}
const comparisons = computed(() => (props.details?.diff.days || []).flatMap(({ day_index, before, after }) => {
  if (!after) return []
  const entries = (day?: DayPlan) => (day?.timeline || []).filter(item => ['attraction','meal','commitment'].includes(item.kind))
  const originals = new Map(entries(before).map(item => [item.activity_id, item]))
  const changed = entries(after).filter(item => !stableTime(item, originals.get(item.activity_id)))
  const format = (item: TimelineItem) => time(item.start_at) + '–' + time(item.end_at) + ' ' + item.title
  return [{ index: day_index, date: after.date, beforeTravel: travel(before), afterTravel: travel(after),
    times: changed.map(item => {
      const prior = originals.get(item.activity_id)
      return prior ? time(prior.start_at) + '–' + time(prior.end_at) + ' → ' + format(item) : '新增 ' + format(item)
    }),
    fixed: entries(after).filter(item => item.kind === 'commitment').map(item => ({
      label: format(item), preserved: stableTime(item, originals.get(item.activity_id)), connection: stationConnection(after, item)
    })),
    status: props.details?.stale ? '请刷新提案后重新核验' :
      after.feasibility?.status === 'feasible' ? '满足已知时间约束' :
      after.feasibility?.status === 'infeasible' ? '存在时间冲突' : '接驳或预约条件仍需核验' }]
}))
</script>
<style scoped>
.day-comparison { padding: 10px 0; border-top: 1px solid #e2e8f0; }
.proposal-review details { margin: 0 0 8px; }
.proposal-review { background: #fff; color: #25324a; border: 1px solid #dfe5ed; border-radius: 10px; padding: 18px; margin: 12px 0; }
</style>

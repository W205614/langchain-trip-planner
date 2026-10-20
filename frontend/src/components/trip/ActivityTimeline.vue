<template>
  <section v-if="day.timeline?.length || day.feasibility" class="timeline" :aria-label="`第 ${day.day_index + 1} 天时间轴`">
    <header><h3>{{ day.city ? `${day.city} · ` : '' }}每日时间轴</h3><a-tag :color="feasibilityColor">{{ feasibilityText }}</a-tag></header>
    <p class="hint">估算和未核实安排不等于已确认营业或完成预订。点击有位置的活动可在地图定位。</p>
    <a-alert v-for="issue in [...(day.feasibility?.conflicts || []), ...(day.feasibility?.unknown || [])]" :key="issue" type="warning" :message="issueText(issue)" class="issue" />
    <ol>
      <li v-for="(item, index) in day.timeline || []" :key="`${item.activity_id || index}-${item.kind}`" :data-testid="item.activity_id ? `activity-${item.activity_id}` : undefined" :class="{ selected: selected === item.activity_id }">
        <div class="time">{{ time(item.start_at) }}<span>{{ time(item.end_at) }}</span></div>
        <div class="activity-copy">
          <button v-if="item.location" class="title locate" type="button" @click="emit('locate', item); selected = item.activity_id">{{ item.title }} ↗</button><strong v-else class="title">{{ item.title }}</strong>
          <div class="tags"><a-tag>{{ kindLabel(item.kind) }}</a-tag><a-tag :color="item.status === 'confirmed' ? 'green' : 'gold'">{{ item.status === 'confirmed' ? '已确认安排' : item.status === 'estimated' ? '时间估算' : '待核实' }}</a-tag><a-tag v-if="locked(item.activity_id)" color="blue">已锁定</a-tag><a-tag v-if="completed(item.activity_id)">已完成</a-tag></div>
          <p v-for="note in item.notes || []" :key="note">{{ issueText(note) }}</p>
          <details v-if="item.source || item.evidence_refs?.length"><summary>查看依据</summary><p v-if="item.source">{{ item.source }}</p><p v-for="(evidence, i) in item.evidence_refs || []" :key="i">{{ typeof evidence === 'string' ? evidence : evidence.source || evidence.id }}</p></details>
        </div>
        <div v-if="editable && item.activity_id && ['attraction', 'meal', 'commitment'].includes(item.kind)" class="actions">
          <a-button size="small" :disabled="busy || completed(item.activity_id)" @click="emit('state', item.activity_id, { locked: !locked(item.activity_id), lock_mode: 'content' })">{{ locked(item.activity_id) ? '解除锁定' : '锁定安排' }}</a-button>
          <a-button v-if="!locked(item.activity_id) && item.start_at && item.end_at" size="small" :disabled="busy || completed(item.activity_id)" @click="emit('state', item.activity_id, { locked: true, lock_mode: 'time' })">锁定时段</a-button>
          <a-button size="small" :disabled="busy" @click="emit('state', item.activity_id, { completed: !completed(item.activity_id) })">{{ completed(item.activity_id) ? '撤销完成' : '标记完成' }}</a-button>
        </div>
      </li>
    </ol>
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import type { DayPlan, PlanningInput, TimelineItem } from '@/types'
const props = defineProps<{ day: DayPlan; planning?: PlanningInput; editable?: boolean; busy?: boolean }>()
const emit = defineEmits<{ locate: [item: TimelineItem]; state: [id: string, state: { locked?: boolean; completed?: boolean; lock_mode?: string }] }>()
const selected = ref<string>()
const time = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('zh-CN', { timeZone: props.planning?.timezone || 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '待计算'
const kindLabel = (kind: string) => ({ attraction: '游览', meal: '用餐', commitment: '固定安排', hotel: '住宿', travel: '交通', waiting: '等待' } as Record<string, string>)[kind] || kind
const locked = (id?: string) => props.planning?.locks?.some(lock => lock.activity_id === id) || false
const completed = (id?: string) => !!id && !!props.planning?.completed_activity_ids?.includes(id)
const feasibilityText = computed(() => ({ feasible: '满足已知时间约束', unverified: '部分条件待核实', not_found: '计算预算内未找到方案', infeasible: '当前条件下无可行解' } as Record<string, string>)[props.day.feasibility?.status || 'unverified'])
const feasibilityColor = computed(() => props.day.feasibility?.status === 'feasible' ? 'green' : props.day.feasibility?.status === 'infeasible' ? 'red' : 'gold')
function issueText(issue: string) {
  if (/[\u3400-\u9fff]/.test(issue)) return issue
  const separator = issue.indexOf(':'), code = separator < 0 ? issue : issue.slice(0, separator), id = separator < 0 ? '' : issue.slice(separator + 1)
  const title = props.day.timeline?.find(item => item.activity_id === id)?.title || props.planning?.commitments?.find(item => String(item.id) === id)?.title
  const labels: Record<string, string> = {
    opening_unknown_or_stale: '景点营业信息未知或已过期，请核对出行日期的开放与最后入场时间。', opening_conflict: '景点营业信息存在冲突，请根据官方来源核对。',
    meal_opening_unverified: '餐厅营业时间尚未核实。', meal_opening_conflict: '餐厅营业信息存在冲突。', meal_location_unknown: '餐厅位置未知，接驳时间尚无法核实。',
    route_endpoint_unknown: '部分路线起终点位置未知，交通时间尚未核实。', route_duration_estimated_30_minutes: '部分路段暂按 30 分钟估算，请核实真实交通时间。',
    commitment_time_unknown: '固定事项缺少明确起止时间，请补充后重新校验。', invalid_commitment: '固定事项时间无效，请检查起止时间与日期。',
    locked_duration_changed: '活动时长与锁定时段冲突，请检查锁定事项。', invalid_time_lock: '锁定时段无效，请重新确认起止时间。', no_time_window: '没有可用时间窗口，请检查营业、预约与每日时间边界。',
    cross_city_confirmed_transport_missing: '跨城行程缺少已确认的交通衔接，请补充班次、起终点和到离时间。', protected_activity_excluded: '当前约束无法保留锁定或已完成活动，请调整允许修改的范围。',
    daily_candidate_limit_exceeded: '当天候选超过计算预算，请减少可选活动或分步规划。', hotel_endpoint_unknown: '住宿位置未知，酒店与活动间的接驳时间尚未核实。',
    candidate_time_model_has_no_solution: '当前候选与已知时间约束无法同时满足，请调整可选活动或时间条件。', solver_budget_exhausted: '本次计算预算已用尽，尚未找到满足条件的方案。',
    'Resume to continue scheduling remaining days': '本次计算预算已用尽，可恢复任务继续安排剩余日期。',
  }
  return `${title ? `${title}：` : ''}${labels[code] || '此项安排仍需核实，请检查时间、位置与固定条件。'}`
}
</script>
<style scoped>
.timeline { border: 1px solid #dfe5ed; border-radius: 10px; padding: 18px; margin: 18px 0; background: #fbfcfe; }header{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;}h3{font-size:16px;margin:0;} .hint { color:#667085;font-size:13px;line-height:1.6; }.issue{margin:8px 0;}ol{list-style:none;padding:0;margin:18px 0 0;}li{display:flex;gap:14px;padding:16px 0;border-top:1px solid #e7ebf0;align-items:flex-start;}.time{font-variant-numeric:tabular-nums;font-size:15px;font-weight:600;min-width:55px;}.time span{display:block;font-size:12px;color:#788292;margin-top:5px;}.activity-copy{flex:1;min-width:0;}.title{font-size:15px;color:#25324a;}.locate{background:none;border:0;padding:0;cursor:pointer;text-align:left;color:#2a5ca7;font-weight:600;}.tags{display:flex;flex-wrap:wrap;margin-top:8px;gap:4px;}.activity-copy p{font-size:13px;color:#596579;margin:8px 0 0;overflow-wrap:anywhere;}summary{font-size:12px;color:#596579;cursor:pointer;margin-top:8px;}.actions{display:flex;flex-wrap:wrap;gap:6px;max-width:190px;}.selected{background:#edf4fc;}@media(max-width:720px){li{flex-wrap:wrap;}.actions{margin-left:69px;max-width:100%;}.timeline{padding:12px;}}
</style>

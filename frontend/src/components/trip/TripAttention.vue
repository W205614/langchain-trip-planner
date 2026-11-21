<template>
  <section v-if="items.length || unavailable" class="trip-attention" aria-labelledby="attention-heading">
    <h2 id="attention-heading">优先处理事项</h2>
    <p v-if="unavailable" role="status">暂时无法读取已保存的行前核验。以下只列出行程中可见的事项。</p>
    <ul>
      <li v-for="item in items.slice(0, expanded ? items.length : 3)" :key="item.key">
        <div>
          <strong>{{ item.date }} · {{ item.place || '当天行程' }} · {{ item.title }}</strong>
          <p>{{ item.message }}</p>
          <small>{{ item.status }} · {{ item.source }}<template v-if="item.observed"> · {{ formatTime(item.observed) }}</template><template v-if="item.acknowledged"> · 已知悉</template></small>
        </div>
        <div class="attention-actions">
          <a-button v-if="editable && item.fact" size="small" @click="$emit('fact', item)">补充信息</a-button>
          <a-button v-if="editable" size="small" @click="$emit('revise', item)">调整{{ item.activityId ? '这项安排' : '这一天' }}</a-button>
          <a-button size="small" type="link" @click="$emit('locate', item)">查看安排</a-button>
        </div>
      </li>
    </ul>
    <a-button v-if="items.length > 3" type="link" :aria-expanded="expanded" @click="expanded = !expanded">
      {{ expanded ? '收起其他事项' : '查看全部 ' + items.length + ' 项' }}
    </a-button>
  </section>
</template>

<script setup lang="ts">
import { Button as AButton } from 'ant-design-vue'
import { computed, ref } from 'vue'
import { factChecklist } from '@/utils/factChecklist'
import type { FactGap } from '@/utils/factChecklist'
import { scheduleCurrent } from '@/utils/scheduleValidity'
import { tripDate, tripZone } from '@/utils/tripDay'
import type { DailyTripCheck, PlanningInput, TripPlan } from '@/types'

const props = defineProps<{ plan: TripPlan; planning?: PlanningInput; checks: DailyTripCheck[]; editable: boolean; unavailable: boolean; now: number }>()
type Attention = {
  key: string; date: string; day: number; place?: string; activityId?: string; title: string; message: string
  source: string; status: string; priority: number; observed?: string; acknowledged?: boolean; fact?: FactGap
}
defineEmits<{ fact: [item: Attention]; revise: [item: Attention]; locate: [item: Attention] }>()
const expanded = ref(false)
const input = computed(() => props.planning || props.plan.planning_input)
const checkLabels: Record<string, string> = {
  hours_changed: '营业时间有变化', hours_unknown: '营业时间待确认', weather_attention: '预报有恶劣天气', weather_changed: '天气预报有变化'
}
const transportLabels: Record<string, string> = {
  fixed_transport_outside_day_window: '固定交通超出当天时间窗口',
  origin_connection_outside_day_window: '接驳时间不足，无法按预计安排到站',
  origin_hotel_connection_overlaps_previous_day: '赴站接驳与前一天安排冲突',
  transport_predeparture_buffer_unverified: '提前到站时间待核实',
  daily_duration_limit_exceeded: '固定交通和接驳超出当天时长限制',
  no_time_window: '无法安排在可用时间窗口内',
  cross_city_confirmed_transport_missing: '跨城交通仍待确认'
}
const items = computed(() => {
  const result: Attention[] = []
  const today = tripDate(props.now, tripZone(input.value))
  for (const day of props.plan.days) {
    const base = { date: day.date, day: day.day_index }
    const places = [...day.attractions, ...day.meals]
    const gaps = input.value ? factChecklist(props.plan, input.value, day.day_index, props.now) : []
    const check = props.checks.find(entry => entry.day_index === day.day_index)
    for (const risk of check?.risks || []) {
      if (!checkLabels[risk.code]) continue
      const place = places.find(entry => entry.poi_id === risk.poi_id)
      const stale = check?.stale || (check?.expires_at && Date.parse(check.expires_at) <= props.now)
      const fact = place?.poi_id ? { kind: 'opening' as const, poiId: place.poi_id, key: 'opening:' + place.poi_id, label: place.name, status: '待确认' as const } : undefined
      result.push({ ...base, key: 'check:' + day.day_index + ':' + risk.id, place: place?.name,
        activityId: place?.activity_id, title: checkLabels[risk.code], message: risk.message,
        source: risk.source === 'amap_rest' ? '高德地图' : risk.source, observed: risk.observed_at || check?.checked_at,
        status: stale ? '核验已过期' : '待核验', priority: 1, acknowledged: !!risk.acknowledged_at, fact })
    }
    for (const fact of gaps) {
      if (fact.kind === 'opening' && fact.status === '未补充' &&
          check?.risks.some(risk => risk.poi_id === fact.poiId && ['hours_changed', 'hours_unknown'].includes(risk.code))) continue
      const place = places.find(entry => entry.poi_id === fact.poiId)
      const commitment = input.value?.commitments.find(entry => String(entry.id) === fact.commitmentId)
      const conflict = ['声明闭馆', '声明冲突', '尚无可用预约'].includes(fact.status)
      const declarations = fact.kind === 'opening' ? input.value?.opening_windows.filter(entry => entry.poi_id === fact.poiId && entry.date === day.date)
        : fact.kind === 'reservation' ? input.value?.reservation_facts?.filter(entry => entry.poi_id === fact.poiId && entry.date === day.date)
        : input.value?.transport_buffers?.filter(entry => entry.commitment_id === fact.commitmentId || entry.commitment_id === commitment?.input_commitment_id)
      result.push({ ...base, key: day.day_index + ':' + fact.key, place: place?.name || commitment?.title,
        activityId: place?.activity_id, title: fact.kind === 'reservation' ? '预约' : fact.kind === 'opening' ? '营业与最后入场' : '提前到站',
        message: fact.kind === 'boarding' ? '核对承运方要求的提前到站时间。' : fact.status === '声明闭馆'
          ? '该地点在出行当地日期关闭，可考虑替换。' : fact.kind === 'opening'
            ? '核对开放时间和最后入场时间。' : '核对当天是否需要预约以及是否已取得预约。',
        source: declarations?.map(entry => entry.source).filter(Boolean).join('、') || '行程核验清单',
        observed: declarations?.[0]?.observed_at, status: fact.status, priority: conflict ? 0 : 2, fact })
    }
    const addSchedule = (issue: string, conflict: boolean) => {
      const [code, ...parts] = issue.split(':')
      if (!transportLabels[code]) return
      const identity = parts.join(':')
      const value = places.find(entry => entry.activity_id === identity)
      const commitment = input.value?.commitments.find(entry =>
        String(entry.id) === identity || 'commitment:' + entry.id === identity || entry.activity_id === identity)
      if (!conflict && gaps.some(gap => gap.kind === 'boarding' && gap.commitmentId === String(commitment?.id))) return
      const stale = !!day.timeline?.length && !scheduleCurrent(day, props.now)
      result.push({ ...base, key: day.day_index + ':' + issue, place: value?.name || commitment?.title,
        activityId: value?.activity_id, title: transportLabels[code], message: conflict ? '查看当天时间轴与固定事项。' : '请核对承运方要求或补充当天交通。',
        source: '行程排程', status: stale ? '排程待重新核验' : conflict ? '安排冲突' : '待核验',
        priority: conflict && !stale ? 0 : 1 })
    }
    for (const issue of day.feasibility?.conflicts || []) addSchedule(issue, true)
    for (const issue of day.feasibility?.unknown || []) addSchedule(issue, false)
  }
  return result.sort((a, b) => Number(a.date < today) - Number(b.date < today) ||
    a.priority - b.priority || a.date.localeCompare(b.date))
})

function formatTime(time: string) {
  const value = Date.parse(time)
  return Number.isFinite(value) ? new Intl.DateTimeFormat('zh-CN', { timeZone: tripZone(input.value), dateStyle: 'short', timeStyle: 'short' }).format(value) : ''
}
</script>

<style scoped>
.trip-attention { padding: 16px; border: 1px solid #efdbb4; border-radius: 10px; margin-bottom: 16px; background: #fffdf7; }
h2 { font-size: 18px; margin: 0 0 8px; }
ul { padding: 0; margin: 0; list-style: none; }
li { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; padding: 10px 0; border-top: 1px solid #eee4d2; }
p { margin: 4px 0; }
small { color: #594d3d; }
.attention-actions { display: flex; flex-wrap: wrap; gap: 6px; align-items: start; }
</style>

<template>
  <section v-if="today && selectedDay" class="trip-today" aria-labelledby="today-heading">
    <header>
      <h2 id="today-heading">{{ selectedDay.date === today.date ? '今天的行程' : selectedDay.date + ' 的行程' }}</h2>
      <label>查看日期
        <select :value="selectedDay.day_index" @change="choose">
          <option v-for="day in plan.days" :key="day.day_index" :value="day.day_index">{{ day.date }} · 第 {{ day.day_index + 1 }} 天</option>
        </select>
      </label>
      <a-button size="small" @click="$emit('full')">查看全程</a-button>
    </header>
    <div v-if="next" class="next-stop">
      <h3>{{ inProgress ? '正在进行' : '下一站' }} · {{ next.title }}</h3>
      <p v-if="departure !== undefined">计划 {{ tripTime(departure, zone) }} 出发</p>
      <p v-else>出发时间待核验</p>
      <p v-if="place?.entrance">已确认入口 {{ place.entrance.name }} · {{ place.entrance.source }}</p>
      <p v-if="mapUrl && !place?.entrance?.location">地图定位<template v-if="!place?.entrance"> · 具体入口待确认</template></p>
      <p v-if="place?.address" class="stop-address">{{ place.address }}</p>
      <a-space wrap>
        <a-button v-if="place?.address" size="small" @click="copyAddress(place.address)">复制地址</a-button>
        <a v-if="mapUrl" :href="mapUrl" target="_blank" rel="noopener noreferrer">用高德打开</a>
      </a-space>
    </div>
    <p v-else>{{ selectedDay.timeline?.length ? '这一天没有剩余的待进行活动。' : '这一天尚无可用时间轴，请查看当天安排。' }}</p>
    <a-button v-if="editable && canRevise" @click="$emit('remaining', selectedDay.day_index)">调整今天剩余安排</a-button>
  </section>
</template>

<script setup lang="ts">
import { Button as AButton, Space as ASpace, message } from 'ant-design-vue'
import { computed } from 'vue'
import type { PlanningInput, TripPlan } from '@/types'
import { activityPlace, amapLink, orderedTimeline, tripDate, tripTime, tripZone } from '@/utils/tripDay'
import { scheduleCurrent } from '@/utils/scheduleValidity'

const props = defineProps<{ plan: TripPlan; planning?: PlanningInput; now: number; selected?: number; editable: boolean }>()
const emit = defineEmits<{ day: [day: number]; full: []; remaining: [day: number] }>()
const input = computed(() => props.planning || props.plan.planning_input)
const zone = computed(() => tripZone(input.value))
const today = computed(() => props.plan.days.find(day => day.date === tripDate(props.now, zone.value)))
const selectedDay = computed(() => props.plan.days.find(day => day.day_index === props.selected) || today.value)
const timeline = computed(() => orderedTimeline(selectedDay.value))
const next = computed(() => timeline.value.find(item =>
  ['attraction', 'meal', 'commitment', 'hotel'].includes(item.kind) &&
  !input.value?.completed_activity_ids.includes(item.activity_id || '') &&
  (selectedDay.value?.date !== today.value?.date || !item.end_at || Date.parse(item.end_at) > props.now)))
const inProgress = computed(() => selectedDay.value?.date === today.value?.date && Date.parse(next.value?.start_at || '') <= props.now)
const canRevise = computed(() => selectedDay.value?.date === today.value?.date && timeline.value.some(item =>
  ['attraction', 'meal'].includes(item.kind) && Date.parse(item.start_at || '') > props.now &&
  !input.value?.completed_activity_ids.includes(item.activity_id || '') &&
  !input.value?.locks.some(lock => lock.activity_id === item.activity_id)))
const place = computed(() => selectedDay.value && next.value ? activityPlace(selectedDay.value, next.value, input.value, props.now) : undefined)
const mapUrl = computed(() => next.value && amapLink(place.value?.location, place.value?.entrance?.location ? place.value.entrance.name : next.value.title, selectedDay.value?.transportation))
const departure = computed(() => {
  const day = selectedDay.value, item = next.value
  if (!day || !item || !scheduleCurrent(day, props.now) || place.value?.entrance?.location) return undefined
  const previous = timeline.value.slice(0, timeline.value.indexOf(item)).reverse().find(entry => entry.kind !== 'waiting')
  const start = Date.parse(previous?.start_at || '')
  return previous?.kind === 'travel' && previous.status === 'confirmed' && Number.isFinite(start) ? start : undefined
})
function choose(event: Event) {
  emit('day', Number((event.target as HTMLSelectElement).value))
}
async function copyAddress(address: string) {
  try {
    await navigator.clipboard.writeText(address)
    message.success('地址已复制')
  } catch {
    message.info('当前浏览器无法直接复制，请选中地址复制。')
  }
}
</script>

<style scoped>
.trip-today { padding: 16px; border: 1px solid #cedde1; border-radius: 10px; margin-bottom: 16px; background: #f7fcfc; }
header { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
h2 { font-size: 19px; margin: 0 auto 0 0; }
h3 { font-size: 17px; margin: 0 0 8px; }
label { display: flex; align-items: center; gap: 6px; }
select { min-height: 30px; max-width: 100%; }
.next-stop { margin: 12px 0; padding: 12px 0; border-block: 1px solid #dce8ea; }
.next-stop p { margin: 0 0 6px; }
.stop-address { user-select: text; overflow-wrap: anywhere; }
</style>

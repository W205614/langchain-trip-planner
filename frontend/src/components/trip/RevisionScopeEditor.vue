<template>
  <section data-testid="revision-scope" class="revision-scope">
    <label>允许调整的范围<a-select v-model:value="mode" aria-label="允许调整的范围" @change="update"><a-select-option value="day">指定日期</a-select-option><a-select-option value="activity">选定活动</a-select-option><a-select-option value="window">当天时间段／剩余安排</a-select-option><a-select-option value="multiple">多个日期</a-select-option></a-select></label>
    <label v-if="mode === 'multiple'">允许改动的日期<a-select v-model:value="selectedDays" mode="multiple" aria-label="允许改动的日期" @change="update"><a-select-option v-for="day in plan.days" :key="day.day_index" :value="day.day_index">第 {{ day.day_index + 1 }} 天 · {{ day.date }}</a-select-option></a-select></label>
    <label v-if="mode === 'activity'">允许改动的活动<a-select v-model:value="selectedActivities" mode="multiple" aria-label="允许改动的活动" @change="update"><a-select-option v-for="item in activities" :key="item.activity_id" :value="item.activity_id">{{ item.name }}</a-select-option></a-select></label>
    <div v-if="mode === 'window'" class="window"><label>从此时间起<input v-model="start" type="datetime-local" aria-label="改排开始时间" @input="update" /></label><label>至此时间止<input v-model="end" type="datetime-local" aria-label="改排结束时间" @input="update" /></label><a-button size="small" @click="remaining">从现在开始</a-button></div>
    <p>范围外、已完成和锁定的安排会保留。扩大范围后将生成新的预览提案，确认前原行程不变。</p>
    <a-alert v-if="validationMessage" type="warning" :message="validationMessage" />
  </section>
</template>
<script setup lang="ts">
import { Alert as AAlert, Button as AButton, Select as ASelect, SelectOption as ASelectOption } from 'ant-design-vue'

import { computed, ref, watch } from 'vue'
import type { TripPlan, RevisionScope } from '@/types'
import { tripLocalTime, zonedTimestamp } from '@/utils/tripDay'
const props = defineProps<{ plan: TripPlan; dayIndex: number; initial?: RevisionScope; zone: string }>()
const emit = defineEmits<{ change: [value: RevisionScope] }>()
const mode = ref('day'), selectedDays = ref<number[]>([props.dayIndex]), selectedActivities = ref<string[]>([]), start = ref(''), end = ref('')
const activities = computed(() => { const day = props.plan.days.find(d => d.day_index === props.dayIndex); const items = [...(day?.attractions || []), ...(day?.meals || []), ...(day?.timeline || []).filter(a => ['attraction', 'meal'].includes(a.kind)).map(a => ({ activity_id: a.activity_id, name: a.title }))]; return [...new Map(items.filter(a => a.activity_id).map(a => [a.activity_id, a])).values()] })
const stamp = (value: string) => value ? zonedTimestamp(value, props.zone) : null
function update() { emit('change', { day_indices: mode.value === 'multiple' ? [...selectedDays.value] : [props.dayIndex], activity_ids: mode.value === 'activity' ? [...selectedActivities.value] : [], start_at: mode.value === 'window' ? stamp(start.value) : null, end_at: mode.value === 'window' ? stamp(end.value) : null }) }
function remaining() { start.value = tripLocalTime(Math.ceil(Date.now() / 60000) * 60000, props.zone); update() }
const validationMessage = computed(() => mode.value === 'multiple' && !selectedDays.value.length ? '至少选择一个允许调整的日期。' : mode.value === 'activity' && !selectedActivities.value.length ? '至少选择一个允许调整的活动。' : mode.value === 'window' && (!stamp(start.value) || !stamp(end.value) || start.value >= end.value) ? '请设置完整且有效的时间范围。' : '')
watch(() => [props.dayIndex, props.initial] as const, ([day, initial]) => {
  selectedDays.value = initial?.day_indices || [day]
  selectedActivities.value = initial?.activity_ids || []
  start.value = initial?.start_at ? tripLocalTime(Date.parse(initial.start_at), props.zone) : ''
  end.value = initial?.end_at ? tripLocalTime(Date.parse(initial.end_at), props.zone) : ''
  mode.value = start.value && end.value ? 'window' : selectedActivities.value.length ? 'activity' : selectedDays.value.length > 1 ? 'multiple' : 'day'
  update()
}, { immediate: true })
defineExpose({ validate: () => validationMessage.value })
</script>
<style scoped>
.revision-scope{margin:16px 0;}label{display:flex;flex-direction:column;gap:7px;margin-bottom:12px;font-size:13px;font-weight:500;}.window{display:flex;gap:12px;align-items:end;flex-wrap:wrap;}.window label{flex:1;min-width:170px;}input{border:1px solid #d6dce6;border-radius:6px;padding:7px;color:#26334a;}p{color:#667085;font-size:13px;line-height:1.6;}
</style>

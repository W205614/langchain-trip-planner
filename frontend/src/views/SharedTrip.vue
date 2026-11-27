<template>
  <main class="page">
    <a-card class="glass-card card" :bordered="false">
      <a-button @click="router.push('/')">← 首页</a-button>
      <a-spin :spinning="loading">
        <a-result v-if="error" status="404" title="分享不可用" :sub-title="error" />
        <template v-else-if="trip">
          <h1>{{ trip.title || trip.city + '行程' }}</h1>
          <p>{{ trip.start_date }} 至 {{ trip.end_date }} · {{ trip.outcome }}</p>
          <p v-if="suggestionsEnabled && !logged">
            <router-link :to="{ path: '/login', query: { redirect: route.path } }">登录后提交同行建议</router-link>
          </p>
          <a-card v-for="day in trip.plan?.days || []" :key="day.day_index"
            :title="'第 ' + (day.day_index + 1) + ' 天 · ' + day.date" class="day">
            <ActivityTimeline :day="day" :planning="trip.plan?.planning_input" />
            <a-timeline v-if="!day.timeline?.length">
              <a-timeline-item v-for="poi in day.attractions" :key="poi.poi_id">
                <strong>{{ poi.name }}</strong><br>{{ poi.address }}
              </a-timeline-item>
            </a-timeline>
            <a-button v-if="logged && suggestionsEnabled && activeDay !== day.day_index" @click="openForm(day.day_index)">
              建议修改这一天
            </a-button>
            <form v-if="activeDay === day.day_index" class="suggestion-form" @submit.prevent="submit">
              <label>建议类型
                <select v-model="kind">
                  <option value="shift_time" :disabled="!dayTargets.length">调整活动时间</option>
                  <option value="alternative">添加备选地点</option>
                </select>
              </label>
              <template v-if="kind === 'shift_time'">
                <label>活动
                  <select v-model.number="selectedItem" required>
                    <option v-for="item in dayTargets" :key="item.item_index" :value="item.item_index">
                      {{ item.old_start.slice(11,16) }} {{ item.title }}
                    </option>
                  </select>
                </label>
                <label>建议开始时间 <input v-model="startTime" type="time" required /></label>
              </template>
              <label v-else>备选地点 <a-input v-model:value="location" :maxlength="120" required /></label>
              <label>理由（可选） <a-textarea v-model:value="reason" :maxlength="500" :rows="2" /></label>
              <p>建议会发送给行程所有者，由对方确认后生效。</p>
              <a-alert v-if="suggestionError" type="error" :message="suggestionError" />
              <a-space>
                <a-button type="primary" html-type="submit" :loading="submitting">提交建议</a-button>
                <a-button :disabled="submitting" @click="activeDay = null">取消</a-button>
              </a-space>
            </form>
            <a-alert v-else-if="submittedDay === day.day_index" type="success" message="建议已提交，等待行程所有者处理" />
          </a-card>
          <a-button v-if="logged" type="primary" @click="copy">复制到我的行程</a-button>
        </template>
      </a-spin>
    </a-card>
  </main>
</template>

<script setup lang="ts">
import {
  Alert as AAlert, Button as AButton, Card as ACard, Input as AInput, Result as AResult,
  Space as ASpace, Spin as ASpin, Textarea as ATextarea, Timeline as ATimeline,
  TimelineItem as ATimelineItem, message
} from 'ant-design-vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ActivityTimeline from '@/components/trip/ActivityTimeline.vue'
import { copySharedTrip, fetchSharedTrip, submitTripSuggestion, type SuggestionTarget } from '@/services/api'
import { isAuthenticated } from '@/services/auth'
import type { TripPlan } from '@/types'

const route = useRoute()
const router = useRouter()
const token = String(route.params.token)
const logged = isAuthenticated()
const trip = ref<{ title: string; city: string; start_date: string; end_date: string; outcome: string; plan: TripPlan }>()
const loading = ref(true)
const error = ref('')
const suggestionsEnabled = ref(false)
const targets = ref<SuggestionTarget[]>([])
const activeDay = ref<number | null>(null)
const submittedDay = ref<number | null>(null)
const kind = ref<'shift_time' | 'alternative'>('alternative')
const selectedItem = ref(0)
const startTime = ref('')
const location = ref('')
const reason = ref('')
const submitting = ref(false)
const suggestionError = ref('')
const dayTargets = computed(() => targets.value.filter(item => item.day_index === activeDay.value))

onMounted(async () => {
  try {
    const data = await fetchSharedTrip(token)
    trip.value = data.data
    suggestionsEnabled.value = data.allow_suggestions === true
    targets.value = data.suggestion_targets || []
  } catch (failure: any) {
    error.value = failure.response?.data?.message || '链接已过期或被撤销'
  } finally {
    loading.value = false
  }
})

function openForm(dayIndex: number) {
  if (submitting.value) return
  activeDay.value = dayIndex
  kind.value = dayTargets.value.length ? 'shift_time' : 'alternative'
  selectedItem.value = dayTargets.value[0]?.item_index ?? 0
  startTime.value = ''
  location.value = ''
  reason.value = ''
  suggestionError.value = ''
}

async function submit() {
  if (activeDay.value === null || submitting.value) return
  const day = activeDay.value
  submitting.value = true
  suggestionError.value = ''
  try {
    const base = { day_index: day, reason: reason.value.trim() }
    await submitTripSuggestion(token, kind.value === 'shift_time'
      ? { ...base, kind: 'shift_time', item_index: selectedItem.value, start_time: startTime.value }
      : { ...base, kind: 'alternative', location: location.value.trim() })
    submittedDay.value = day
    activeDay.value = null
  } catch (failure: any) {
    suggestionError.value = failure.response?.data?.message || '提交失败，请稍后重试。'
  } finally {
    submitting.value = false
  }
}

async function copy() {
  const response = await copySharedTrip(token)
  message.success(response.message)
  router.push('/history')
}
</script>

<style scoped>
.page { padding: 36px; min-height: 100vh; }
.card { max-width: 900px; margin: auto; border-radius: 22px; }
.day { margin: 16px 0; }
.suggestion-form { display: grid; gap: 12px; max-width: 460px; }
.suggestion-form label { display: grid; gap: 4px; }
.suggestion-form select, .suggestion-form input[type="time"] {
  padding: 6px 10px;
  border: 1px solid #b6bdc7;
  border-radius: 6px;
  background: #fff;
  color: #222;
}
.suggestion-form p { margin: 0; }
@media (max-width: 700px) { .page { padding: 12px; } }
</style>

<template>
  <div class="result-container">
    <AttractionPicker v-if="tripPlan" :open="pickerDay !== null" :city="tripPlan.city"
      :used-ids="usedPoiIds" @close="pickerDay = null" @select="addAttraction" />
    <!-- 页面头部 -->
    <div class="page-header">
      <a-button class="back-button" size="large" @click="goBack">
        ← {{ returnLabel }}
      </a-button>
      <a-space size="middle">
        <a-button v-if="!editMode" @click="toggleEditMode" type="default">
          ✏️ 编辑行程
        </a-button>
        <a-button v-else @click="saveChanges" type="primary">
          💾 保存修改
        </a-button>
        <a-button v-if="editMode" @click="cancelEdit" type="default">
          ❌ 取消编辑
        </a-button>
        <a-button v-if="!editMode && historyRecordId" @click="shareTrip">🔗 分享</a-button>

        <!-- 导出按钮 -->
        <a-dropdown v-if="!editMode" :disabled="exportBusy">
          <template #overlay>
            <a-menu>
              <a-menu-item key="image" @click="exportAsImage">
                📷 导出为图片
              </a-menu-item>
              <a-menu-item key="pdf" @click="exportAsPDF">
                📄 导出为PDF
              </a-menu-item>
              <a-menu-item key="html" @click="exportAsHTML">
                🌐 导出离线网页（可展开/收起）
              </a-menu-item>
            </a-menu>
          </template>
          <a-button type="default" :loading="exportBusy" :disabled="exportBusy">
            📥 导出行程 <DownOutlined />
          </a-button>
        </a-dropdown>
      </a-space>
    </div>

    <div v-if="tripPlan" class="content-wrapper">
      <!-- 侧边导航 -->
      <div class="side-nav">
        <a-affix :offset-top="80">
          <a-menu mode="inline" :selected-keys="[activeSection]" @click="scrollToSection">
            <a-menu-item key="overview">
              <span>📋 行程概览</span>
            </a-menu-item>
            <a-menu-item key="budget" v-if="tripPlan.budget">
              <span>💰 预算明细</span>
            </a-menu-item>
            <a-menu-item key="map">
              <span>📍 景点地图</span>
            </a-menu-item>
            <a-sub-menu key="days" title="📅 每日行程">
              <a-menu-item v-for="(day, index) in tripPlan.days" :key="`day-${index}`">
                第{{ day.day_index + 1 }}天
              </a-menu-item>
            </a-sub-menu>
            <a-menu-item key="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0">
              <span>🌤️ 天气信息</span>
            </a-menu-item>
          </a-menu>
        </a-affix>
      </div>

      <!-- 主内容区 -->
      <div class="main-content">
        <div class="result-notices">
          <a-alert v-if="editMode || editNotice || unsaved" type="info" show-icon message="编辑期间预算为上次保存值；保存到服务器后重新计算预算和规则，路线信息仍需确认。" />
          <a-alert v-if="quality.outcome === 'draft'" type="warning" show-icon message="未完成草稿：以下要求尚未满足，不能视为完整可执行行程。" />
          <a-alert v-for="notice in tripPlan.enrichment_notices || []" :key="notice" type="info" :message="notice" />
          <a-alert v-if="quality.completion_policy === 'unassessed'" type="info" message="历史结果尚未按新的完成标准评估。" />
          <a-alert v-for="(issue, index) in (quality.issues || []).filter((i: any) => i.blocking)" :key="`issue-${index}`"
            type="error" show-icon :message="issue.reason" :description="issue.action" />
          <a-button v-if="quality.assistant_confirmation_required" type="primary" @click="confirmAssistant">确认保存此助手方案</a-button>
          <a-button v-else-if="quality.revision_parent?.record_id" @click="applyRevisionDraft">确认将此草稿应用到原行程（原行程将更新）</a-button>
          <a-alert v-if="quality.policy_version" :type="quality.rules_passed ? 'info' : 'warning'" show-icon
            :message="quality.rules_passed ? '已通过当前规则检查，开放与预约等事实仍需核实' : '部分旅行要求尚未满足，请检查下方说明并调整行程'" />
          <a-alert v-for="check in quality.day_checks || []" :key="`check-${check.day_index}`" type="info"
            :message="dayCheckMessage(check)" />
          <a-alert v-if="quality.repairs?.length" type="info" :message="`已调整 ${quality.repairs.length} 处重复、不去或超限景点；请确认必去要求是否满足。`" />
          <a-alert v-if="unsaved" type="warning" show-icon message="当前修改尚未保存到服务器，请重新保存或从历史记录加载。" />
          <a-alert v-if="quality.degraded_days?.length" type="warning" show-icon
            :message="`第 ${quality.degraded_days.map((d: number) => d + 1).join('、')} 天使用规则兜底，请核对安排`" />
          <a-alert v-if="quality.data_gaps?.length" type="info" show-icon
            :message="factGapMessage" />
          <a-alert v-for="warning in (quality.warnings || []).filter((w: string) => !(quality.issues || []).some((i: any) => i.blocking && i.reason === w))" :key="warning" type="warning" :message="warning" />
        </div>
        <!-- 顶部信息区:左侧概览+预算,右侧地图 -->
        <div class="top-info-section">
          <!-- 左侧:行程概览和预算明细 -->
          <div class="left-info">
            <!-- 行程概览 -->
            <a-card id="overview" :title="`${tripPlan.city}旅行计划`" :bordered="false" class="overview-card">
              <template #extra>
                <span class="overview-days">{{ tripPlan.days.length }} 天行程</span>
              </template>
              <div class="overview-content">
                <div class="info-item">
                  <span class="info-label">📅 日期</span>
                  <span class="info-value">{{ tripPlan.start_date }} 至 {{ tripPlan.end_date }}</span>
                </div>
                <!-- 行程统计 -->
                <div class="overview-stats">
                  <div class="stat-box">
                    <div class="stat-num">{{ tripPlan.days.length }}</div>
                    <div class="stat-label">天行程</div>
                  </div>
                  <div class="stat-box">
                    <div class="stat-num">{{ totalAttractions }}</div>
                    <div class="stat-label">个景点</div>
                  </div>
                  <div class="stat-box">
                    <div class="stat-num">¥{{ formatMoney(tripPlan.budget?.total || 0) }}</div>
                    <div class="stat-label">预估总预算</div>
                  </div>
                </div>
                <div class="info-item">
                  <span class="info-label">💡 旅行建议</span>
                  <span class="info-value">{{ tripPlan.overall_suggestions }}</span>
                </div>
              </div>
            </a-card>

            <!-- 预算明细 -->
            <a-card id="budget" v-if="tripPlan.budget" title="💰 预算估算（含费用预留，非实时报价）" :bordered="false" class="budget-card">
              <div class="budget-grid">
                <div class="budget-item">
                  <div class="budget-label">景点门票</div>
                  <div class="budget-value">¥{{ formatMoney(tripPlan.budget.total_attractions) }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">酒店住宿</div>
                  <div class="budget-value">¥{{ formatMoney(tripPlan.budget.total_hotels) }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">餐饮费用</div>
                  <div class="budget-value">¥{{ formatMoney(tripPlan.budget.total_meals) }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">交通费用</div>
                  <div class="budget-value">¥{{ formatMoney(tripPlan.budget.total_transportation) }}</div>
                </div>
              </div>
              <p v-for="note in tripPlan.budget.assumptions || []" :key="note" class="budget-note">{{ note }}</p>
              <div class="budget-total">
                <span class="total-label">预估总费用</span>
                <span class="total-value">¥{{ formatMoney(tripPlan.budget.total) }}</span>
              </div>
            </a-card>
          </div>

          <!-- 右侧:地图 -->
          <div class="right-map">
            <a-card id="map" title="📍 景点地图" :bordered="false" class="map-card">
              <p>虚线为景点游览顺序示意，非实际导航路线。</p>
              <div id="amap-container" style="width: 100%; height: 100%"></div>
            </a-card>
          </div>
        </div>

        <!-- 每日行程:可折叠 -->
        <a-card title="📅 每日行程" :bordered="false" class="days-card">
          <a-collapse v-model:activeKey="activeDays" accordion>
            <a-collapse-panel
              v-for="(day, index) in tripPlan.days"
              :key="index"
              :id="`day-${index}`"
              :force-render="true"
            >
              <template #header>
                <div class="day-header">
                  <span class="day-title">第{{ day.day_index + 1 }}天</span>
                  <a-space>
                    <span class="day-date">{{ day.date }}</span>
                    <a-button
                      v-if="historyRecordId && !editMode"
                      size="small"
                      type="dashed"
                      @click.stop="openRevision(day.day_index)"
                    >✨ AI 重新安排</a-button>
                  </a-space>
                </div>
              </template>

              <!-- 行程基本信息 -->
              <div class="day-info">
                <div class="info-row">
                  <span class="label">📝 行程描述:</span>
                  <span class="value">{{ day.description }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🚗 交通方式:</span>
                  <span class="value">{{ day.transportation }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🏨 住宿:</span>
                  <span class="value">{{ day.accommodation }}</span>
                </div>
              </div>

              <a-card v-if="quality.day_checks?.find((c: any) => c.day_index === day.day_index)?.routes?.length" size="small" class="route-card" title="景点间交通（高德路线参考）">
                <p v-for="leg in quality.day_checks.find((c: any) => c.day_index === day.day_index).routes" :key="leg.from + leg.to">
                  {{ leg.from }} → {{ leg.to }}（{{ leg.route_type === 'walking' ? '步行' : leg.route_type === 'driving' ? '驾车' : '公共交通' }}）：{{ leg.minutes == null ? '暂未取得路线，请在地图中确认' : `${leg.minutes} 分钟 / ${formatDistance(leg.distance_km)}` }}
                  <span v-if="leg.walking_km != null">，其中步行 {{ formatDistance(leg.walking_km) }}</span>
                </p>
                <p class="route-total">{{ routeTotalMessage(day.day_index) }}</p>
              </a-card>
              <!-- 景点安排 -->
              <a-divider orientation="left">🎯 景点安排</a-divider>
              <a-button v-if="editMode" type="dashed" @click="pickerDay = index">＋ 添加景点</a-button>
              <a-list
                :data-source="day.attractions"
                :grid="{ gutter: 16, column: 2 }"
              >
                <template #renderItem="{ item, index }">
                  <a-list-item>
                    <a-card :title="item.name" size="small" class="attraction-card">
                      <!-- 编辑模式下的操作按钮 -->
                      <template #extra v-if="editMode">
                        <a-space>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'up')"
                            :disabled="index === 0"
                          >
                            ↑
                          </a-button>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'down')"
                            :disabled="index === day.attractions.length - 1"
                          >
                            ↓
                          </a-button>
                          <a-button
                            size="small"
                            danger
                            @click="deleteAttraction(day.day_index, index)"
                          >
                            🗑️
                          </a-button>
                        </a-space>
                      </template>

                      <!-- 景点图片 -->
                      <div class="attraction-image-wrapper">
                        <img
                          :src="getAttractionImage(item.name, index)"
                          :alt="item.name"
                          class="attraction-image"
                          loading="lazy"
                          @error="handleImageError"
                        />
                        <div class="attraction-badge">
                          <span class="badge-number">{{ index + 1 }}</span>
                        </div>
                        <div v-if="item.ticket_price" class="price-tag">
                          估算 ¥{{ item.ticket_price }}
                        </div>
                      </div>

                      <!-- 编辑模式下可编辑的字段 -->
                      <div v-if="editMode">
                        <p><strong>地址:</strong></p>
                        <a-input v-model:value="item.address" size="small" style="margin-bottom: 8px" />

                        <p><strong>游览时长(分钟):</strong></p>
                        <a-input-number v-model:value="item.visit_duration" :min="10" :max="480" size="small" style="width: 100%; margin-bottom: 8px" />

                        <p><strong>描述:</strong></p>
                        <a-textarea v-model:value="item.description" :rows="2" size="small" style="margin-bottom: 8px" />
                      </div>

                      <!-- 查看模式 -->
                      <div v-else>
                        <p v-if="item.requested_names?.length"><strong>必去要求:</strong> {{ item.requested_names.join('、') }}（已匹配此景点）</p>
                        <p><strong>地址:</strong> {{ item.address }}</p>
                        <p><strong>游览时长:</strong> {{ item.visit_duration }}分钟</p>
                        <p><strong>开放时间:</strong> {{ item.opening_hours ? item.opening_hours + "（高德参考，出行日请确认）" : "暂无可靠数据，请查景区公告" }}</p>
                        <p><strong>预约:</strong> 请通过景区官方渠道确认是否需要预约及剩余名额</p>
                        <p><strong>描述:</strong> <span class="attraction-desc">{{ item.description }}</span></p>
                        <p v-if="item.rating"><strong>评分:</strong> {{ item.rating }}⭐</p>
                      </div>
                    </a-card>
                  </a-list-item>
                </template>
              </a-list>

              <!-- 酒店推荐 -->
              <a-divider v-if="day.hotel" orientation="left">🏨 住宿推荐</a-divider>
              <a-card v-if="day.hotel" size="small" class="hotel-card">
                <template #title>
                  <span class="hotel-title">{{ day.hotel.name }}</span>
                </template>
                <a-descriptions :column="2" size="small">
                  <a-descriptions-item label="地址">{{ day.hotel.address }}</a-descriptions-item>
                  <a-descriptions-item label="类型">{{ day.hotel.type }}</a-descriptions-item>
                  <a-descriptions-item label="价格范围">{{ day.hotel.price_range }}</a-descriptions-item>
                  <a-descriptions-item label="评分">{{ day.hotel.rating }}⭐</a-descriptions-item>
                  <a-descriptions-item label="距离" :span="2">{{ day.hotel.distance }}</a-descriptions-item>
                </a-descriptions>
              </a-card>

              <!-- 餐饮安排 -->
              <a-divider orientation="left">🍽️ 餐饮安排</a-divider>
              <a-descriptions :column="1" bordered size="small">
                <a-descriptions-item
                  v-for="meal in day.meals"
                  :key="meal.type"
                  :label="getMealLabel(meal.type)"
                >
                  {{ meal.name }}
                  <span v-if="meal.description"> - {{ meal.description }}</span>
                </a-descriptions-item>
              </a-descriptions>
            </a-collapse-panel>
          </a-collapse>
        </a-card>

        <a-alert
          v-if="tripPlan.weather_notice"
          class="weather-notice"
          type="warning"
          show-icon
          :message="tripPlan.weather_notice"
        />
        <a-card id="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0" title="🌤️ 天气信息" class="weather-section" :bordered="false">
        <a-list
          :data-source="tripPlan.weather_info"
          :grid="{ gutter: 16, column: 3 }"
        >
          <template #renderItem="{ item }">
            <a-list-item>
              <a-card size="small" class="weather-card">
                <div class="weather-date">{{ item.date }}</div>
                <div class="weather-info-row">
                  <span class="weather-icon">☀️</span>
                  <div>
                    <div class="weather-label">白天</div>
                    <div class="weather-value">{{ item.day_weather }} {{ item.day_temp }}°C</div>
                  </div>
                </div>
                <div class="weather-info-row">
                  <span class="weather-icon">🌙</span>
                  <div>
                    <div class="weather-label">夜间</div>
                    <div class="weather-value">{{ item.night_weather }} {{ item.night_temp }}°C</div>
                  </div>
                </div>
                <div class="weather-wind">
                  💨 {{ item.wind_direction }} {{ item.wind_power }}
                </div>
              </a-card>
            </a-list-item>
          </template>
        </a-list>
        </a-card>
      </div>
    </div>

    <a-empty v-else description="没有找到旅行计划数据">
      <template #image>
        <div style="font-size: 80px;">🗺️</div>
      </template>
      <template #description>
        <span style="color: #999;">暂无旅行计划数据,请先创建行程</span>
      </template>
      <a-button type="primary" @click="goBack">返回首页创建行程</a-button>
    </a-empty>

    <a-modal
      v-model:open="revisionOpen"
      title="重新安排当天行程"
      :confirm-loading="revisionLoading"
      ok-text="仅重新安排这一天"
      @ok="submitRevision"
    >
      <a-alert
        type="info"
        show-icon
        message="只会调用一次规划模型并修改当前日期；其余日期保持不变。"
        style="margin-bottom: 16px"
      />
      <a-textarea
        v-model:value="revisionInstruction"
        :rows="4"
        :maxlength="500"
        show-count
        placeholder="例如：下雨，改为室内博物馆并减少步行"
      />
    </a-modal>

    <!-- 回到顶部按钮 -->
    <a-back-top :visibility-height="300">
      <div class="back-top-button">
        ↑
      </div>
    </a-back-top>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { DownOutlined } from '@ant-design/icons-vue'
import { useTripMap } from '@/composables/useTripMap'
import AttractionPicker from '@/components/trip/AttractionPicker.vue'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'
import type { TripPlan, POIInfo } from '@/types'
import { reviseHistoryDay, updateHistory, fetchHistoryDetail, createTripShare, confirmAssistantProposal } from '@/services/api'

const router = useRouter()
const route = useRoute()
const returnLabel = computed(() => route.query.from === 'history' ? '返回历史行程' : '返回首页')
const tripPlan = ref<TripPlan | null>(null)
const recordVersion = ref(Number(sessionStorage.getItem('tripPlanVersion') || 1))
const unsaved = ref(sessionStorage.getItem('tripUnsaved') === 'true')
async function confirmAssistant() {
  if (!historyRecordId.value || !quality.value.assistant_conversation_id) return
  try {
    const response = await confirmAssistantProposal(quality.value.assistant_conversation_id, historyRecordId.value, recordVersion.value)
    historyRecordId.value = response.id; recordVersion.value = response.version; quality.value = response.quality
    sessionStorage.setItem('tripPlanId', String(response.id)); sessionStorage.setItem('tripPlanVersion', String(response.version))
    sessionStorage.setItem('tripQuality', JSON.stringify(response.quality)); message.success(response.message)
  } catch (e: any) { message.error(e.response?.data?.message || '确认失败，请重新打开方案') }
}
async function applyRevisionDraft() {
  try {
    const { default: api } = await import('@/services/api')
    await api.post(`/api/history/${historyRecordId.value}/apply-draft`, {}, { headers: { 'If-Match': String(recordVersion.value) } })
    message.success('已应用，未满足要求仍保留为草稿提示')
    delete quality.value.revision_parent
  } catch (e: any) { message.error(e.response?.data?.message || e.response?.data?.detail || '应用失败，请重新打开原行程确认版本') }
}
const quality = ref(JSON.parse(sessionStorage.getItem('tripQuality') || '{}'))
const factGapMessage = computed(() => {
  const gaps: string[] = quality.value.data_gaps || []
  const notes = ['预约要求及余票请通过景区官方渠道确认']
  notes.push(gaps.includes('opening_hours_unavailable') ? '部分景点暂无开放时间数据' : '开放时间为查询参考，出行日请再次确认')
  if (gaps.includes('route_duration_unavailable_fallback_to_straight_line')) notes.push('部分景点间路线查询失败，详见每日交通')
  notes.push('酒店、餐厅接驳及景区内部步行未计入路线核实')
  return notes.join('；') + '。'
})
const acceptVersion = (response: any) => {
  recordVersion.value = response.version
  quality.value = response.quality || {}
  sessionStorage.setItem('tripPlanVersion', String(response.version))
  sessionStorage.setItem('tripQuality', JSON.stringify(quality.value))
}
const editMode = ref(false)
const pickerDay = ref<number | null>(null)
const usedPoiIds = computed(() => tripPlan.value?.days.flatMap(day => day.attractions.map(a => a.poi_id || '')) || [])
const originalQuality = ref<any>(null)
const editNotice = ref(false)
const addAttraction = (poi: POIInfo) => {
  if (!editMode.value || pickerDay.value === null || !tripPlan.value || usedPoiIds.value.includes(poi.id)) return
  tripPlan.value.days[pickerDay.value].attractions.push({
    poi_id: poi.id, name: poi.name, address: poi.address, location: { ...poi.location },
    category: poi.type, opening_hours: poi.opening_hours, fact_source: 'amap', price_source: 'unknown',
    visit_duration: 120, description: '手动添加的景点，请确认游览时间及预约要求。'
  })
  invalidateChecks()
  pickerDay.value = null
  loadAttractionPhotos()
}
const invalidateChecks = () => {
  editNotice.value = true
  quality.value = { data_gaps: ['user_edited_plan_not_externally_verified'] }
}
watch(tripPlan, () => { if (editMode.value) invalidateChecks() }, { deep: true, flush: 'sync' })
const exportBusy = ref(false)
const originalPlan = ref<TripPlan | null>(null)
const attractionPhotos = ref<Record<string, string>>({})
const activeSection = ref('overview')
const activeDays = ref<number[]>([0]) // 默认展开第一天
const historyRecordId = ref<number>(0) // 从历史打开时的记录 id (0=新规划)
async function shareTrip() {
  try {
    const response = await createTripShare(historyRecordId.value, 7)
    const url = `${window.location.origin}/shared/${response.token}`
    await navigator.clipboard.writeText(url)
    message.success('7天有效的只读分享链接已复制')
  } catch (error: any) {
    message.error(error.response?.data?.message || '创建分享失败')
  }
}
const revisionOpen = ref(false)
const revisionLoading = ref(false)
const revisionDayIndex = ref<number | null>(null)
const revisionInstruction = ref('')
const { initMap } = useTripMap(tripPlan)

// 统计所有景点数量
const totalAttractions = computed(() => {
  if (!tripPlan.value) return 0
  return tripPlan.value.days.reduce((sum, day) => sum + day.attractions.length, 0)
})

// 金额千分位格式化
const formatMoney = (value: number): string => {
  return (value || 0).toLocaleString('zh-CN')
}

const formatDistance = (km: number): string => {
  if (!Number.isFinite(km)) return '暂未核实'
  if (km < 0.1) return `${Math.round(km * 1000)} 米`
  return `${km < 1 ? km.toFixed(2) : km.toFixed(1)} 公里`
}

const dayCheckMessage = (check: any): string => {
  const schedule = `第${check.day_index + 1}天：安排 ${check.planned_minutes == null ? '待核实' : check.planned_minutes + ' 分钟'}（含用餐与缓冲预留）`
  if (check.walking_status === 'no_attractions') return `${schedule}；当天没有可验证景点，无法统计景点间距离`
  if (check.walking_status === 'single_stop') return `${schedule}；当天只有一个景点，无景点间步行`
  if (check.walking_status === 'not_applicable') return `${schedule}；自驾路线不含停车后步行`
  return `${schedule}；景点间步行 ${check.inter_stop_walking_km == null ? '暂未核实' : formatDistance(check.inter_stop_walking_km)}`
}

const routeTotalMessage = (dayIndex: number): string => {
  const check = (quality.value.day_checks || []).find((item: any) => item.day_index === dayIndex)
  if (!check || check.route_distance_km == null) return '合计：部分路线暂未取得，不能给出虚假的 0 公里。'
  const walking = check.inter_stop_walking_km == null ? '步行距离暂未核实' : `步行 ${formatDistance(check.inter_stop_walking_km)}`
  return `当日景点间路线合计 ${formatDistance(check.route_distance_km)}，${walking}。`
}

onMounted(async () => {
  const data = sessionStorage.getItem('tripPlan')
  if (data) {
    tripPlan.value = JSON.parse(data)
    // 历史打开时记录 id (供编辑保存写回数据库); 新规划则为 0
    historyRecordId.value = Number(sessionStorage.getItem('tripPlanId') || '0')
    if (historyRecordId.value && !unsaved.value && quality.value.policy_version !== 'constraints-v2' && sessionStorage.getItem('access_token')) {
      try {
        const response = await fetchHistoryDetail(historyRecordId.value)
        if (response.success && response.data) {
          tripPlan.value = response.data.plan
          acceptVersion(response.data)
          sessionStorage.setItem('tripPlan', JSON.stringify(tripPlan.value))
        }
      } catch {
        message.warning('暂时无法刷新行程检查，当前显示上次保存结果')
      }
    }
    // 加载景点图片
    await loadAttractionPhotos()
    // 等待DOM渲染完成后初始化地图
    await nextTick()
    initMap()
  }
})

const goBack = () => {
  router.push(route.query.from === 'history' ? '/history' : '/')
}

// 滚动到指定区域
const scrollToSection = ({ key }: { key: string }) => {
  activeSection.value = key
  // 每日行程: 先展开对应面板再滚动定位
  if (key.startsWith('day-')) {
    const dayIndex = Number(key.replace('day-', ''))
    activeDays.value = [dayIndex]
    nextTick(() => {
      document.getElementById(key)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
    return
  }
  document.getElementById(key)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// 切换编辑模式
const toggleEditMode = () => {
  editMode.value = true
  originalQuality.value = JSON.parse(JSON.stringify(quality.value))
  // 保存原始数据用于取消编辑
  originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value))
  message.info('进入编辑模式')
}

// 保存修改
const saveChanges = async () => {
  invalidateChecks()
  pickerDay.value = null
  sessionStorage.setItem('tripQuality', JSON.stringify(quality.value))
  editMode.value = false
  unsaved.value = true
  sessionStorage.setItem('tripUnsaved', 'true')
  // 更新sessionStorage (始终保留当前渲染数据)
  if (tripPlan.value) {
    sessionStorage.setItem('tripPlan', JSON.stringify(tripPlan.value))
  }
  // 从历史打开时: 把编辑结果持久化回数据库, 下次打开历史仍是编辑后的内容
  if (historyRecordId.value && tripPlan.value) {
    try {
      const response = await updateHistory(historyRecordId.value, tripPlan.value, recordVersion.value)
      acceptVersion(response)
      editNotice.value = false
      tripPlan.value = response.data
      sessionStorage.setItem('tripPlan', JSON.stringify(response.data))
      unsaved.value = false
      sessionStorage.removeItem('tripUnsaved')
      message.success('修改已保存到历史记录')
    } catch (error: any) {
      message.error(error.response?.status === 409 ? '其它页面已修改行程，请从历史记录重新打开；本次修改未保存' : '保存失败，本次修改仅保留在本地')
    }
  } else {
    message.info('修改仅保留在当前浏览器')
  }

  // 重新初始化地图以反映更改
  nextTick(() => {
    initMap()
  })
}

// 取消编辑
const cancelEdit = () => {
  if (originalPlan.value) {
    tripPlan.value = JSON.parse(JSON.stringify(originalPlan.value))
  }
  editMode.value = false
  pickerDay.value = null
  quality.value = originalQuality.value || {}
  editNotice.value = false
  message.info('已取消编辑')
}

const openRevision = (dayIndex: number) => {
  revisionDayIndex.value = dayIndex
  revisionInstruction.value = ''
  revisionOpen.value = true
}

const submitRevision = async () => {
  if (!historyRecordId.value || revisionDayIndex.value === null || !tripPlan.value) return
  const instruction = revisionInstruction.value.trim()
  if (instruction.length < 2) {
    message.warning('请填写具体的改排要求')
    return
  }
  revisionLoading.value = true
  try {
    const response = await reviseHistoryDay(historyRecordId.value, revisionDayIndex.value, instruction, recordVersion.value)
    if (response.id) { historyRecordId.value = response.id; sessionStorage.setItem("tripPlanId", String(response.id)) }
    if (!response.success || !response.data) throw new Error(response.message || '改排失败')
    acceptVersion(response)
    tripPlan.value = response.data
    sessionStorage.setItem('tripPlan', JSON.stringify(response.data))
    revisionOpen.value = false
    message.success(response.message || '当天行程已重新安排')
    await nextTick()
    await loadAttractionPhotos()
    initMap()
  } catch (error: any) {
    message.error(error.response?.data?.detail || error.message || '改排失败')
  } finally {
    revisionLoading.value = false
  }
}

// 删除景点
const deleteAttraction = (dayIndex: number, attrIndex: number) => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  if (day.attractions.length <= 1) {
    message.warning('每天至少需要保留一个景点')
    return
  }

  day.attractions.splice(attrIndex, 1)
  invalidateChecks()
  message.success('景点已删除')
}

// 移动景点顺序
const moveAttraction = (dayIndex: number, attrIndex: number, direction: 'up' | 'down') => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  const attractions = day.attractions
  invalidateChecks()

  if (direction === 'up' && attrIndex > 0) {
    [attractions[attrIndex], attractions[attrIndex - 1]] = [attractions[attrIndex - 1], attractions[attrIndex]]
  } else if (direction === 'down' && attrIndex < attractions.length - 1) {
    [attractions[attrIndex], attractions[attrIndex + 1]] = [attractions[attrIndex + 1], attractions[attrIndex]]
  }
}

const getMealLabel = (type: string): string => {
  const labels: Record<string, string> = {
    breakfast: '早餐',
    lunch: '午餐',
    dinner: '晚餐',
    snack: '小吃'
  }
  return labels[type] || type
}

const svgToDataUrl = (svg: string): string =>
  `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`

// 页面和导出都直接使用同源受控代理，避免先解析一次、渲染时再解析一次。
// 代理会在无图或上游失败时返回 SVG 占位图，因此无需额外的预检请求。
const loadAttractionPhotos = async () => {
  if (!tripPlan.value) return

  tripPlan.value.days.forEach(day => {
    day.attractions.forEach(attraction => {
      // Invalidate placeholders cached by the first Java proxy; real photos still keep their normal TTL.
      attractionPhotos.value[attraction.name] = `/api/poi/photo/image?name=${encodeURIComponent(attraction.name)}&poi_id=${encodeURIComponent(attraction.poi_id || "")}&city=${encodeURIComponent(tripPlan.value!.city)}&v=java-photo-v3`
    })
  })
}

// 获取景点图片
const getAttractionImage = (name: string, index: number): string => {
  // 如果已加载真实图片,返回真实图片
  if (attractionPhotos.value[name]) {
    return attractionPhotos.value[name]
  }

  // 返回一个纯色占位图(避免跨域问题)
  const colors = [
    { start: '#667eea', end: '#764ba2' },
    { start: '#f093fb', end: '#f5576c' },
    { start: '#4facfe', end: '#00f2fe' },
    { start: '#43e97b', end: '#38f9d7' },
    { start: '#fa709a', end: '#fee140' }
  ]
  const colorIndex = index % colors.length
  const { start, end } = colors[colorIndex]

  // 使用base64编码避免中文问题
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">
    <defs>
      <linearGradient id="grad${index}" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:${start};stop-opacity:1" />
        <stop offset="100%" style="stop-color:${end};stop-opacity:1" />
      </linearGradient>
    </defs>
    <rect width="400" height="300" fill="url(#grad${index})"/>
    <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="24" font-weight="bold" fill="white">${name}</text>
  </svg>`

  return svgToDataUrl(svg)
}

// 图片加载失败时的处理
const handleImageError = (event: Event) => {
  const img = event.target as HTMLImageElement
  // 使用灰色占位图
  img.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="400" height="300"%3E%3Crect width="400" height="300" fill="%23f0f0f0"/%3E%3Ctext x="50%25" y="50%25" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="18" fill="%23999"%3E图片加载失败%3C/text%3E%3C/svg%3E'
}

const escapeSvgText = (value: string): string => value
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&apos;')

const buildExportMapImage = (): string => {
  const width = 920
  const height = 460
  const margin = 58
  const points = (tripPlan.value?.days || []).flatMap((day, dayIndex) =>
    day.attractions
      .filter(attraction => Number.isFinite(attraction.location?.longitude) && Number.isFinite(attraction.location?.latitude))
      .map((attraction, attractionIndex) => ({ attraction, dayIndex, attractionIndex }))
  )

  if (!points.length) {
    return svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#f5f7fa"/><text x="50%" y="50%" text-anchor="middle" font-family="sans-serif" font-size="24" fill="#64748b">暂无可导出的景点坐标</text></svg>`)
  }

  const longitudes = points.map(point => point.attraction.location.longitude)
  const latitudes = points.map(point => point.attraction.location.latitude)
  const minLongitude = Math.min(...longitudes)
  const maxLongitude = Math.max(...longitudes)
  const minLatitude = Math.min(...latitudes)
  const maxLatitude = Math.max(...latitudes)
  const longitudeRange = maxLongitude - minLongitude || 0.01
  const latitudeRange = maxLatitude - minLatitude || 0.01
  const project = (point: typeof points[number]) => ({
    x: margin + ((point.attraction.location.longitude - minLongitude) / longitudeRange) * (width - margin * 2),
    y: height - margin - ((point.attraction.location.latitude - minLatitude) / latitudeRange) * (height - margin * 2)
  })
  const projected = points.map(point => ({ ...point, ...project(point) }))
  const colors = ['#2563eb', '#7c3aed', '#db2777', '#0891b2', '#16a34a']
  const routeLines = Array.from({ length: tripPlan.value?.days.length || 0 }, (_, dayIndex) => {
    const dayPoints = projected.filter(point => point.dayIndex === dayIndex)
    if (dayPoints.length < 2) return ''
    return `<polyline points="${dayPoints.map(point => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ')}" fill="none" stroke="${colors[dayIndex % colors.length]}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" opacity="0.7"/>`
  }).join('')
  const markers = projected.map((point, index) => {
    const label = escapeSvgText(point.attraction.name.length > 12 ? `${point.attraction.name.slice(0, 12)}…` : point.attraction.name)
    const color = colors[point.dayIndex % colors.length]
    return `<g><circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="16" fill="${color}" stroke="#fff" stroke-width="4"/><text x="${point.x.toFixed(1)}" y="${(point.y + 5).toFixed(1)}" text-anchor="middle" font-family="sans-serif" font-size="14" font-weight="700" fill="#fff">${index + 1}</text><text x="${point.x.toFixed(1)}" y="${(point.y - 24).toFixed(1)}" text-anchor="middle" font-family="sans-serif" font-size="15" font-weight="600" fill="#1e293b">${label}</text></g>`
  }).join('')
  const legend = (tripPlan.value?.days || []).map((_, index) => `<tspan fill="${colors[index % colors.length]}">●</tspan><tspan> 第${index + 1}天 </tspan>`).join('')

  return svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><defs><pattern id="grid" width="46" height="46" patternUnits="userSpaceOnUse"><path d="M 46 0 L 0 0 0 46" fill="none" stroke="#dbeafe" stroke-width="1"/></pattern></defs><rect width="100%" height="100%" rx="14" fill="#eff6ff"/><rect x="18" y="18" width="${width - 36}" height="${height - 36}" rx="10" fill="url(#grid)" stroke="#bfdbfe"/><text x="${margin}" y="36" font-family="sans-serif" font-size="16" fill="#475569">行程点位示意图（按 POI 坐标绘制）</text><text x="${width - margin}" y="36" text-anchor="end" font-family="sans-serif" font-size="14" fill="#475569">${legend}</text>${routeLines}${markers}</svg>`)
}

const waitForExportImages = async (container: HTMLElement): Promise<void> => {
  const fallback = svgToDataUrl('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><rect width="100%" height="100%" fill="#e2e8f0"/><text x="50%" y="50%" text-anchor="middle" font-family="sans-serif" font-size="24" fill="#475569">图片暂不可用，请以文字行程为准</text></svg>')
  await Promise.all(Array.from(container.querySelectorAll('img')).map(image => new Promise<void>(resolve => {
    let done = false
    const finish = (ok: boolean) => {
      if (done) return
      done = true
      clearTimeout(timer)
      image.removeEventListener('load', loaded)
      image.removeEventListener('error', failed)
      if (!ok) {
        image.removeAttribute('srcset')
        image.src = fallback
      }
      resolve()
    }
    const loaded = () => finish(image.naturalWidth > 0)
    const failed = () => finish(false)
    const timer = window.setTimeout(failed, 8000)
    image.addEventListener('load', loaded)
    image.addEventListener('error', failed)
    image.loading = 'eager'
    if (image.complete) finish(image.naturalWidth > 0)
  })))
}

const prepareExportContainer = (element: HTMLElement): HTMLElement => {
  const exportContainer = document.createElement('div')
  exportContainer.style.width = `${Math.max(1000, element.offsetWidth)}px`
  exportContainer.dataset.tripExport = "true"
  exportContainer.style.backgroundColor = '#f5f7fa'
  exportContainer.style.padding = '20px'
  exportContainer.innerHTML = element.innerHTML

  // 高德底图的瓦片/canvas 受跨域保护，不能可靠地导出；使用同一 POI 坐标生成静态示意图。
  const exportMapContainer = exportContainer.querySelector('#amap-container')
  if (exportMapContainer) {
    exportMapContainer.innerHTML = `<img src="${buildExportMapImage()}" alt="行程点位示意图" style="width:100%;height:100%;object-fit:cover;display:block;" />`
  }

  exportContainer.querySelectorAll('.ant-card').forEach(card => {
    const cardEl = card as HTMLElement
    cardEl.className = ''
    cardEl.style.setProperty('background-color', '#ffffff')
    cardEl.style.setProperty('border-radius', '12px')
    cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
    cardEl.style.setProperty('margin-bottom', '20px')
    cardEl.style.setProperty('overflow', 'hidden')
  })
  exportContainer.querySelectorAll('.ant-card-head').forEach(head => {
    const headEl = head as HTMLElement
    headEl.style.setProperty('background-color', '#667eea')
    headEl.style.setProperty('color', '#ffffff')
    headEl.style.setProperty('padding', '16px 24px')
    headEl.style.setProperty('font-size', '18px')
    headEl.style.setProperty('font-weight', '600')
  })
  exportContainer.querySelectorAll('.ant-card-body').forEach(body => {
    (body as HTMLElement).style.setProperty('background-color', '#ffffff')
    ;(body as HTMLElement).style.setProperty('padding', '24px')
  })
  exportContainer.querySelectorAll('.hotel-card').forEach(card => {
    const head = card.querySelector('.ant-card-head') as HTMLElement
    if (head) head.style.setProperty('background-color', '#1976d2')
    ;(card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
  })
  exportContainer.querySelectorAll('.weather-card').forEach(card => {
    ;(card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
  })
  const budgetTotal = exportContainer.querySelector('.budget-total') as HTMLElement | null
  if (budgetTotal) {
    budgetTotal.style.setProperty('background-color', '#667eea')
    budgetTotal.style.setProperty('color', '#ffffff')
    budgetTotal.style.setProperty('padding', '20px')
    budgetTotal.style.setProperty('border-radius', '12px')
    budgetTotal.style.setProperty('margin-bottom', '20px')
  }
  exportContainer.querySelectorAll('.budget-item').forEach(item => {
    const itemEl = item as HTMLElement
    itemEl.style.setProperty('background-color', '#f5f7fa')
    itemEl.style.setProperty('padding', '16px')
    itemEl.style.setProperty('border-radius', '8px')
    itemEl.style.setProperty('margin-bottom', '12px')
  })
  exportContainer.querySelectorAll('.ant-collapse-content').forEach(content => {
    const panel = content as HTMLElement
    panel.classList.remove('ant-collapse-content-hidden')
    panel.style.display = 'block'
    panel.style.height = 'auto'
    panel.style.visibility = 'visible'
  })
  exportContainer.querySelectorAll('button, .ant-collapse-expand-icon').forEach(control => control.remove())
  exportContainer.querySelectorAll('img').forEach(image => {
    image.loading = 'eager'
    image.decoding = 'sync'
    image.setAttribute('crossorigin', 'anonymous')
    // cloneNode/innerHTML 不会复制 Vue 绑定的 @error 监听器；导出副本也要替换失败图片，
    // 否则单张上游图片异常仍会留下空白区域。
    image.addEventListener('error', handleImageError, { once: true })
  })
  exportContainer.style.position = 'absolute'
  exportContainer.style.left = '-100000px'
  exportContainer.style.top = '0'
  return exportContainer
}

const createExportCanvas = async (): Promise<HTMLCanvasElement> => {
  const element = document.querySelector('.main-content') as HTMLElement | null
  if (!element) throw new Error('未找到内容元素')
  const exportContainer = prepareExportContainer(element)
  document.body.appendChild(exportContainer)
  try {
    await waitForExportImages(exportContainer)
    const width = exportContainer.offsetWidth
    const height = exportContainer.scrollHeight
    // Bound both browser canvas dimensions and memory for multi-week itineraries.
    const scale = Math.min(2, 16000 / width, 16000 / height, Math.sqrt(24000000 / (width * height)))
    if (!Number.isFinite(scale) || scale <= 0) throw new Error('行程尺寸无效，请刷新后重试')
    return await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale,
      logging: false,
      useCORS: true,
      allowTaint: false,
      imageTimeout: 15000
    })
  } finally {
    document.body.removeChild(exportContainer)
  }
}

// Native details/summary provides portable offline interaction without executable scripts.
const exportAsHTML = async () => {
  if (exportBusy.value || !tripPlan.value) return
  exportBusy.value = true
  let container: HTMLElement | null = null
  try {
    message.loading({ content: '正在打包离线网页...', key: 'export', duration: 0 })
    const source = document.querySelector('.main-content') as HTMLElement
    container = prepareExportContainer(source)
    document.body.appendChild(container)
    const images = Array.from(container.querySelectorAll('img'))
    await Promise.all(images.map(async image => {
      if (image.src.startsWith('data:')) return
      try {
        const response = await fetch(image.src, { signal: AbortSignal.timeout(8000) })
        if (!response.ok) throw new Error('图片下载失败')
        const blob = await response.blob()
        image.src = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(String(reader.result))
          reader.onerror = reject
          reader.readAsDataURL(blob)
        })
      } catch {
        image.src = svgToDataUrl('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><rect width="100%" height="100%" fill="#e2e8f0"/><text x="50%" y="50%" text-anchor="middle" font-family="sans-serif" font-size="24">图片暂不可用，请以文字行程为准</text></svg>')
      }
      image.removeAttribute('srcset')
      image.removeAttribute('crossorigin')
    }))
    container.querySelectorAll('.ant-collapse-item').forEach((panel, index) => {
      const details = document.createElement('details')
      details.className = 'offline-day'
      details.open = index === 0
      const summary = document.createElement('summary')
      summary.textContent = panel.querySelector('.ant-collapse-header')?.textContent?.trim() || `第${index + 1}天`
      details.appendChild(summary)
      const content = panel.querySelector('.ant-collapse-content')
      if (content) details.appendChild(content)
      panel.replaceWith(details)
    })
    const css = Array.from(document.styleSheets).map(sheet => {
      try { return Array.from(sheet.cssRules).map(rule => rule.cssText).join('\n') } catch { return '' }
    }).join('\n')
    const offline = document.implementation.createHTMLDocument(`${tripPlan.value.city}旅行计划`)
    offline.documentElement.lang = 'zh-CN'
    const charset = offline.createElement('meta')
    charset.setAttribute('charset', 'utf-8')
    offline.head.prepend(charset)
    const policy = offline.createElement('meta')
    policy.httpEquiv = 'Content-Security-Policy'
    policy.content = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    offline.head.appendChild(policy)
    const styles = offline.createElement('style')
    styles.textContent = css + '\nbody{margin:0;background:#f5f7fa;font-family:Arial,sans-serif} .offline-day{margin:16px 0;border:1px solid #dbe2ed;border-radius:10px;overflow:hidden} .offline-day>summary{cursor:pointer;padding:18px;background:#667eea;color:white;font-size:18px;font-weight:bold} .offline-day .ant-collapse-content{display:block!important} .offline-note{padding:16px;background:#eef2ff;color:#334155}'
    offline.head.appendChild(styles)
    const note = offline.createElement('p')
    note.className = 'offline-note'
    note.textContent = '离线旅行计划：点击每天的标题可展开或收起。图片和正文已打包，无需登录；PNG/PDF 为完整静态版本。'
    offline.body.appendChild(note)
    const copy = container.cloneNode(true) as HTMLElement
    copy.removeAttribute('data-trip-export')
    copy.style.position = 'static'
    copy.style.left = ''
    copy.style.margin = '0 auto'
    copy.style.maxWidth = '100%'
    copy.style.boxSizing = 'border-box'
    offline.body.appendChild(copy)
    const blob = new Blob(['<!DOCTYPE html>\n', offline.documentElement.outerHTML], { type: 'text/html;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.download = `旅行计划_${tripPlan.value.city}_可交互.html`
    link.href = url
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 60000)
    message.success({ content: '离线网页已导出，用浏览器打开即可展开/收起每天行程', key: 'export' })
  } catch (error: any) {
    message.error({ content: `离线网页导出失败: ${error.message}`, key: 'export' })
  } finally {
    container?.remove()
    exportBusy.value = false
  }
}

// 导出为图片
const exportAsImage = async () => {
  if (exportBusy.value) return
  exportBusy.value = true
  try {
    message.loading({ content: '正在生成图片...', key: 'export', duration: 0 })
    const canvas = await createExportCanvas()
    const link = document.createElement('a')
    link.download = `旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.png`
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value =>
      value ? resolve(value) : reject(new Error('图片编码失败')), 'image/png'))
    const url = URL.createObjectURL(blob)
    link.href = url
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 60000)
    message.success({ content: '图片导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出图片失败:', error)
    message.error({ content: `导出图片失败: ${error.message}`, key: 'export' })
  } finally {
    exportBusy.value = false
  }
}

// 导出为PDF
const exportAsPDF = async () => {
  if (exportBusy.value) return
  exportBusy.value = true
  try {
    message.loading({ content: '正在生成PDF...', key: 'export', duration: 0 })
    const canvas = await createExportCanvas()
    const pdf = new jsPDF({
      orientation: 'portrait',
      unit: 'mm',
      format: 'a4'
    })

    const margin = 8
    const imgWidth = 210 - margin * 2
    const pixelsPerPage = Math.floor((297 - margin * 2) * canvas.width / imgWidth)
    const context = canvas.getContext('2d')!
    let top = 0
    let pageIndex = 0
    while (top < canvas.height) {
      let bottom = Math.min(top + pixelsPerPage, canvas.height)
      // Prefer a nearby blank row so pagination does not cut text in half.
      if (bottom < canvas.height) {
        const scanHeight = Math.min(120, bottom - top)
        const scanTop = bottom - scanHeight
        const pixels = context.getImageData(0, scanTop, canvas.width, scanHeight).data
        for (let row = scanHeight - 1; row >= 0; row--) {
          let ink = 0
          for (let x = 0; x < canvas.width; x += 4) {
            const offset = (row * canvas.width + x) * 4
            if (pixels[offset + 3] > 0 && Math.min(pixels[offset], pixels[offset + 1], pixels[offset + 2]) < 230) ink++
          }
          if (ink < canvas.width / 800) { bottom = scanTop + row; break }
        }
      }
      const pageCanvas = document.createElement('canvas')
      pageCanvas.width = canvas.width
      pageCanvas.height = bottom - top
      pageCanvas.getContext('2d')!.drawImage(canvas, 0, top, canvas.width, bottom - top, 0, 0, canvas.width, bottom - top)
      if (pageIndex++) pdf.addPage()
      pdf.addImage(pageCanvas.toDataURL('image/jpeg', 0.92), 'JPEG', margin, margin, imgWidth, pageCanvas.height * imgWidth / canvas.width)
      pageCanvas.width = pageCanvas.height = 0
      top = bottom
    }

    pdf.save(`旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.pdf`)

    message.success({ content: 'PDF导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出PDF失败:', error)
    message.error({ content: `导出PDF失败: ${error.message}`, key: 'export' })
  } finally {
    exportBusy.value = false
  }
}

</script>

<style scoped>
.result-container {
  min-height: 100vh;
  background: transparent;
  padding: 40px 20px;
}

.page-header {
  max-width: 1200px;
  margin: 0 auto 30px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  animation: fadeInDown 0.6s ease-out;
}

.back-button {
  border-radius: 8px;
  font-weight: 500;
}

/* 内容布局 */
.content-wrapper {
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  gap: 24px;
}

.side-nav {
  width: 240px;
  flex-shrink: 0;
}

.side-nav :deep(.ant-menu) {
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.93);
  backdrop-filter: blur(16px) saturate(140%);
  border: 1px solid rgba(255, 255, 255, 0.5);
  box-shadow: 0 12px 36px rgba(2, 6, 23, 0.35);
}

.side-nav :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.side-nav :deep(.ant-menu-item-selected) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.side-nav :deep(.ant-menu-item:hover) {
  background: rgba(102, 126, 234, 0.1);
}

.main-content {
  flex: 1;
  min-width: 0;
}

.result-notices {
  display: grid;
  gap: 8px;
  margin-bottom: 16px;
}

.result-notices :deep(.ant-alert) {
  color: #1f2937;
}

.route-card p {
  color: #344054;
  line-height: 1.7;
}

.route-total {
  margin: 12px 0 0;
  padding-top: 12px;
  border-top: 1px dashed #d0d5dd;
  font-weight: 700;
  color: #4338ca !important;
}

/* 景点图片样式 */
.attraction-image-wrapper {
  position: relative;
  margin-bottom: 12px;
  border-radius: 8px;
  overflow: hidden;
}

/* 景点描述: 保留换行, 展示知识库多行详情 (门票/开放时间/交通/避坑) */
.attraction-desc {
  white-space: pre-line;
  color: #666;
  font-size: 13px;
  line-height: 1.6;
}

.attraction-image {
  width: 100%;
  height: auto;
  /* Match the reference SVG's 800x500 canvas: fill the card without cropping its caption. */
  aspect-ratio: 8 / 5;
  object-fit: cover;
  display: block;
  background: #f1f5f9;
  transition: transform 0.3s ease;
}

.attraction-image-wrapper:hover .attraction-image {
  transform: none;
}

.attraction-badge {
  position: absolute;
  top: 12px;
  left: 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

.badge-number {
  font-size: 18px;
}

.price-tag {
  position: absolute;
  top: 12px;
  right: 12px;
  background: rgba(255, 77, 79, 0.9);
  color: white;
  padding: 4px 12px;
  border-radius: 12px;
  font-weight: bold;
  font-size: 14px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

/* 天气卡片样式 */
.weather-card {
  background: linear-gradient(135deg, #e0f7fa 0%, #b2ebf2 100%);
  border: none !important;
  transition: all 0.3s ease;
}

.weather-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.15);
}

.weather-date {
  font-size: 16px;
  font-weight: bold;
  color: #00796b;
  margin-bottom: 12px;
  text-align: center;
}

.weather-info-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.weather-icon {
  font-size: 24px;
}

.weather-label {
  font-size: 12px;
  color: #666;
}

.weather-value {
  font-size: 16px;
  font-weight: 600;
  color: #00796b;
}

.weather-wind {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(0, 121, 107, 0.2);
  text-align: center;
  color: #00796b;
  font-size: 14px;
}

/* 回到顶部按钮 */
.back-top-button {
  width: 50px;
  height: 50px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: bold;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  cursor: pointer;
  transition: all 0.3s ease;
}

.back-top-button:hover {
  transform: scale(1.1);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
}

/* 酒店卡片样式 */
.hotel-card {
  background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
  border: none !important;
}

.hotel-card :deep(.ant-card-head) {
  background: linear-gradient(135deg, #1976d2 0%, #1565c0 100%);
}

.hotel-title {
  color: white !important;
  font-weight: 600;
}

/* 顶部信息区布局 */
.top-info-section {
  display: flex;
  gap: 20px;
  margin-bottom: 20px;
}

.left-info {
  flex: 0 0 400px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.right-map {
  flex: 1;
}

/* 行程概览卡片 */
.overview-card {
  height: fit-content;
}

.overview-days {
  color: #667eea;
  font-weight: 600;
  font-size: 13px;
  background: rgba(102, 126, 234, 0.1);
  padding: 4px 12px;
  border-radius: 12px;
}

.overview-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 概览统计 */
.overview-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin: 4px 0;
}

.stat-box {
  text-align: center;
  padding: 12px 4px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 10px;
  border: 1px solid #e8e8e8;
}

.stat-num {
  font-size: 20px;
  font-weight: 700;
  color: #667eea;
}

.stat-label {
  font-size: 12px;
  color: #999;
  margin-top: 2px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-label {
  font-size: 14px;
  font-weight: 600;
  color: #666;
}

.info-value {
  font-size: 15px;
  color: #333;
  line-height: 1.6;
}

/* 预算卡片 */
.budget-card {
  height: fit-content;
}

.budget-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}

.budget-item {
  text-align: center;
  padding: 12px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.budget-label {
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}

.budget-value {
  font-size: 20px;
  font-weight: 700;
  color: #1890ff;
}

.budget-total {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 8px;
  color: white;
}

.total-label {
  font-size: 16px;
  font-weight: 600;
}

.total-value {
  font-size: 28px;
  font-weight: 700;
}

/* 地图卡片 */
.map-card {
  height: 100%;
  min-height: 500px;
}

.map-card :deep(.ant-card-body) {
  height: calc(100% - 57px);
  padding: 0;
}

/* 每日行程卡片 */
.days-card {
  margin-top: 20px;
}

/* 天气信息卡片 */
.weather-section {
  margin-top: 20px;
}

.weather-notice {
  margin-top: 20px;
}

.day-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.day-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

.day-date {
  font-size: 14px;
  color: #999;
}

.day-info {
  margin-bottom: 20px;
  padding: 16px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.info-row {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
}

.info-row:last-child {
  margin-bottom: 0;
}

.info-row .label {
  font-weight: 600;
  color: #666;
  min-width: 100px;
}

.info-row .value {
  color: #333;
  flex: 1;
}

/* 卡片样式优化 */
:deep(.ant-card) {
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  margin-bottom: 20px;
  transition: all 0.3s ease;
  animation: fadeInUp 0.6s ease-out;
}

:deep(.ant-card:hover) {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}

:deep(.ant-card-head) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white !important;
  border-radius: 12px 12px 0 0;
  font-weight: 600;
}

:deep(.ant-card-head-title) {
  color: white !important;
  font-size: 18px;
}

:deep(.ant-card-head-title span) {
  color: white !important;
}

/* Collapse样式 */
:deep(.ant-collapse) {
  border: none;
  background: transparent;
}

:deep(.ant-collapse-item) {
  margin-bottom: 16px;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  overflow: hidden;
}

:deep(.ant-collapse-header) {
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  padding: 16px 20px !important;
  font-weight: 600;
}

:deep(.ant-collapse-content) {
  border-top: 1px solid #e8e8e8;
}

:deep(.ant-collapse-content-box) {
  padding: 20px;
}

/* 统计卡片样式 */
:deep(.ant-statistic-title) {
  font-size: 14px;
  color: #666;
  margin-bottom: 8px;
}

:deep(.ant-statistic-content) {
  font-size: 24px;
  font-weight: 600;
  color: #1890ff;
}

/* 景点卡片样式 */
:deep(.ant-list-item) {
  transition: all 0.3s ease;
}

:deep(.ant-list-item:hover) {
  transform: scale(1.02);
}

/* 动画 */
@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 响应式设计 */
@media (max-width: 768px) {
  .result-container {
    padding: 20px 10px;
  }

  .page-header {
    flex-direction: column;
    gap: 16px;
  }
}
</style>


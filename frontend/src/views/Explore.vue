<template>
  <main class="explore-page">
    <section class="explore-shell">
      <header class="page-header">
        <a-button class="back-button" @click="router.push('/')">← 首页</a-button>
        <div class="title-block">
          <span class="eyebrow">AGENT · AMAP MCP</span>
          <h1>发现值得去的景点</h1>
          <p>Agent 通过高德 MCP 获取真实 POI 与对应图片；开放信息仍请以景区公告为准。</p>
        </div>
        <a-tag class="agent-badge" color="purple">Agent 数据链路</a-tag>
      </header>

      <div class="search-panel">
        <div class="field city-field"><label>目的地</label><a-input v-model:value="city" placeholder="例如：北京" /></div>
        <div class="field category-field"><label>类别</label><a-select v-model:value="category">
          <a-select-option value="">全部类别</a-select-option><a-select-option value="博物馆">博物馆</a-select-option>
          <a-select-option value="公园">公园</a-select-option><a-select-option value="历史文化">历史文化</a-select-option>
        </a-select></div>
        <div class="field keyword-field"><label>想去哪里</label><a-input-search v-model:value="keyword" placeholder="输入景点或主题" enter-button="搜索" :loading="loading" @search="search" /></div>
      </div>

      <a-alert v-if="error" type="error" :message="error" show-icon class="status-alert" />
      <div v-if="searched && !loading && results.length" class="result-summary">
        <span>找到 {{ results.length }} 个可信地点</span><span>图片来自对应 POI，缺图时会明确标注</span>
      </div>
      <a-empty v-if="searched&&!loading&&!results.length" class="explore-empty" description="没有找到可信景点，请尝试更具体的名称" />

      <a-row :gutter="[20, 20]" class="poi-grid">
        <a-col v-for="poi in results" :key="poi.id" :xs="24" :md="12" :xl="8">
          <article class="poi-card">
            <div class="poi-image-wrap">
              <img :src="photoUrl(poi)" :alt="`${poi.name}参考图片`" class="poi-image" loading="lazy" />
              <div class="image-note">地点参考图</div>
            </div>
            <div class="poi-body">
              <div class="poi-title-row"><h2>{{ poi.name }}</h2><span class="poi-id">#{{ poi.id }}</span></div>
              <div class="type-tags">
                <a-tag v-for="type in poiTypes(poi.type)" :key="type" color="blue">{{ type }}</a-tag>
                <a-tag v-if="!poiTypes(poi.type).length">景点</a-tag>
              </div>
              <p class="poi-fact"><span>📍</span><span>{{ poi.address || '地址待确认' }}</span></p>
              <p class="poi-fact"><span>🕐</span><span>{{ poi.opening_hours || '暂无可靠开放时间，请查景区公告' }}</span></p>
            </div>
          </article>
        </a-col>
      </a-row>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { searchAttractionPOIs } from '@/services/map'
import type { POIInfo } from '@/types'

const router = useRouter()
const city = ref('北京')
const keyword = ref('景点')
const category = ref('')
const results = ref<POIInfo[]>([])
const loading = ref(false)
const searched = ref(false)
const error = ref('')

const poiTypes = (raw: string) => {
  const values = [...new Set((raw || '').split(/[;|]/).map(item => item.trim()).filter(Boolean))]
  const specific = values.filter(item => !['风景名胜', '科教文化服务', '公园广场'].includes(item))
  return (specific.length ? specific : values).slice(0, 3)
}

const photoUrl = (poi: POIInfo) => `/api/poi/photo/image?name=${encodeURIComponent(poi.name)}&poi_id=${encodeURIComponent(poi.id)}&city=${encodeURIComponent(city.value)}&v=explore-v1`

async function search() {
  if (!city.value.trim() || !keyword.value.trim()) { error.value = '请填写城市和景点关键词'; return }
  loading.value = true
  error.value = ''
  try {
    results.value = await searchAttractionPOIs(city.value.trim(), `${keyword.value.trim()} ${category.value}`.trim())
    searched.value = true
  } catch (e: any) {
    results.value = []
    searched.value = true
    error.value = e?.response?.data?.message || '景点搜索暂不可用，请稍后重试'
  } finally { loading.value = false }
}

search()
</script>

<style scoped>
.explore-page{min-height:100vh;padding:32px 24px 64px;color:#172033}.explore-shell{max-width:1240px;margin:auto}.page-header{display:grid;grid-template-columns:150px 1fr 150px;align-items:start;gap:24px;padding:34px 38px;background:linear-gradient(135deg,rgba(255,255,255,.97),rgba(240,245,255,.94));border:1px solid rgba(255,255,255,.7);border-radius:28px 28px 0 0}.title-block{text-align:center}.eyebrow{display:block;color:#5b5bd6;font-size:12px;font-weight:800;letter-spacing:.16em;margin-bottom:8px}.title-block h1{font-size:38px;line-height:1.2;margin:0;color:#101828}.title-block p{margin:12px 0 0;color:#52606d}.agent-badge{justify-self:end;margin-top:4px}.search-panel{display:grid;grid-template-columns:180px 190px 1fr;gap:14px;padding:22px 38px 28px;background:rgba(255,255,255,.94);box-shadow:0 24px 54px rgba(7,10,31,.28);border-radius:0 0 28px 28px}.field{display:flex;flex-direction:column;gap:7px}.field label{font-size:13px;font-weight:700;color:#475467}.field :deep(.ant-select),.field :deep(.ant-input-group-wrapper){width:100%}.field :deep(.ant-input),.field :deep(.ant-select-selector),.field :deep(.ant-input-search-button){height:42px}.status-alert{margin:20px 0}.result-summary{display:flex;justify-content:space-between;color:#dbeafe;font-size:14px;margin:24px 4px 12px}.poi-grid{margin-top:10px}.poi-card{display:flex;flex-direction:column;height:100%;overflow:hidden;background:#fff;border:1px solid rgba(255,255,255,.75);border-radius:20px;box-shadow:0 15px 35px rgba(3,7,30,.24);transition:transform .25s ease,box-shadow .25s ease}.poi-card:hover{transform:translateY(-5px);box-shadow:0 22px 45px rgba(3,7,30,.34)}.poi-image-wrap{position:relative;height:190px;background:#e8eef8;overflow:hidden;flex:0 0 auto}.poi-image{width:100%;height:100%;object-fit:cover;display:block;transition:transform .35s ease}.poi-card:hover .poi-image{transform:scale(1.035)}.image-note{position:absolute;left:14px;bottom:12px;padding:5px 10px;border-radius:999px;background:rgba(15,23,42,.76);color:#fff;font-size:12px;backdrop-filter:blur(8px)}.poi-body{padding:20px 20px 18px;flex:1}.poi-title-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.poi-title-row h2{margin:0;color:#101828;font-size:22px;line-height:1.35}.poi-id{font-size:11px;color:#98a2b3;white-space:nowrap;margin-top:5px}.type-tags{min-height:28px;margin:12px 0 14px}.poi-fact{display:grid;grid-template-columns:22px 1fr;gap:5px;margin:10px 0;color:#344054;line-height:1.6}@media(max-width:760px){.explore-page{padding:12px}.page-header{grid-template-columns:1fr;padding:24px}.title-block{text-align:left}.title-block h1{font-size:30px}.agent-badge{justify-self:start}.search-panel{grid-template-columns:1fr;padding:20px 24px}.result-summary{align-items:flex-start;flex-direction:column;gap:6px}}
.explore-empty { margin-top: 22px; padding: 68px 20px; border-radius: 18px; background: rgba(255,255,255,.96); box-shadow: 0 18px 46px rgba(2,6,23,.28); }
.explore-empty :deep(.ant-empty-description) { color: var(--trip-text-on-light); font-size: 16px; }
</style>

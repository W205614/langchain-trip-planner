<template>
  <main class="community-page">
    <section class="shell">
      <header class="hero glass-card">
        <a-button @click="router.push('/')">← 首页</a-button>
        <div><span class="eyebrow">REVIEWED COMMUNITY ROUTES</span><h1>行程玩法广场</h1>
          <p>这里只展示审核通过的行程快照；复制后会成为你的独立草稿，并要求重新核验。</p></div>
        <a-button v-if="loggedIn" type="primary" @click="router.push('/history')">投稿我的行程</a-button>
        <a-button v-else @click="router.push('/login?redirect=/community')">登录后复制</a-button>
      </header>

      <a-alert v-if="error" type="error" show-icon :message="error" class="alert" />
      <div v-if="loading" class="loading"><a-spin size="large" tip="正在读取审核通过的行程..." /></div>
      <a-empty v-else-if="!cards.length" description="还没有已发布的社区行程" class="empty" />
      <a-row v-else :gutter="[20,20]" class="grid">
        <a-col v-for="card in cards" :key="card.id" :xs="24" :md="12" :xl="8">
          <article class="card glass-card">
            <div class="card-top"><a-tag color="purple">{{ card.city }}</a-tag><span>版本 {{ card.record_version }}</span></div>
            <h2>{{ card.title }}</h2>
            <p class="author">由 {{ card.author }} 分享 · {{ card.published_at }}</p>
            <div class="facts">
              <span>📅 {{ card.snapshot.start_date }} ~ {{ card.snapshot.end_date }}</span>
              <span>🗓️ {{ card.snapshot.travel_days }} 天</span>
              <span>🚇 {{ card.snapshot.transportation }}</span>
            </div>
            <div class="days">
              <div v-for="day in (card.snapshot.plan?.days || []).slice(0,3)" :key="day.day_index">
                <b>Day {{ day.day_index + 1 }}</b><span>{{ day.theme || day.description }}</span>
              </div>
            </div>
            <a-button block type="primary" :loading="copying===card.id" @click="copy(card.id)">复制为我的草稿</a-button>
          </article>
        </a-col>
      </a-row>
      <a-pagination v-if="total>pageSize" v-model:current="page" :total="total" :page-size="pageSize" class="pager" @change="load" />
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { copyCommunityCard, fetchCommunityCards } from '@/services/api'
import { isAuthenticated } from '@/services/auth'

const router = useRouter()
const loggedIn = isAuthenticated()
const cards = ref<any[]>([])
const loading = ref(false)
const copying = ref(0)
const error = ref('')
const total = ref(0)
const page = ref(1)
const pageSize = 12

async function load() {
  loading.value = true; error.value = ''
  try { const result = await fetchCommunityCards(page.value, pageSize); cards.value = result.data || []; total.value = result.total || 0 }
  catch (e: any) { error.value = e.response?.data?.detail || '行程广场暂不可用' }
  finally { loading.value = false }
}

async function copy(id: number) {
  if (!loggedIn) { await router.push({ path: '/login', query: { redirect: '/community' } }); return }
  copying.value = id
  try { await copyCommunityCard(id); message.success('已复制到我的行程，请重新核验后使用'); await router.push('/history') }
  catch (e: any) { message.error(e.response?.data?.detail || '复制失败') }
  finally { copying.value = 0 }
}

onMounted(load)
</script>

<style scoped>
.community-page{min-height:100vh;padding:36px 24px 70px}.shell{max-width:1240px;margin:auto}.hero{display:grid;grid-template-columns:150px 1fr 170px;align-items:center;gap:22px;border-radius:26px;padding:30px 36px}.hero h1{margin:4px 0 8px;color:#111827;font-size:36px}.hero p{margin:0;color:#596579}.eyebrow{color:#6757d9;font-size:12px;font-weight:800;letter-spacing:.14em}.alert,.grid{margin-top:22px}.loading,.empty{padding:100px 0;color:#fff}.card{height:100%;padding:24px;border-radius:22px;box-sizing:border-box}.card-top{display:flex;justify-content:space-between;color:#667085;font-size:12px}.card h2{font-size:22px;color:#101828;margin:14px 0 6px}.author{color:#667085}.facts{display:flex;flex-direction:column;gap:6px;color:#344054;margin:16px 0}.days{min-height:106px;margin:0 0 18px;padding:12px;background:#f6f7ff;border-radius:12px}.days div{display:grid;grid-template-columns:60px 1fr;gap:8px;margin:5px 0;color:#475467}.days span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.pager{text-align:center;margin-top:28px;padding:12px;background:rgba(255,255,255,.9);border-radius:12px}@media(max-width:760px){.community-page{padding:14px}.hero{grid-template-columns:1fr;padding:24px}.hero h1{font-size:30px}}
</style>

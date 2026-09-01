<template>
  <main class="page-shell">
    <section class="panel">
      <a-button type="link" @click="router.push('/')">← 返回首页</a-button>
      <h1>旅行资料研究</h1>
      <a-alert type="info" show-icon message="仅展示检索到的公开资料及来源，不把资料片段改写为未经验证的结论。" />
      <a-form :model="form" layout="vertical" class="form" @finish="search">
        <a-form-item label="城市" name="city" :rules="[{ required: true, message: '请输入城市' }]">
          <a-input v-model:value="form.city" placeholder="例如：北京" />
        </a-form-item>
        <a-form-item label="想了解什么" name="query" :rules="[{ required: true, message: '请输入问题' }]">
          <a-textarea v-model:value="form.query" :rows="3" :maxlength="300" show-count placeholder="例如：带孩子参观博物馆需要注意什么？" />
        </a-form-item>
        <a-button type="primary" html-type="submit" :loading="loading">检索资料</a-button>
      </a-form>

      <a-empty v-if="searched && !evidence.length" description="没有找到匹配资料；可换一种问法或先投稿审核后的攻略。" />
      <a-list v-if="evidence.length" :data-source="evidence" class="results">
        <template #renderItem="{ item, index }">
          <a-list-item>
            <a-card :title="`证据 ${index + 1}`" size="small">
              <template #extra><a-tag>{{ tierLabel(item.source_tier) }}</a-tag></template>
              <div class="source">{{ item.source }}<span v-if="item.page"> · 第{{ item.page }}页</span></div>
              <pre>{{ item.content }}</pre>
            </a-card>
          </a-list-item>
        </template>
      </a-list>
    </section>
  </main>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import { researchTravel } from '@/services/api'

const router = useRouter()
const form = reactive({ city: '', query: '' })
const loading = ref(false)
const searched = ref(false)
const evidence = ref<any[]>([])
const labels: Record<string, string> = {
  official: '官方资料', reviewed: '人工核验', community: '投稿资料',
  curated_static: '内置精选', amap_live: '高德实时数据'
}
const tierLabel = (tier: string) => labels[tier] || tier || '来源未标注'

const search = async () => {
  if (!form.city.trim() || form.query.trim().length < 2) {
    message.warning('请输入城市和至少两个字的问题')
    return
  }
  loading.value = true
  searched.value = false
  try {
    const response = await researchTravel(form.city.trim(), form.query.trim())
    evidence.value = response.data?.evidence || []
    searched.value = true
  } catch (error: any) {
    message.error(error.response?.data?.message || error.message || '资料检索失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.page-shell { max-width: 900px; margin: 0 auto; padding: 48px 20px; }
.panel { background: rgba(255,255,255,.96); border-radius: 20px; padding: 28px; box-shadow: 0 20px 45px rgba(2,6,23,.25); }
.form, .results { margin-top: 20px; }
.source { color: #64748b; margin-bottom: 10px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; margin: 0; line-height: 1.7; }
</style>

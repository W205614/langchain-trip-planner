<template>
  <main class="page-shell">
    <section class="panel">
      <a-button type="link" @click="router.push('/')">← 返回首页</a-button>
      <h1>旅行资料研究</h1>
      <a-alert type="info" show-icon message="默认只检索现有公开索引，不调用生成或嵌入模型。需要问答时请明确选择；引用匹配不代表事实已独立核验。" />
      <a-form :model="form" layout="vertical" class="form" @finish="search">
        <a-form-item label="城市" name="city" :rules="[{ required: true, message: '请输入城市' }]">
          <a-input v-model:value="form.city" placeholder="例如：北京" />
        </a-form-item>
        <a-form-item label="想了解什么" name="query" :rules="[{ required: true, message: '请输入问题' }]">
          <a-textarea v-model:value="form.query" :rows="3" :maxlength="300" show-count placeholder="例如：带孩子参观博物馆需要注意什么？" />
        </a-form-item>
        <a-space><a-button type="primary" html-type="submit" :loading="loading">仅检索资料</a-button>
          <a-button :loading="loading" @click="searchAnswer">根据资料回答（使用模型）</a-button></a-space>
      </a-form>

      <section v-if="answer" class="results" data-testid="research-answer">
        <a-tag>{{ answerStatus }}</a-tag><pre>{{ answer }}</pre>
        <p>语义真实性：未进行独立评估。{{ supportNote }}</p>
      </section>
      <p v-if="searched && mode === 'retrieval_only'" data-testid="retrieval-call-boundary">本次仅检索：生成调用 0 次，不自动构建城市索引。</p>

      <a-empty v-if="searched && !evidence.length" description="没有找到匹配资料；可换一种问法或先投稿审核后的攻略。" />
      <a-list v-if="evidence.length" :data-source="evidence" class="results">
        <template #renderItem="{ item, index }">
          <a-list-item>
            <a-card :title="mode === 'answer' ? (item.citation_index ? `可引用证据 ${item.citation_index}` : '仅供参考的资料') : `证据 ${index + 1}`" size="small">
              <template #extra><a-tag>{{ tierLabel(item.source_tier) }}（元数据声明）</a-tag></template>
              <div class="source">{{ item.source }}<span v-if="item.page"> · 第{{ item.page }}页</span></div>
              <a-tag v-if="item.data_classification === 'demonstration'" color="orange">演示资料</a-tag>
              <a-tag>{{ freshnessLabel(item.freshness) }}</a-tag>
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
import { researchTravel, answerTravelQuestion } from '@/services/api'

const router = useRouter()
const form = reactive({ city: '', query: '' })
const loading = ref(false)
const searched = ref(false)
const evidence = ref<any[]>([])
const answer = ref('')
const answerStatus = ref('')
const supportNote = ref('')
const mode = ref('retrieval_only')
const labels: Record<string, string> = {
  official: '官方资料', reviewed: '人工核验', community: '投稿资料',
  curated_static: '内置精选', amap_live: '高德实时数据'
}
const tierLabel = (tier: string) => labels[tier] || tier || '来源未标注'
const freshnessLabel = (value: string) => (({ unknown: '时效未核验', dated: '附来源时间', expired: '已过期，仅供参考', invalid_timestamp: '时间元数据异常' } as Record<string, string>)[value] || '时效未核验')

const searchAnswer = () => search('answer')
const search = async (requestedMode: any = 'retrieval_only') => {
  if (!form.city.trim() || form.query.trim().length < 2) {
    message.warning('请输入城市和至少两个字的问题')
    return
  }
  loading.value = true
  searched.value = false
  evidence.value = []
  answer.value = ''
  mode.value = requestedMode === 'answer' ? 'answer' : 'retrieval_only'
  try {
    const response = await (mode.value === 'answer' ? answerTravelQuestion : researchTravel)(form.city.trim(), form.query.trim())
    evidence.value = response.data?.evidence || []
    answer.value = response.data?.answer || ''
    answerStatus.value = ({ supported_excerpt_answer: '已核对原句引用', extractive_fallback: '生成内容未通过支持校验，显示摘录', insufficient_evidence: '有效证据不足' } as Record<string, string>)[response.data?.answer_status] || ''
    supportNote.value = response.data?.support_check?.passed ? '仅通过保守原句匹配。' : '未将未经支持的生成内容展示为结论。'
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

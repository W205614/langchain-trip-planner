<template>
  <main class="page-shell">
    <section class="panel">
      <a-button type="link" @click="router.push('/')">← 返回首页</a-button>
      <h1>投稿公共旅行攻略</h1>
      <p class="hint">支持 JPEG、PNG、GIF、WebP 和扫描 PDF（20 MB、最多 10 页）。管理员审核后，所有用户都能在旅行规划中使用。上传内容会发送至配置的视觉模型解析。</p>
      <a-form layout="vertical" @finish="submit">
        <a-form-item label="目标城市" required><a-input v-model:value="city" placeholder="例如：北京" /></a-form-item>
        <a-form-item label="资料标题" required><a-input v-model:value="title" placeholder="例如：故宫参观攻略" /></a-form-item>
        <a-form-item label="图片或扫描 PDF" required><input type="file" accept="image/jpeg,image/png,image/gif,image/webp,application/pdf" @change="selectFile" /></a-form-item>
        <a-button type="primary" html-type="submit" :loading="submitting">提交审核</a-button>
      </a-form>
    </section>
    <section class="panel">
      <h2>我的投稿</h2>
      <a-empty v-if="!items.length" description="还没有提交资料" />
      <a-list :data-source="items" bordered>
        <template #renderItem="{ item }"><a-list-item><b>{{ item.title }}</b> · {{ item.city }} <a-tag>{{ item.status }}</a-tag><span v-if="item.review_note">{{ item.review_note }}</span></a-list-item></template>
      </a-list>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import { fetchMyKnowledge, submitKnowledge } from '@/services/api'

const router = useRouter()
const city = ref('')
const title = ref('')
const file = ref<File | null>(null)
const items = ref<any[]>([])
const submitting = ref(false)
const load = async () => { items.value = (await fetchMyKnowledge()).data || [] }
const selectFile = (event: Event) => { file.value = (event.target as HTMLInputElement).files?.[0] || null }
const submit = async () => {
  if (!city.value.trim() || !title.value.trim() || !file.value) return message.error('请填写城市、标题并选择文件')
  submitting.value = true
  try { await submitKnowledge(city.value.trim(), title.value.trim(), file.value); message.success('已提交审核'); title.value = ''; file.value = null; await load() }
  catch (error: any) { message.error(error.response?.data?.detail || error.message || '提交失败') }
  finally { submitting.value = false }
}
onMounted(load)
</script>

<style scoped>
.page-shell { max-width: 920px; margin: 0 auto; padding: 48px 20px; }
.panel { background: rgba(255,255,255,.96); border-radius: 20px; padding: 28px; margin-bottom: 20px; box-shadow: 0 20px 45px rgba(2,6,23,.25); }
.hint { color: #64748b; line-height: 1.7; }
</style>

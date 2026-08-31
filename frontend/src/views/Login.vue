<template>
  <div class="login-container">
    <div class="bg-decoration">
      <div class="circle circle-1"></div>
      <div class="circle circle-2"></div>
      <div class="circle circle-3"></div>
    </div>

    <div class="login-card">
      <div class="login-header">
        <div class="brand-badge">🔐 用户登录</div>
        <h1 class="login-title">{{ isRegister ? '注册账号' : '欢迎回来' }}</h1>
        <p class="login-subtitle">
          {{ isRegister ? '创建账号后即可查看和管理你的历史行程' : '登录后可查看和管理你的历史行程' }}
        </p>
      </div>

      <a-form
        :model="form"
        layout="vertical"
        @finish="handleSubmit"
      >
        <a-form-item
          name="username"
          :rules="[{ required: true, min: 3, max: 32, message: '用户名需3-32字符' }]"
        >
          <a-input
            v-model:value="form.username"
            placeholder="用户名"
            size="large"
            class="auth-input"
          >
            <template #prefix>👤</template>
          </a-input>
        </a-form-item>

        <a-form-item
          name="password"
          :rules="[{ required: true, min: 6, message: '密码至少6位' }]"
        >
          <a-input-password
            v-model:value="form.password"
            placeholder="密码"
            size="large"
            class="auth-input"
          >
            <template #prefix>🔑</template>
          </a-input-password>
        </a-form-item>

        <a-form-item v-if="isRegister" name="confirmPassword">
          <a-input-password
            v-model:value="form.confirmPassword"
            placeholder="确认密码"
            size="large"
            class="auth-input"
          >
            <template #prefix>🔒</template>
          </a-input-password>
        </a-form-item>

        <a-button
          type="primary"
          html-type="submit"
          size="large"
          block
          :loading="loading"
          class="submit-button"
        >
          {{ isRegister ? '注册并登录' : '登 录' }}
        </a-button>
      </a-form>

      <div class="switch-mode">
        <span>{{ isRegister ? '已有账号?' : '还没有账号?' }}</span>
        <a-button type="link" @click="toggleMode">
          {{ isRegister ? '去登录' : '去注册' }}
        </a-button>
      </div>

      <a-button class="back-home" type="text" @click="goHome">
        ← 返回首页
      </a-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import { login, register } from '@/services/api'
import { setAuth } from '@/services/auth'

const router = useRouter()
const route = useRoute()
const isRegister = ref(false)
const loading = ref(false)

const form = reactive({
  username: '',
  password: '',
  confirmPassword: ''
})

const toggleMode = () => {
  isRegister.value = !isRegister.value
  form.password = ''
  form.confirmPassword = ''
}

const goHome = () => {
  router.push('/')
}

const handleSubmit = async () => {
  if (isRegister.value && form.password !== form.confirmPassword) {
    message.error('两次输入的密码不一致')
    return
  }
  loading.value = true
  try {
    const resp = isRegister.value
      ? await register(form.username, form.password)
      : await login(form.username, form.password)
    setAuth(resp.access_token, resp.username, resp.is_admin)
    message.success(isRegister.value ? '注册成功!' : '登录成功!')
    // 登录后跳回来源页；直接登录或注册默认进入旅行规划首页。
    const redirect = (route.query.redirect as string) || '/'
    router.push(redirect)
  } catch (error: any) {
    message.error(error.response?.data?.detail || error.message || '操作失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  padding: 20px;
}

.bg-decoration {
  position: absolute;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
}

.circle {
  position: absolute;
  border-radius: 50%;
  animation: float 20s infinite ease-in-out;
}

.circle-1 {
  width: 320px; height: 320px; top: -100px; left: -100px;
  background: radial-gradient(circle, rgba(139,92,246,.28), transparent 70%);
}
.circle-2 {
  width: 240px; height: 240px; bottom: -60px; right: -60px; animation-delay: 5s;
  background: radial-gradient(circle, rgba(59,130,246,.24), transparent 70%);
}
.circle-3 {
  width: 180px; height: 180px; top: 50%; left: 30%; animation-delay: 10s;
  background: radial-gradient(circle, rgba(16,185,129,.2), transparent 70%);
}

@keyframes float {
  0%, 100% { transform: translateY(0) rotate(0deg); }
  50% { transform: translateY(-30px) rotate(180deg); }
}

.login-card {
  width: 400px;
  max-width: 100%;
  padding: 40px;
  border-radius: 24px;
  background: rgba(255,255,255,.95);
  backdrop-filter: blur(20px) saturate(150%);
  border: 1px solid rgba(255,255,255,.55);
  box-shadow: 0 30px 80px rgba(2,6,23,.5);
  animation: fadeInUp .6s ease-out;
  position: relative;
  z-index: 1;
}

.login-header {
  text-align: center;
  margin-bottom: 28px;
}

.brand-badge {
  display: inline-block;
  padding: 6px 18px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  color: #667eea;
  background: #f5f7ff;
  margin-bottom: 16px;
}

.login-title {
  margin: 0 0 8px;
  font-size: 28px;
  font-weight: 800;
  color: #1e293b;
}

.login-subtitle {
  margin: 0;
  color: #64748b;
  font-size: 14px;
}

.auth-input :deep(.ant-input),
.auth-input :deep(.ant-input-password) {
  border-radius: 12px;
  border: 2px solid #e8e8e8;
}

.auth-input :deep(.ant-input:hover),
.auth-input :deep(.ant-input-password:hover) {
  border-color: #667eea;
}

.auth-input :deep(.ant-input:focus),
.auth-input :deep(.ant-input-password:focus),
.auth-input :deep(.ant-input-affix-wrapper-focused) {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102,126,234,.1);
}

.submit-button {
  height: 48px;
  border-radius: 24px;
  font-size: 16px;
  font-weight: 600;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  box-shadow: 0 8px 24px rgba(102,126,234,.4);
}

.switch-mode {
  margin-top: 20px;
  text-align: center;
  color: #64748b;
}

.back-home {
  display: block;
  margin: 12px auto 0;
  color: #94a3b8;
}

@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(30px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>

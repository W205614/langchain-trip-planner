import axios from 'axios'
import type { TripFormData, TripPlanResponse } from '@/types'
import { getToken, clearAuth } from './auth'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:9000'

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000, // 2分钟超时
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器: 自动附带 JWT
apiClient.interceptors.request.use(
  (config) => {
    const token = getToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    console.log('发送请求:', config.method?.toUpperCase(), config.url)
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器: 401 统一处理 (登录失效 → 清除本地 token → 跳登录页)
apiClient.interceptors.response.use(
  (response) => {
    console.log('收到响应:', response.status, response.config.url)
    return response
  },
  (error) => {
    console.error('响应错误:', error.response?.status, error.message)
    if (error.response?.status === 401) {
      clearAuth()
      // 跳转到登录页 (仅当前不在登录页时)
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

/**
 * 用户注册 (成功自动登录, 返回 token)
 */
export async function register(username: string, password: string): Promise<any> {
  const response = await apiClient.post('/api/auth/register', { username, password })
  return response.data
}

/**
 * 用户登录
 */
export async function login(username: string, password: string): Promise<any> {
  const response = await apiClient.post('/api/auth/login', { username, password })
  return response.data
}

/**
 * 生成旅行计划
 */
export async function generateTripPlan(formData: TripFormData): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.post<TripPlanResponse>('/api/trip/plan', formData)
    return response.data
  } catch (error: any) {
    console.error('生成旅行计划失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '生成旅行计划失败')
  }
}

export interface TripPlanProgress {
  stage: string
  percent: number
  message: string
}

/**
 * 通过 POST + SSE 接收后端真实阶段进度。fetch 支持 Authorization Header，
 * 因此不会把 JWT 放入 URL；浏览器不支持流时由调用方回退到普通接口。
 */
export async function generateTripPlanStream(
  formData: TripFormData,
  onProgress: (progress: TripPlanProgress) => void
): Promise<TripPlanResponse> {
  const token = getToken()
  const idempotencyKey = crypto.randomUUID()
  const response = await fetch(`${API_BASE_URL}/api/trip/plan/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      'Idempotency-Key': idempotencyKey
    },
    body: JSON.stringify(formData)
  })
  if (response.status === 401) {
    clearAuth()
    window.location.href = '/login'
    throw new Error('登录已过期')
  }
  if (!response.ok || !response.body) {
    throw new Error(`流式请求失败 (${response.status})`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() || ''
    for (const block of blocks) {
      const event = block.match(/^event: (.+)$/m)?.[1]
      const data = block.match(/^data: (.+)$/m)?.[1]
      if (!event || !data) continue
      const payload = JSON.parse(data)
      if (event === 'progress') onProgress(payload as TripPlanProgress)
      if (event === 'complete') return payload as TripPlanResponse
      if (event === 'error') throw new Error(payload.message || '生成旅行计划失败')
    }
    if (done) break
  }
  throw new Error('生成连接意外关闭')
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    console.error('健康检查失败:', error)
    throw new Error(error.message || '健康检查失败')
  }
}

/**
 * 查询历史行程列表 (分页)
 */
export async function fetchHistory(
  page: number = 1,
  pageSize: number = 10,
  city?: string
): Promise<any> {
  const response = await apiClient.get('/api/history', {
    params: { page, page_size: pageSize, city: city || undefined }
  })
  return response.data
}

/**
 * 查询历史行程详情 (含完整计划)
 */
export async function fetchHistoryDetail(id: number): Promise<any> {
  const response = await apiClient.get(`/api/history/${id}`)
  return response.data
}

/**
 * 更新历史行程 (编辑保存后持久化)
 */
export async function updateHistory(id: number, plan: any): Promise<any> {
  const response = await apiClient.put(`/api/history/${id}`, plan)
  return response.data
}

/**
 * 删除历史行程
 */
export async function deleteHistory(id: number): Promise<any> {
  const response = await apiClient.delete(`/api/history/${id}`)
  return response.data
}

export default apiClient

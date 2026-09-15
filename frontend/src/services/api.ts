import axios from 'axios'
import type { TripFormData, TripPlanResponse } from '@/types'
import { getToken, clearAuth } from './auth'

// Docker/Nginx 生产演示使用同源代理；Vite 开发仍直连本机后端，保持原有联调体验。
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? 'http://localhost:9000' : '')

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000 // 2分钟超时；由 Axios 按 JSON/FormData 自动设置 Content-Type
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

export async function logout(): Promise<void> {
  await apiClient.post('/api/auth/logout')
  clearAuth()
}

export async function fetchTasks(page = 1): Promise<any> {
  return (await apiClient.get('/api/trip/tasks', { params: { page } })).data
}

export async function fetchTask(id: string): Promise<any> {
  return (await apiClient.get(`/api/trip/tasks/${id}`)).data.data
}

export async function cancelTask(id: string): Promise<any> {
  return (await apiClient.post(`/api/trip/tasks/${id}/cancel`)).data
}

export async function retryTask(id: string, key: string): Promise<any> {
  return (await apiClient.post(`/api/trip/tasks/${id}/retry`, {}, { headers: { 'Idempotency-Key': key } })).data
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
  const fingerprint = JSON.stringify(formData)
  let pending = JSON.parse(sessionStorage.getItem('pendingTripTask') || 'null')
  if (!pending || pending.fingerprint !== fingerprint) {
    pending = { fingerprint, body: formData, key: crypto.randomUUID(), id: null }
    sessionStorage.setItem('pendingTripTask', JSON.stringify(pending))
  }
  if (!pending.id) {
    const created = await apiClient.post('/api/trip/tasks', formData, {
      headers: { 'Idempotency-Key': pending.key }
    })
    pending.id = created.data.data.id
    sessionStorage.setItem('pendingTripTask', JSON.stringify(pending))
  }
  const response = await fetch(`${API_BASE_URL}/api/trip/tasks/${pending.id}/events`, {
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    signal: AbortSignal.timeout(310000)
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
      const event = block.match(/^event:[ \t]*(.+)$/m)?.[1]
      const data = block.match(/^data:[ \t]*(.+)$/m)?.[1]
      if (!event || !data) continue
      const payload = JSON.parse(data)
      if (event === 'progress') onProgress(payload as TripPlanProgress)
      if (event === 'complete') {
        sessionStorage.removeItem('pendingTripTask')
        await reader.cancel()
        return payload as TripPlanResponse
      }
      if (event === 'error') {
        sessionStorage.removeItem('pendingTripTask')
        await reader.cancel()
        throw new Error(payload.message || '生成旅行计划失败')
      }
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
export async function updateHistory(id: number, plan: any, version: number): Promise<any> {
  const response = await apiClient.put(`/api/history/${id}`, plan, { headers: { 'If-Match': String(version) } })
  return response.data
}

/**
 * 删除历史行程
 */
export async function deleteHistory(id: number): Promise<any> {
  const response = await apiClient.delete(`/api/history/${id}`)
  return response.data
}

export async function submitKnowledge(city: string, title: string, file: File): Promise<any> {
  const form = new FormData()
  form.append('city', city)
  form.append('title', title)
  form.append('file', file)
  // 不手动设置 Content-Type，让浏览器补上 multipart boundary；否则部分环境会把文件请求判为无效。
  const response = await apiClient.post('/api/knowledge/submissions', form)
  return response.data
}

export async function fetchTravelPreferences(): Promise<any> {
  return (await apiClient.get('/api/preferences/me')).data
}

export async function saveTravelPreferences(payload: {
  preferences: string[]
  transportation: string
  accommodation: string
}): Promise<any> {
  return (await apiClient.put('/api/preferences/me', payload)).data
}

export async function researchTravel(city: string, query: string): Promise<any> {
  return (await apiClient.post('/api/research', { city, query })).data
}

/** 仅重新安排历史行程中的一个日期，不重新生成整份计划。 */
export async function reviseHistoryDay(id: number, dayIndex: number, instruction: string, version: number): Promise<any> {
  const fingerprint = JSON.stringify({ id, dayIndex, instruction, version })
  let pending = JSON.parse(sessionStorage.getItem('pendingRevision') || 'null')
  if (!pending || pending.fingerprint !== fingerprint) {
    pending = { fingerprint, key: crypto.randomUUID(), id: null }
    sessionStorage.setItem('pendingRevision', JSON.stringify(pending))
  }
  if (!pending.id) {
    const created = await apiClient.post(`/api/history/${id}/revise-task`, {
      day_index: dayIndex, instruction
    }, { headers: { 'If-Match': String(version), 'Idempotency-Key': pending.key } })
    pending.id = created.data.data.id
    sessionStorage.setItem('pendingRevision', JSON.stringify(pending))
  }
  const deadline = Date.now() + 310000
  while (Date.now() < deadline) {
    const state = await fetchTask(pending.id)
    if (['succeeded', 'needs_attention'].includes(state.status)) {
      sessionStorage.removeItem('pendingRevision')
      return state.result
    }
    if (['failed', 'cancelled'].includes(state.status)) {
      sessionStorage.removeItem('pendingRevision')
      throw new Error(`${state.message} (${state.error_code})`)
    }
    await new Promise(resolve => setTimeout(resolve, 1000))
  }
  throw new Error('等待超时，请在“我的任务”查看改排结果')
}

export async function fetchMyKnowledge(): Promise<any> {
  return (await apiClient.get('/api/knowledge/submissions/mine')).data
}

export async function fetchAdminKnowledge(status?: string): Promise<any> {
  return (await apiClient.get('/api/knowledge/admin/submissions', { params: { status_filter: status } })).data
}

export async function approveKnowledge(id: number, note: string = '', sourceTier: 'community' | 'reviewed' | 'official' = 'community'): Promise<any> {
  return (await apiClient.post(`/api/knowledge/admin/submissions/${id}/approve`, { note, source_tier: sourceTier })).data
}

export async function rejectKnowledge(id: number, note: string = ''): Promise<any> {
  return (await apiClient.post(`/api/knowledge/admin/submissions/${id}/reject`, { note })).data
}

export async function deleteKnowledge(id: number): Promise<any> {
  return (await apiClient.delete(`/api/knowledge/admin/submissions/${id}`)).data
}

export default apiClient

export function storeTripResult(response: TripPlanResponse): void {
  sessionStorage.removeItem('tripUnsaved')
  sessionStorage.setItem('tripPlan', JSON.stringify(response.data))
  sessionStorage.setItem('tripPlanId', String(response.id || 0))
  sessionStorage.setItem('tripPlanVersion', String(response.version || 1))
  sessionStorage.setItem('tripQuality', JSON.stringify(response.quality || {}))
}

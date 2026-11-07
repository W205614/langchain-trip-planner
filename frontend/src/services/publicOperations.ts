import axios from 'axios'
import { csrfToken, getToken } from './auth'

const base = import.meta.env.DEV ? (import.meta.env.VITE_API_BASE_URL || '') : ''
const client = axios.create({ baseURL: base, withCredentials: true, timeout: 30000 })
client.interceptors.request.use(config => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  if (!['get', 'head', 'options'].includes(config.method || 'get')) {
    const csrf = csrfToken()
    if (csrf) config.headers['X-CSRF-Token'] = csrf
  }
  return config
})

export interface DataJob {
  id: string; kind: 'export' | 'erasure'; status: string; stage: string
  error_code: string; created_at?: string; expires_at?: string; user_id?: number
}
export interface FeedbackItem {
  id: number; user_id: number; category: string; subject: string; message: string
  status: string; reply: string; version: number; trip_id?: number; task_id?: string
  request_id?: string; created_at: string
}
export interface OperationalUser {
  id: number; username: string; email?: string; email_verified_at?: string
  account_state: string; is_admin: boolean
}
export interface QuotaValues {
  daily_tasks: number | null; active_tasks: number | null; daily_text: number | null
  daily_embedding: number | null; daily_amap: number | null
}

export const publicOperations = {
  limits: async () => (await client.get('/api/usage/limits')).data,
  users: async (query = '', page = 1) => (await client.get('/api/admin/operations/users', { params: { query, page } })).data,
  userState: async (id: number, state: string, reason: string) => (await client.put(`/api/admin/operations/users/${id}/state`, { state, reason })).data,
  quotas: async (id: number) => (await client.get(`/api/admin/operations/users/${id}/quotas`)).data,
  saveQuotas: async (id: number, values: QuotaValues) => (await client.put(`/api/admin/operations/users/${id}/quotas`, values)).data,
  feedback: async () => (await client.get('/api/feedback')).data,
  submitFeedback: async (values: { category: string; subject: string; message: string; trip_id?: number; task_id?: string; request_id?: string }) => (await client.post('/api/feedback', values)).data,
  adminFeedback: async (status = '') => (await client.get('/api/admin/operations/feedback', { params: { status } })).data,
  reply: async (item: FeedbackItem, status: string, reply: string) => (await client.put(`/api/admin/operations/feedback/${item.id}`, { status, reply }, { headers: { 'If-Match': String(item.version) } })).data,
  mail: async () => (await client.get('/api/admin/mail')).data,
  replayMail: async (id: number) => (await client.post(`/api/admin/mail/${id}/replay`)).data,
  jobs: async () => (await client.get('/api/account/data-jobs')).data,
  adminJobs: async () => (await client.get('/api/admin/operations/data-jobs')).data,
  replayJob: async (id: string) => (await client.post(`/api/admin/operations/data-jobs/${id}/replay`)).data,
  requestExport: async () => (await client.post('/api/account/exports')).data,
  downloadExport: async (id: string) => (await client.get(`/api/account/exports/${id}/download`, { responseType: 'blob', timeout: 300000 })).data as Blob,
  erasurePreview: async () => (await client.get('/api/account/erasure-preview')).data,
  erase: async (values: { password: string; totp_code: string; confirmation: string }) => (await client.post('/api/account/erasure', values)).data,
  erasureStatus: async (id: string, receipt: string): Promise<DataJob> => {
    const response = await fetch(`${base}/api/auth/erasure/${encodeURIComponent(id)}`, {
      credentials: 'omit', headers: { 'X-Erasure-Receipt': receipt }, cache: 'no-store'
    })
    if (!response.ok) throw new Error('注销记录不存在、凭据错误或已过期')
    return response.json()
  }
}

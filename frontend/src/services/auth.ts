/**
 * 浏览器会话由同源 HttpOnly Cookie 承载，Storage 仅保存显示信息。
 * 旧 Bearer 客户端兼容读取保留；新登录不再把访问凭证写入 Storage。
 */

const TOKEN_KEY = 'access_token'
const USERNAME_KEY = 'username'
const ADMIN_KEY = 'is_admin'
let transientBearer: string | null = null

export function getToken(): string | null {
  return transientBearer || sessionStorage.getItem(TOKEN_KEY)
}

export function setAuth(token: string | undefined, username: string, isAdmin: boolean = false): void {
  if (getUsername() !== username) clearAuth()
  sessionStorage.removeItem(TOKEN_KEY)
  transientBearer = token || null
  sessionStorage.setItem('auth_mode', token ? 'bearer' : 'cookie')
  sessionStorage.setItem(USERNAME_KEY, username)
  sessionStorage.setItem(ADMIN_KEY, String(isAdmin))
}

export function getUsername(): string | null {
  return sessionStorage.getItem(USERNAME_KEY)
}

export function isAdmin(): boolean {
  return sessionStorage.getItem(ADMIN_KEY) === 'true'
}

export function clearAuth(): void {
  transientBearer = null
  sessionStorage.removeItem('auth_mode')
  for (const key of ['pendingRevision', 'pendingTripTask', 'tripPlan', 'tripPlanId', 'tripPlanVersion', 'tripQuality', 'tripUnsaved']) sessionStorage.removeItem(key)
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(USERNAME_KEY)
  sessionStorage.removeItem(ADMIN_KEY)
}

export function isAuthenticated(): boolean {
  return !!getToken() || (sessionStorage.getItem('auth_mode') === 'cookie' && !!getUsername())
}

export function csrfToken(): string {
  return decodeURIComponent(document.cookie.split('; ').find(value => value.startsWith('trip_csrf='))?.slice('trip_csrf='.length) || '')
}

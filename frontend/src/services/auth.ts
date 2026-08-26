/**
 * 前端登录态管理: JWT token 存取
 *
 * token 存 sessionStorage (页面关闭即失效), 兼顾刷新后保留与"非持久登录"的安全取舍。
 * 生产可换成 localStorage + 刷新续期。
 */

const TOKEN_KEY = 'access_token'
const USERNAME_KEY = 'username'

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function setAuth(token: string, username: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
  sessionStorage.setItem(USERNAME_KEY, username)
}

export function getUsername(): string | null {
  return sessionStorage.getItem(USERNAME_KEY)
}

export function clearAuth(): void {
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(USERNAME_KEY)
}

export function isAuthenticated(): boolean {
  return !!getToken()
}

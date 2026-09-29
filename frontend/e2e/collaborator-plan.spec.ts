import { expect, test } from '@playwright/test'

const plan = {
  city: '北京', start_date: '2026-10-01', end_date: '2026-10-02', overall_suggestions: '同行者查看完整行程',
  days: [
    { day_index: 0, date: '2026-10-01', description: '第一天参观故宫', transportation: '步行', accommodation: '酒店',
      attractions: [{ poi_id: 'P1', name: '故宫博物院', address: '北京市东城区', location: { longitude: 116.4, latitude: 39.9 }, visit_duration: 180, description: '参观宫殿' }], meals: [] },
    { day_index: 1, date: '2026-10-02', description: '第二天参观天坛', transportation: '地铁', accommodation: '酒店', attractions: [], meals: [] }
  ],
  budget: { total_attractions: 60, total_hotels: 300, total_meals: 100, total_transportation: 20, total: 480, assumptions: ['两天预算估算'] }
}

for (const role of ['viewer', 'editor'] as const) {
test(`accepted ${role} opens and refreshes the complete read-only itinerary`, async ({ page }) => {
  let workspaceReads = 0
  let historyReads = 0
  await page.addInitScript(() => {
    sessionStorage.setItem('access_token', 'viewer-fixture')
    sessionStorage.setItem('tripPlan', JSON.stringify({ city: '旧缓存行程', days: [] }))
    sessionStorage.setItem('tripPlanId', '99')
  })
  await page.route('**/api/trips/42/workspace', route => {
    workspaceReads++
    return route.fulfill({ json: { success: true, data: { id: 42, version: 3, role, city: '北京', travel_days: 2, plan, quality: {} } } })
  })
  await page.route('**/api/trips/42/checks/latest*', route => route.fulfill({ json: { success: true, data: { status: 'never_checked', risks: [] } } }))
  await page.route('**/api/trips/42/commitments', route => route.fulfill({ json: { success: true, data: [] } }))
  await page.route('**/api/trips/42/expenses', route => route.fulfill({ json: { success: true, data: { expenses: [], summary: {} } } }))
  await page.route('**/api/history/**', route => { historyReads++; return route.fulfill({ status: 403 }) })
  await page.route('**/api/poi/photo/image*', route => route.fulfill({ status: 404 }))

  await page.goto('/trips/42/operations')
  await expect(page.getByRole('button', { name: '查看完整行程' })).toBeVisible()
  await page.getByRole('button', { name: '查看完整行程' }).click()
  await expect(page).toHaveURL(/\/trips\/42\/plan$/)
  await expect(page.getByText('第一天参观故宫')).toBeVisible()
  await expect(page.getByText('两天预算估算')).toBeVisible()
  await expect(page.getByRole('button', { name: /编辑行程|AI 重新安排|分享/ })).toHaveCount(0)
  await page.reload()
  await page.getByRole('tab', { name: /第2天 2026-10-02/ }).click()
  await expect(page.getByText('第二天参观天坛')).toBeVisible()
  expect(workspaceReads).toBeGreaterThanOrEqual(3)
  expect(historyReads).toBe(0)
})
}

test('rejected collaborator cannot see a cached itinerary', async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem('access_token', 'removed-viewer')
    sessionStorage.setItem('tripPlan', JSON.stringify(plan))
  })
  await page.route('**/api/trips/42/workspace', route => route.fulfill({ status: 404, json: { detail: '行程不存在或无权访问' } }))
  await page.goto('/trips/42/plan')
  await expect(page.getByText('行程不存在或无权访问').first()).toBeVisible()
  await expect(page.getByText('第一天参观故宫')).toHaveCount(0)
})

test('accepted invitation opens the full itinerary directly', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'viewer-fixture'))
  await page.route('**/api/trips/invitations', route => route.fulfill({ json: { success: true, data: [
    { trip_id: 42, city: '北京', title: '同行行程', start_date: '2026-10-01', end_date: '2026-10-02', role: 'viewer', status: 'accepted' }
  ] } }))
  await page.route('**/api/notifications', route => route.fulfill({ json: { success: true, data: [] } }))
  await page.route('**/api/usage/summary', route => route.fulfill({ json: { success: true, data: { warning_limit: 100000 } } }))
  await page.route('**/api/trips/42/workspace', route => route.fulfill({ json: { success: true, data: { id: 42, version: 3, role: 'viewer', city: '北京', travel_days: 2, plan, quality: {} } } }))
  await page.route('**/api/poi/photo/image*', route => route.fulfill({ status: 404 }))
  await page.goto('/inbox')
  await page.getByRole('button', { name: '查看完整行程' }).click()
  await expect(page).toHaveURL(/\/trips\/42\/plan$/)
  await expect(page.getByText('第一天参观故宫')).toBeVisible()
})

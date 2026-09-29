import { expect, test } from '@playwright/test'

test('an old lazy chunk recovers to the requested page after a frontend rebuild', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'fixture-browser-token'))
  const plan = {
    city: '北京', start_date: '2026-10-01', end_date: '2026-10-01', overall_suggestions: '行程建议',
    constraints: { must_visit: [], avoid: [], daily_minutes: 600 }, weather_info: [], days: [{
      date: '2026-10-01', day_index: 0, description: '故宫一日游', transportation: '步行',
      accommodation: '经济型酒店', attractions: [{ poi_id: 'B0001', name: '故宫博物院',
        location: { longitude: 116.4, latitude: 39.9 }, visit_duration: 180, photos: [] }], meals: []
    }]
  }
  await page.route('**/api/history**', route => route.fulfill({ json: /\/api\/history\/61(?:\?|$)/.test(route.request().url())
    ? { success: true, data: { id: 61, version: 1, plan, quality: { outcome: 'complete', issues: [] } } }
    : { success: true, data: [{ id: 61, version: 1, city: '北京', start_date: '2026-10-01',
      end_date: '2026-10-01', travel_days: 1, attraction_count: 1 }], total: 1 } }))
  await page.route('**/api/poi/photo/image**', route => route.fulfill({ status: 404 }))
  let failedOnce = false
  await page.route('**/assets/Result-*.js', async route => {
    if (!failedOnce) {
      failedOnce = true
      await route.fulfill({ status: 404, contentType: 'text/plain', body: 'old chunk unavailable' })
    } else await route.continue()
  })

  await page.goto('/history')
  await page.getByRole('button', { name: '👁️ 查看行程' }).click()
  await expect.poll(() => failedOnce).toBe(true)
  await expect(page).toHaveURL(/\/result\?from=history$/)
  await expect(page.getByText('故宫博物院')).toBeVisible()
})

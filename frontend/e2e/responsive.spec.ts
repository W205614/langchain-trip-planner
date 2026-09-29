import { expect, test } from '@playwright/test'

test('mobile home keeps every signed-in navigation action on screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => {
    sessionStorage.setItem('access_token', 'fixture-browser-token')
    sessionStorage.setItem('username', 'mobile-fixture')
  })
  await page.route('**/api/preferences/me', route => route.fulfill({ json: { success: true, data: { saved: false } } }))
  await page.route('**/api/capabilities', route => route.fulfill({ json: { agent: 'ready', rag: 'disabled', map: 'ready', vision: 'disabled' } }))

  await page.goto('/')
  const actions = page.locator('.top-actions')
  const buttons = actions.getByRole('button')
  await expect(buttons).toHaveCount(6)
  for (const button of await buttons.all()) {
    const box = await button.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(390)
  }
  await expect.poll(async () => {
    const navigation = await actions.boundingBox()
    const badge = await page.locator('.page-header .brand-badge').boundingBox()
    return !!navigation && !!badge && navigation.y + navigation.height <= badge.y
  }).toBe(true)
})

test('itinerary details fit tablet and phone widths without horizontal clipping', async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem('tripPlan', JSON.stringify({
      city: '北京', start_date: '2026-10-01', end_date: '2026-10-01', overall_suggestions: '行程建议',
      constraints: { must_visit: [], avoid: [], daily_minutes: 600 }, weather_info: [], days: [{
        date: '2026-10-01', day_index: 0, description: '故宫一日游', transportation: '步行',
        accommodation: '经济型酒店', attractions: [{ poi_id: 'B0001', name: '故宫博物院',
          location: { longitude: 116.4, latitude: 39.9 }, visit_duration: 180, photos: [] }], meals: []
      }]
    }))
    sessionStorage.setItem('tripQuality', JSON.stringify({ outcome: 'complete', issues: [] }))
  })
  await page.route('**/api/poi/photo/image**', route => route.fulfill({ status: 404 }))

  for (const width of [737, 390]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/result')
    await expect(page.getByText('北京旅行计划')).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  }
})

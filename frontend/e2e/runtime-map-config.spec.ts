import { expect, test } from '@playwright/test'

test('the public image accepts a browser map key at runtime', async ({ page }) => {
  await page.route('**/runtime-config.js', route => route.fulfill({ contentType: 'application/javascript', body: 'window.__TRIP_CONFIG__ = {amapWebJsKey:"runtime-fixture-key"};' }))
  let key = ''
  await page.route('https://webapi.amap.com/maps*', route => {
    key = new URL(route.request().url()).searchParams.get('key') || ''
    return route.abort()
  })
  await page.route('**/api/poi/photo/image*', route => route.fulfill({ status: 404 }))
  await page.addInitScript(() => {
    sessionStorage.setItem('tripPlan', JSON.stringify({ city: '北京', start_date: '2026-11-10', end_date: '2026-11-10', days: [{ day_index: 0, date: '2026-11-10', attractions: [], meals: [] }] }))
    sessionStorage.setItem('tripQuality', '{}')
  })
  await page.goto('/result')
  await expect.poll(() => key).toBe('runtime-fixture-key')
  await expect(page.locator('#amap-container')).toContainText('地图暂不可用，仍可查看、编辑和导出行程。')
})

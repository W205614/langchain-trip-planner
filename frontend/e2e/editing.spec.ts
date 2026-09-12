import { test, expect } from '@playwright/test'

const attraction = { poi_id: 'P1', name: '原有景点', address: '北京', location: { longitude: 116.4, latitude: 39.9 }, visit_duration: 120, description: '原行程' }
const plan = { city: '北京', start_date: '2026-09-14', end_date: '2026-09-14', overall_suggestions: '测试',
  days: [{ date: '2026-09-14', day_index: 0, description: '第一天', transportation: '步行', accommodation: '无需住宿', attractions: [attraction], meals: [] }] }
const poi = { id: 'P2', name: '新增景点', address: '北京市东城区', location: { longitude: 116.41, latitude: 39.91 }, type: '景点', photos: [], opening_hours: '09:00-17:00' }

test.beforeEach(async ({ page }) => {
  await page.route('**/api/poi/photo/image*', route => route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30"/>' }))
  await page.route('https://webapi.amap.com/**', route => route.fulfill({ contentType: 'application/javascript', body: `
    window.mapSnapshots = [];
    class FakeTripMap { add(items) { if (Array.isArray(items)) window.mapSnapshots.push(items.map(i=>i.options.title)); } clearMap() {} clearInfoWindow() {} setFitView() {} destroy() {} }
    class Marker { constructor(options) { this.options = options; } on() {} }
    window.AMap = { Map: FakeTripMap, Marker, Polyline: class {}, InfoWindow: class { open() {} } };
    window.___onAPILoaded();
  ` }))
  await page.addInitScript(plan => {
    sessionStorage.setItem('tripPlan', JSON.stringify(plan))
    sessionStorage.setItem('tripQuality', JSON.stringify({ policy_version: 'constraints-v2', rules_passed: true }))
  }, plan)
})

test('add, prevent duplicates, redraw order, delete and cancel restore original plan', async ({ page }) => {
  await page.route('**/api/map/poi*', route => {
    expect(new URL(route.request().url()).searchParams.get('city')).toBe('北京')
    return route.fulfill({ json: { success: true, data: [poi] } })
  })
  await page.goto('/result')
  await page.getByRole('button', { name: /编辑行程/ }).click()
  await page.getByRole('button', { name: /添加景点/ }).click()
  await page.getByPlaceholder('输入景点名称').fill('新增')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await page.getByRole('button', { name: /^添\s*加$/ }).click()
  await expect(page.locator('.attraction-card')).toHaveCount(2)
  await expect.poll(() => page.evaluate(() => (window as any).mapSnapshots?.at(-1))).toEqual(['原有景点', '新增景点'])
  await page.getByRole('button', { name: /添加景点/ }).click()
  await page.getByPlaceholder('输入景点名称').fill('新增')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await expect(page.getByRole('button', { name: '已在行程中' })).toBeDisabled()
  await page.getByRole('button', { name: 'Close', exact: true }).click()
  await page.locator('.attraction-card').nth(1).getByRole('button', { name: '↑', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as any).mapSnapshots.at(-1))).toEqual(['新增景点', '原有景点'])
  await page.locator('.attraction-card').first().getByRole('button', { name: '🗑️', exact: true }).click()
  await expect(page.locator('.attraction-card')).toHaveCount(1)
  await page.getByRole('button', { name: /取消编辑/ }).click()
  await expect(page.getByText('已通过当前规则检查', { exact: false })).toBeVisible()
  expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem('tripPlan')!).days[0].attractions.length)).toBe(1)
})

test('search errors are recoverable and failed saves retain local additions with If-Match', async ({ page }) => {
  let searches = 0
  await page.route('**/api/map/poi*', route => ++searches === 1
    ? route.fulfill({ status: 503, json: { detail: 'unavailable' } })
    : route.fulfill({ json: { success: true, data: [poi] } }))
  await page.route('**/api/history/99', route => {
    expect(route.request().method()).toBe('PUT')
    expect(route.request().headers()['if-match']).toBe('3')
    expect(route.request().postDataJSON().days[0].attractions[1].poi_id).toBe('P2')
    return route.fulfill({ status: 409, json: { detail: 'version conflict' } })
  })
  await page.addInitScript(() => { sessionStorage.setItem('tripPlanId', '99'); sessionStorage.setItem('tripPlanVersion', '3') })
  await page.goto('/result')
  await page.getByRole('button', { name: /编辑行程/ }).click()
  await page.getByRole('button', { name: /添加景点/ }).click()
  await page.getByPlaceholder('输入景点名称').fill('新增')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await expect(page.getByText('景点搜索失败，请稍后重试')).toBeVisible()
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await page.getByRole('button', { name: /^添\s*加$/ }).click()
  await page.getByRole('button', { name: /保存修改/ }).click()
  await expect(page.getByText(/其它页面已修改行程/)).toBeVisible()
  expect(await page.evaluate(() => sessionStorage.getItem('tripUnsaved'))).toBe('true')
  expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem('tripPlan')!).days[0].attractions[1].poi_id)).toBe('P2')
})

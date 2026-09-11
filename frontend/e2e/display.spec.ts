import { test, expect } from '@playwright/test'

test('result retains required-name mapping, cost assumptions and precise photo identity', async ({ page }) => {
  const attraction = { poi_id: 'B0KDJ78DYD', name: '上海迪士尼乐园', requested_names: ['上海迪士尼公园'],
    address: '上海市浦东新区', location: { longitude: 121.667, latitude: 31.144 },
    visit_duration: 480, description: '整天游览', opening_hours: '08:30-21:30', ticket_price: 500 }
  const plan = { city: '上海', start_date: '2026-09-14', end_date: '2026-09-17', overall_suggestions: '验证行程',
    days: Array.from({length: 4}, (_, i) => ({day_index: i, date: `2026-09-${14+i}`,
      description: '行程安排', transportation: '公共交通', accommodation: '经济型酒店',
      attractions: i === 0 ? [attraction] : [], meals: []})),
    budget: { total_attractions: 500, total_hotels: 750, total_meals: 640, total_transportation: 120,
      total: 2010, assumptions: ['住宿按3晚、1间房计算；缺少报价时按250元/晚预留。'] } }
  await page.route('**/api/poi/photo/image*', route => {
    const url = new URL(route.request().url())
    expect(url.searchParams.get('poi_id')).toBe('B0KDJ78DYD')
    expect(url.searchParams.get('city')).toBe('上海')
    return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="400" height="300" fill="blue"/></svg>' })
  })
  await page.addInitScript(plan => {
    sessionStorage.setItem('tripPlan', JSON.stringify(plan))
    sessionStorage.setItem('tripQuality', JSON.stringify({ rules_passed: true, policy_version: 'constraints-v1',
      data_gaps: ['opening_hours_travel_date_unverified', 'reservation_unverified'], day_checks: [] }))
  }, plan)
  await page.goto('/result')
  await expect(page.getByText('上海迪士尼公园（已匹配此景点）', { exact: false })).toBeVisible()
  await expect(page.getByText(/08:30-21:30（高德参考/)).toBeVisible()
  await expect(page.getByText('住宿按3晚、1间房计算；缺少报价时按250元/晚预留。')).toBeVisible()
  await expect(page.locator('#budget')).toContainText('¥750')
  await expect(page.locator('#budget')).toContainText('¥120')
  await expect(page.locator('img[alt="上海迪士尼乐园"]')).toBeVisible()
  await expect.poll(() => page.locator('img[alt="上海迪士尼乐园"]').evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

for (const unsaved of [false, true]) {
  test(`legacy saved checks refresh without overwriting unsaved edits: ${unsaved}`, async ({ page }) => {
    const plan = { city: '北京', start_date: '2026-09-14', end_date: '2026-09-14', overall_suggestions: '',
      days: [{ day_index: 0, date: '2026-09-14', description: '北京游览', transportation: '自驾', accommodation: '酒店',
        attractions: [{ poi_id: 'P1', name: '故宫博物院', address: '北京', location: {longitude:116.4,latitude:39.9}, visit_duration:180, description:'游览' }], meals: [] }] }
    const updated = { ...plan, days: [{...plan.days[0], attractions: [{...plan.days[0].attractions[0], requested_names:['北京故宫博物馆']}]}] }
    let reads = 0
    await page.route('**/api/history/99', route => {
      reads++
      return route.fulfill({ json: { success: true, data: { id:99,version:1,plan:updated,quality: {
        policy_version:'constraints-v2',rules_passed:true,warnings:[],day_checks:[{day_index:0,planned_minutes:300,walking_status:'not_applicable',inter_stop_walking_km:null}] } } } })
    })
    await page.route('**/api/poi/photo/image*', route => route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"/>'}))
    await page.addInitScript(({plan,unsaved})=>{
      sessionStorage.setItem('tripPlan',JSON.stringify(plan))
      sessionStorage.setItem('tripPlanId','99')
      sessionStorage.setItem('access_token','browser-fixture')
      sessionStorage.setItem('tripUnsaved',String(unsaved))
      sessionStorage.setItem('tripQuality',JSON.stringify({policy_version:'constraints-v1',rules_passed:false,warnings:['未满足必去景点：北京故宫博物馆']}))
    }, {plan,unsaved})
    await page.goto('/result')
    if (unsaved) {
      await expect(page.getByText('未满足必去景点：北京故宫博物馆',{exact:true})).toBeVisible()
      expect(reads).toBe(0)
    } else {
      await expect(page.getByText(/自驾路线不含停车后步行/)).toBeVisible()
      await expect(page.getByText('未满足必去景点：北京故宫博物馆',{exact:true})).toHaveCount(0)
      expect(reads).toBe(1)
      expect(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('tripQuality')!).policy_version)).toBe('constraints-v2')
    }
  })
}

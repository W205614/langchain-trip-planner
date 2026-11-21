import { expect, test } from '@playwright/test'
import { pathToFileURL } from 'node:url'
import { installOfflineBrowserBoundary } from './offline-provider-boundary'

installOfflineBrowserBoundary()

test('two-day traveler confirms scope, reacts to closure, travels offline and reconciles a booking', async ({ page, context, request }, testInfo) => {
  test.setTimeout(180_000)
  const marker = await request.get('/api/validation/fixture')
  expect(marker.ok(), 'requires isolated fixture services').toBe(true)
  expect((await marker.json()).offline_fixture).toBe(true)
  const registered = await request.post('/api/auth/register', {
    data: { username: 'journey_' + crypto.randomUUID().slice(0, 8), password: 'travel-fixture-only' }
  })
  expect(registered.ok()).toBe(true)
  const token = (await registered.json()).access_token
  const headers = { Authorization: 'Bearer ' + token }
  const api = async (method: string, path: string, data?: unknown, extraHeaders = {}) => {
    const response = await request.fetch(path, { method, data, headers: { ...headers, ...extraHeaders } })
    expect(response.ok(), method + ' ' + path + ': ' + await response.text()).toBe(true)
    return response.json()
  }
  const date = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' })
  const first = date.format(Date.now())
  const second = date.format(Date.now() + 86_400_000)
  let submissions = 0
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/trip/tasks' && request.method() === 'POST') submissions++
  })
  await page.goto('/')
  await page.evaluate(value => sessionStorage.setItem('access_token', value), token)
  await page.reload()

  await test.step('confirm two sights for the whole trip without submitting a task early', async () => {
    await page.getByPlaceholder('例如: 北京').fill('北京')
    const fields = page.getByPlaceholder('选择日期')
    for (const [index, value] of [first, second].entries()) {
      const field = fields.nth(index)
      await field.click()
      const picker = page.locator('.ant-picker-dropdown:not(.ant-picker-dropdown-hidden):visible')
      const target = picker.locator('td[title="' + value + '"]').first()
      await target.click()
      await expect(field).toHaveValue(value)
    }
    await page.getByLabel('整段总预算', { exact: false }).fill('1000')
    await page.getByText('固定预约与已确认跨城交通', { exact: true }).click()
    await page.getByRole('button', { name: '添加固定事项' }).click()
    const fixed = page.getByRole('article', { name: '固定事项 1' })
    await fixed.getByLabel('名称／班次').fill('G123 高铁')
    await fixed.getByLabel('类型').selectOption('transport')
    await fixed.getByLabel('日期').selectOption('1')
    await fixed.getByLabel('开始／发车').fill(second + 'T15:00')
    await fixed.getByLabel('结束／抵达').fill(second + 'T16:00')
    await fixed.getByLabel('出发城市').fill('北京')
    await fixed.getByLabel('到达城市').fill('天津')
    await fixed.getByLabel('出发站／地点').fill('北京南站')
    await fixed.getByLabel('出发经度').fill('116.38')
    await fixed.getByLabel('出发纬度').fill('39.86')
    await fixed.getByLabel('到达经度').fill('117.2')
    await fixed.getByLabel('到达纬度').fill('39.13')
    await page.locator('textarea#free_text_input, textarea[placeholder*="请输入您的额外要求"]').first().fill('只去两个景点')
    await page.getByRole('button', { name: '开始规划我的旅行' }).click()
    const review = page.getByRole('dialog', { name: '确认旅行需求' })
    await expect(review.getByText(/每天还是全程/)).toBeVisible()
    await expect(review.getByText(/G123 高铁/)).toBeVisible()
    await expect(review.getByRole('button', { name: '确认并生成' })).toBeDisabled()
    expect(submissions).toBe(0)
    await review.getByLabel('数量范围').selectOption('trip')
    const submitted = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/trip/tasks' && response.request().method() === 'POST')
    await review.getByRole('button', { name: '确认并生成' }).click()
    const accepted = await submitted
    expect(accepted.status(), await accepted.text()).toBe(202)
    await expect(page).toHaveURL(/\/result$/, { timeout: 75_000 })
    await expect(page.getByTestId('attraction-count-check')).toContainText(/全程指定 2 个景点，实际 2 个/)
    expect(submissions).toBe(1)
  })
  const id = Number(await page.evaluate(() => sessionStorage.getItem('tripPlanId')))
  expect(id).toBeGreaterThan(0)
  const before = (await api('GET', '/api/history/' + id)).data
  const closed = before.plan.days[1].attractions[0]
  expect(closed).toBeTruthy()
  const oldOfflinePath = testInfo.outputPath('行程_改排前_v' + before.version + '.html')
  const newOfflinePath = testInfo.outputPath('行程_改排后_v' + (before.version + 1) + '.html')
  await test.step('download the saved itinerary before the change', async () => {
    await page.getByRole('button', { name: /导出行程/ }).click()
    const downloaded = page.waitForEvent('download')
    await page.getByRole('menuitem').filter({ hasText: '导出离线网页' }).click()
    const file = await downloaded
    expect(file.suggestedFilename()).toBe('旅行计划_北京_v' + before.version + '.html')
    await file.saveAs(oldOfflinePath)
  })
  const planningUrl = '/api/trips/' + id + '/planning-input'
  const inputs = (await api('GET', planningUrl)).data
  const observed = new Date(Date.now() - 60_000).toISOString()
  const expiry = new Date(Date.now() + 3 * 86_400_000).toISOString()
  const train = inputs.planning_input.commitments.find((item: { title: string }) => item.title === 'G123 高铁')
  expect(train).toBeTruthy()
  await api('PUT', planningUrl, { planning_revision: inputs.planning_revision, planning_input: {
    ...inputs.planning_input,
    opening_windows: [{ poi_id: closed.poi_id, date: second, windows: [], closed: true, source: '景区闭馆公告夹具',
      confirmed: true, observed_at: observed, expires_at: expiry }],
    transport_buffers: [{ commitment_id: String(train.id), minutes: 30, source: '高铁提前到站夹具',
      confirmed: true, observed_at: observed, expires_at: expiry }]
  } }, { 'If-Match': String(inputs.trip_version) })

  await test.step('find the closure and preview only the affected activity before confirming', async () => {
    await page.reload()
    const attention = page.getByRole('region', { name: '优先处理事项' })
    const risk = attention.getByRole('listitem').filter({ hasText: '景区闭馆公告夹具' })
    await expect(risk).toContainText(/声明闭馆/)
    await risk.getByRole('button', { name: '补充信息' }).click()
    await expect(page.getByLabel('处理日期')).toHaveValue('1')
    await expect(page.getByRole('combobox', { name: '地点', exact: true })).toHaveValue(closed.poi_id)
    await page.getByRole('button', { name: '打开行程并按日改排' }).click()
    await page.getByRole('region', { name: '优先处理事项' }).getByRole('listitem')
      .filter({ hasText: '景区闭馆公告夹具' }).getByRole('button', { name: '调整这项安排' }).click()
    const dialog = page.getByRole('dialog', { name: '生成受限改排提案' })
    await expect(dialog.getByTestId('revision-scope')).toContainText(closed.name)
    await dialog.getByPlaceholder('例如：下雨，改为室内博物馆并减少步行').fill('景区闭馆，换室内景点，保留高铁。fixture:replace=' + closed.poi_id)
    await dialog.getByRole('button', { name: '生成差异提案' }).click()
    const preview = page.getByTestId('proposal-diff')
    await expect(preview).toBeVisible({ timeout: 75_000 })
    await expect(preview).toContainText('计划接驳')
    await expect(preview).toContainText('15:00–16:00 G123 高铁')
    await expect(preview).toContainText('提前 30 分钟要求')
    const stillOriginal = (await api('GET', '/api/history/' + id)).data
    expect(stillOriginal.plan.days[1].attractions[0].poi_id).toBe(closed.poi_id)
    expect(stillOriginal.version).toBe(before.version)
    await expect(page.getByRole('button', { name: '重新导出离线网页' })).toHaveCount(0)
    await page.screenshot({ path: testInfo.outputPath('proposal.png') })
    await page.getByRole('button', { name: '确认应用到原行程' }).click()
    await expect(page.getByText('已确认并保存助手方案')).toBeVisible()
    const changed = (await api('GET', '/api/history/' + id)).data
    expect(changed.version).toBe(before.version + 1)
    expect(changed.plan.days[0].attractions).toEqual(before.plan.days[0].attractions)
    expect(changed.plan.days[1].attractions[0].poi_id).not.toBe(closed.poi_id)
    const departure = changed.plan.days[1].timeline.find((item: { title: string }) => item.title === 'G123 高铁')?.start_at
    expect(Date.parse(departure)).toBe(Date.parse(second + 'T15:00:00+08:00'))
  })

  await test.step('download again in one click and distinguish both files offline', async () => {
    const current = (await api('GET', '/api/history/' + id)).data
    const replacement = current.plan.days[1].attractions[0]
    await page.clock.setFixedTime(new Date(Date.now() + 120_000))
    const reexport = page.getByRole('button', { name: '重新导出离线网页' })
    await expect(reexport).toBeEnabled()
    await expect(page.getByText(/如果已下载旧版离线网页/)).toBeVisible()
    const downloaded = page.waitForEvent('download')
    await reexport.click()
    const newFile = await downloaded
    expect(newFile.suggestedFilename()).toBe('旅行计划_北京_v' + current.version + '.html')
    await newFile.saveAs(newOfflinePath)
    await expect(reexport).toHaveCount(0)

    await context.setOffline(true)
    const offline = await context.newPage()
    await offline.setViewportSize({ width: 390, height: 844 })
    const timestamps: number[] = []
    for (const [path, version, expectedSight, otherSight] of [
      [oldOfflinePath, before.version, closed.name, replacement.name],
      [newOfflinePath, current.version, replacement.name, closed.name]
    ] as const) {
      await offline.goto(pathToFileURL(path).href)
      const header = offline.locator('header.offline-note')
      await expect(header.getByText('北京离线旅行计划 · 行程版本 ' + version, { exact: true })).toBeInViewport()
      const time = header.locator('time')
      await expect(time).toBeInViewport()
      await expect(header).toContainText('北京时间')
      timestamps.push(Date.parse((await time.getAttribute('datetime'))!))
      await offline.screenshot({ path: testInfo.outputPath('offline-v' + version + '.png') })
      const secondDay = offline.locator('details.offline-day').nth(1)
      await secondDay.locator('summary').first().click()
      await expect(secondDay.getByText(expectedSight, { exact: true }).first()).toBeVisible()
      await expect(secondDay.getByText(otherSight, { exact: true })).toHaveCount(0)
      await expect(secondDay.getByText(/G123 高铁/).first()).toBeVisible()
    }
    expect(timestamps[1]).toBeGreaterThan(timestamps[0])
    await offline.close()
    await context.setOffline(false)
  })

  await test.step('retrieve the next stop on mobile and keep entrance directions offline', async () => {
    const current = (await api('GET', '/api/history/' + id)).data
    const candidate = current.plan.days[1].attractions[0]
    const planning = (await api('GET', planningUrl)).data
    await api('PUT', planningUrl, { planning_revision: planning.planning_revision, planning_input: {
      ...planning.planning_input,
      visit_entrances: [{ day_index: 1, poi_id: candidate.poi_id, name: '东入口', address: '东入口验收地址',
        location: candidate.location, source: '隔离入口夹具', confirmed: true, observed_at: observed, expires_at: expiry }]
    } }, { 'If-Match': String(planning.trip_version) })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.clock.setFixedTime(new Date(second + 'T09:45:00+08:00'))
    await page.reload()
    const today = page.getByRole('region', { name: '今天的行程' })
    await expect(today.getByLabel('查看日期')).toHaveValue('1')
    await expect(today).toContainText(/下一站/)
    await expect(today).toContainText('东入口验收地址')
    const link = new URL((await today.getByRole('link', { name: '用高德打开' }).getAttribute('href'))!)
    expect(link.hostname).toBe('uri.amap.com')
    expect(link.searchParams.get('to')).toBe([candidate.location.longitude, candidate.location.latitude, '东入口'].join(','))
    await expect(today.getByRole('button', { name: '复制地址' })).toBeVisible()
    await expect(today.getByRole('button', { name: '调整今天剩余安排' })).toBeVisible()
    expect(await page.locator('.result-actions button').evaluateAll(buttons => buttons.every(button => {
      const box = button.getBoundingClientRect()
      return box.left >= 0 && box.right <= window.innerWidth
    }))).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('today-mobile.png') })
    await today.getByRole('button', { name: '调整今天剩余安排' }).click()
    await expect(page.getByLabel('改排开始时间')).toHaveValue(second + 'T09:45')
    await expect(page.getByLabel('改排结束时间')).toHaveValue(second + 'T16:00')
    await page.getByRole('button', { name: '返回行程', exact: true }).click()
    await expect(page.getByRole('dialog', { name: '生成受限改排提案' })).toBeHidden()
    await page.getByRole('button', { name: /导出行程/ }).click()
    const downloaded = page.waitForEvent('download')
    await page.getByRole('menuitem').filter({ hasText: '导出离线网页' }).click()
    const path = testInfo.outputPath('two-day-trip.html')
    await (await downloaded).saveAs(path)
    await context.setOffline(true)
    const offline = await context.newPage()
    await offline.goto(pathToFileURL(path).href)
    await expect(offline.getByText(/导出于/)).toBeVisible()
    const secondDay = offline.locator('details.offline-day').nth(1)
    await secondDay.locator('summary').first().click()
    await expect(secondDay.getByText(/东入口验收地址/)).toBeVisible()
    await expect(secondDay.getByText(/G123 高铁/, { exact: false }).first()).toBeVisible()
    await offline.close()
    await context.setOffline(false)
  })

  await test.step('record a partial hotel payment without counting it twice', async () => {
    const created = await api('POST', '/api/trips/' + id + '/commitments', {
      title: '测试酒店', amount: 400, day_index: 1, category: 'lodging', status: 'confirmed'
    }, { 'Idempotency-Key': crypto.randomUUID() })
    await page.goto('/trips/' + id + '/operations')
    const expensesTab = page.getByRole('tab', { name: '预订与费用' })
    await expensesTab.click()
    await expect(expensesTab).toHaveAttribute('aria-selected', 'true')
    await page.getByPlaceholder('实际金额').fill('150')
    const budget = page.getByTestId('remaining-budget')
    await expect(budget).toContainText('600')
    const row = page.getByRole('tabpanel', { name: '预订与费用' })
    await row.getByRole('combobox', { name: '关联预订' }).selectOption({ label: '测试酒店' })
    await page.getByRole('button', { name: '记一笔' }).click()
    await expect(row.getByText('250.00', { exact: false })).toBeVisible()
    await expect(budget).toContainText('600')
    const result = await api('GET', '/api/trips/' + id + '/expenses')
    expect(result.data[0].commitment_id).toBe(created.id)
    expect(result.summary).toMatchObject({ actual_cents: 15000, pending_cents: 25000, remaining_budget_cents: 60000 })
    await budget.scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath('budget.png') })
  })
})

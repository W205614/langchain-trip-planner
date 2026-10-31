import { expect, test } from '@playwright/test'

// Real accounts, requests and PostgreSQL; no browser API interception. External
// model/map calls are replaced only by the isolated validation Agent providers.
test('real backend roles enforce planning rights and remove revoked itinerary from an open page', async ({ browser, request }, testInfo) => {
  test.setTimeout(180_000)
  const marker = await request.get('/api/validation/fixture')
  expect(marker.ok(), 'run only against the isolated fixture stack').toBe(true)
  expect((await marker.json()).offline_fixture).toBe(true)
  const prefix = `ui_${Date.now().toString(36)}`
  const users: Record<string, { token: string; name: string; id?: number }> = {}
  for (const role of ['owner', 'editor', 'viewer', 'pending', 'revoked']) {
    const name = `${prefix}_${role}`
    const result = await request.post('/api/auth/register', { data: { username: name, password: 'isolated-browser123' } })
    expect(result.ok(), `registration HTTP ${result.status()}`).toBe(true)
    const body = await result.json(); users[role] = { token: body.access_token, name }
  }
  const headers = (role: string) => ({ Authorization: `Bearer ${users[role].token}` })
  const submitted = await request.post('/api/trip/tasks', { headers: { ...headers('owner'), 'Idempotency-Key': crypto.randomUUID() }, data: { city: '北京', start_date: '2026-11-01', end_date: '2026-11-02', travel_days: 2, transportation: '公共交通', accommodation: '酒店', preferences: [], planning_input: { timezone: 'Asia/Shanghai', day_start: '09:00', day_end: '20:00' } } })
  expect(submitted.status()).toBe(202)
  const taskId = (await submitted.json()).data.id
  let tripId = 0
  let terminalTask: any
  await expect.poll(async () => { const r = await request.get(`/api/trip/tasks/${taskId}`, { headers: headers('owner') }); terminalTask = (await r.json()).data; tripId = Number(terminalTask.result?.id || 0); return terminalTask.status }, { timeout: 90_000 }).toMatch(/^(succeeded|needs_attention|failed|cancelled)$/)
  expect(terminalTask.status, `${terminalTask.error_code || ''}: ${terminalTask.message || ''}`).toMatch(/^(succeeded|needs_attention)$/)
  expect(tripId).toBeGreaterThan(0)
  for (const role of ['editor', 'viewer', 'pending', 'revoked']) {
    const invited = await request.post(`/api/trips/${tripId}/members`, { headers: headers('owner'), data: { username: users[role].name, role: role === 'editor' ? 'editor' : 'viewer' } })
    expect(invited.ok()).toBe(true); users[role].id = (await invited.json()).user_id
    if (role !== 'pending') expect((await request.post(`/api/trips/${tripId}/members/accept`, { headers: headers(role) })).ok()).toBe(true)
  }
  const pages: Record<string, Awaited<ReturnType<typeof browser.newPage>>> = {}
  try {
    for (const role of Object.keys(users)) {
      const page = await browser.newPage({ baseURL: testInfo.project.use.baseURL }); pages[role] = page
      await page.route('https://webapi.amap.com/**', route => route.abort())
      await page.addInitScript(({ token, name }) => { sessionStorage.setItem('access_token', token); sessionStorage.setItem('username', name) }, users[role])
      await page.goto(`/trips/${tripId}/operations`)
      if (role === 'pending') { await expect(page.getByRole('heading', { name: /北京.*执行工作台/ })).toHaveCount(0); await expect(page.getByRole('tab', { name: '行前核验' })).toHaveCount(0); continue }
      await expect(page.getByRole('heading', { name: /北京.*执行工作台/ })).toBeVisible()
      if (role !== 'owner') {
        await expect(page.getByRole('tab', { name: '时间与约束' })).toHaveCount(0)
        await expect(page.getByRole('button', { name: '核验当天信息' })).toHaveCount(0)
      }
      if (role === 'editor') {
        await page.getByRole('tab', { name: '同行协作' }).click()
        await expect(page.getByRole('button', { name: '保存顺序' })).toBeVisible()
        await page.getByRole('tab', { name: '行前核验' }).click()
      }
      if (role === 'viewer') {
        await page.getByRole('tab', { name: '预订与费用' }).click()
        await expect(page.getByRole('button', { name: '添加待确认事项' })).toHaveCount(0)
        await page.getByRole('tab', { name: '行前核验' }).click()
      }
      await page.getByRole('button', { name: role === 'owner' ? '打开行程并按日改排' : '查看完整行程' }).click()
      await expect(page.getByText('北京旅行计划')).toBeVisible()
      if (role === 'owner') {
        // Query the actual generated record; never synthesize or overwrite a solver binding.
        const currentResponse = await request.get(`/api/trips/${tripId}/planning-input`, { headers: headers('owner') })
        expect(currentResponse.ok()).toBe(true)
        const currentData = (await currentResponse.json()).data
        expect(currentData.planning_input.day_start).toBe('09:00')
        expect(currentData.plan.days).toHaveLength(2)
        for (const day of currentData.plan.days) {
          expect(day.schedule_stale).toBe(false)
          expect(day.schedule_dependencies.input_hash).toMatch(/^[a-f0-9]{64}$/)
          expect(day.schedule_dependencies.fact_hash).toMatch(/^[a-f0-9]{64}$/)
          expect(Date.parse(day.schedule_dependencies.expires_at)).toBeGreaterThan(Date.now())
        }
        const activityTestId = await page.locator('[data-testid^="activity-"]').filter({ has: page.getByRole('button', { name: '锁定时段', exact: true }) }).first().getAttribute('data-testid')
        expect(activityTestId).toBeTruthy()
        const activity = page.getByTestId(activityTestId!)
        await activity.getByRole('button', { name: '锁定时段', exact: true }).click()
        await expect(activity.getByText('已锁定', { exact: true })).toBeVisible()
        await activity.getByRole('button', { name: '标记完成', exact: true }).click()
        await expect(activity.getByText('已完成', { exact: true })).toBeVisible()
        const completedActivityId = activityTestId!.slice('activity-'.length)
        // Re-enter the actual workspace to obtain revisions after both successful state writes.
        const reloadPlanning = page.waitForResponse(response => new URL(response.url()).pathname === `/api/trips/${tripId}/planning-input` && response.request().method() === 'GET')
        await page.goto(`/trips/${tripId}/operations`)
        const reloadedResponse = await reloadPlanning
        expect(reloadedResponse.ok()).toBe(true)
        const reloadedData = (await reloadedResponse.json()).data
        expect(reloadedData.planning_revision).toBeGreaterThan(currentData.planning_revision)
        expect(reloadedData.planning_input.completed_activity_ids).toContain(completedActivityId)
        expect(reloadedData.planning_input.locks.some((lock: any) => lock.activity_id === completedActivityId && lock.mode === 'time')).toBe(true)
        await page.getByRole('tab', { name: '时间与约束' }).click()
        await page.getByLabel('每日最早出发', { exact: true }).fill('08:30')
        const savePlanning = page.waitForResponse(response => new URL(response.url()).pathname === `/api/trips/${tripId}/planning-input` && response.request().method() === 'PUT')
        await page.getByTestId('planning-save').click()
        const savedResponse = await savePlanning
        expect(savedResponse.ok()).toBe(true)
        const savedData = (await savedResponse.json()).data
        expect(savedData.planning_input.day_start).toBe('08:30')
        expect(savedData.planning_revision).toBe(reloadedData.planning_revision + 1)
        expect(savedData.plan.days).toHaveLength(2)
        for (const day of savedData.plan.days) {
          expect(day.schedule_stale).toBe(true)
          expect(day.feasibility.status).toBe('unverified')
        }
        await expect(page.getByText('规划输入已保存；当前行程未自动改排')).toBeVisible()
        await page.getByRole('tab', { name: '行前核验' }).click()
        await page.getByRole('button', { name: '打开行程并按日改排' }).click()
        await expect(page.getByText('北京旅行计划')).toBeVisible()
        await expect(page.locator('.timeline').first()).toContainText('时间轴待重新核验')
        const staleCompletedActivity = page.getByTestId(activityTestId!)
        await expect(staleCompletedActivity.getByText('已完成', { exact: true })).toBeVisible()
        await expect(staleCompletedActivity.getByText('已锁定', { exact: true })).toBeVisible()
        await expect(staleCompletedActivity.getByRole('button', { name: '解除锁定', exact: true })).toBeDisabled()
        await expect(staleCompletedActivity.getByRole('button', { name: '撤销完成', exact: true })).toBeEnabled()
        const staleActivity = page.locator('[data-testid^="activity-"]').filter({ has: page.getByRole('button', { name: '锁定时段', exact: true }) }).first()
        const staleActivityTestId = await staleActivity.getAttribute('data-testid')
        expect(staleActivityTestId).toBeTruthy()
        const staleActivityId = staleActivityTestId!.slice('activity-'.length)
        expect(staleActivityId).not.toBe(completedActivityId)
        await expect(staleActivity.getByRole('button', { name: '锁定时段', exact: true })).toBeDisabled()
        await expect(staleActivity.getByRole('button', { name: '标记完成', exact: true })).toBeDisabled()
        for (const operation of [{ locked: true, lock_mode: 'time' }, { completed: true }]) {
          // Read actual latest versions for every attempt; a VERSION_CONFLICT cannot satisfy this check.
          const latestResponse = await request.get(`/api/trips/${tripId}/planning-input`, { headers: headers('owner') })
          expect(latestResponse.ok()).toBe(true)
          const latestData = (await latestResponse.json()).data
          const denied = await request.post(`/api/trips/${tripId}/activities/${encodeURIComponent(staleActivityId)}/state`, {
            headers: { ...headers('owner'), 'If-Match': String(latestData.trip_version) },
            data: { ...operation, planning_revision: latestData.planning_revision },
          })
          expect(denied.status()).toBe(409)
          expect(await denied.json()).toMatchObject({ success: false, error_code: 'SCHEDULE_STALE', code: 'SCHEDULE_STALE' })
          const afterResponse = await request.get(`/api/trips/${tripId}/planning-input`, { headers: headers('owner') })
          expect(afterResponse.ok()).toBe(true)
          const afterData = (await afterResponse.json()).data
          expect(afterData.planning_revision).toBe(latestData.planning_revision)
          expect(afterData.trip_version).toBe(latestData.trip_version)
          expect(afterData.planning_input.completed_activity_ids).toEqual(latestData.planning_input.completed_activity_ids)
          expect(afterData.planning_input.locks).toEqual(latestData.planning_input.locks)
        }
        // Undoing completion is allowed even when an old timeline can no longer authorize new completion.
        const undoCompletion = page.waitForResponse(response => new URL(response.url()).pathname === `/api/trips/${tripId}/activities/${encodeURIComponent(completedActivityId)}/state` && response.request().method() === 'POST')
        await staleCompletedActivity.getByRole('button', { name: '撤销完成', exact: true }).click()
        const undoResponse = await undoCompletion
        expect(undoResponse.ok()).toBe(true)
        const undoData = (await undoResponse.json()).data
        expect(undoData.planning_revision).toBeGreaterThan(savedData.planning_revision)
        expect(undoData.planning_input.completed_activity_ids).not.toContain(completedActivityId)
        expect(undoData.planning_input.locks.some((lock: any) => lock.activity_id === completedActivityId && lock.mode === 'time')).toBe(true)
        for (const day of undoData.plan.days) {
          expect(day.schedule_stale).toBe(true)
          expect(day.feasibility.status).toBe('unverified')
        }
        await expect(staleCompletedActivity.getByText('已完成', { exact: true })).toHaveCount(0)
        await expect(staleCompletedActivity.getByText('已锁定', { exact: true })).toBeVisible()
      } else await expect(page.getByRole('button', { name: /锁定安排|锁定时段|标记完成|AI 重新安排|编辑行程/ })).toHaveCount(0)
    }
    const revoked = pages.revoked
    await revoked.evaluate(({ id }) => { sessionStorage.setItem('tripPlanId', String(id)); sessionStorage.setItem('tripPlan', JSON.stringify({ city: '不应保留的缓存', days: [] })) }, { id: tripId })
    expect((await request.delete(`/api/trips/${tripId}/members/${users.revoked.id}`, { headers: headers('owner') })).ok()).toBe(true)
    await revoked.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect(revoked.getByText('北京旅行计划')).toHaveCount(0)
    await expect.poll(() => revoked.evaluate(() => sessionStorage.getItem('tripPlan'))).toBeNull()
    await revoked.reload()
    await expect(revoked.locator('.result-empty')).toBeVisible()
    await expect(revoked.getByText('北京旅行计划')).toHaveCount(0)
    await pages.owner.locator('.timeline').first().evaluate(element => element.scrollIntoView({ block: 'start' }))
    const ownerScreenshot = testInfo.outputPath('owner-time-axis.png'), revokedScreenshot = testInfo.outputPath('revoked-access.png')
    await pages.owner.screenshot({ path: ownerScreenshot })
    await revoked.screenshot({ path: revokedScreenshot })
    await testInfo.attach('owner-time-axis', { path: ownerScreenshot, contentType: 'image/png' })
    await testInfo.attach('revoked-access', { path: revokedScreenshot, contentType: 'image/png' })
  } finally { for (const page of Object.values(pages)) await page.close() }
})

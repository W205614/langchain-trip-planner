import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'

// Real browser login, HTTP, Java transactions and PostgreSQL. The validation
// Agent uses offline providers; this is not evidence of live generation quality.
// Do not intercept or synthesize any business API response in this test.
test('real browser reviews proposals and restores ordinary drafts without mixing their actions', async ({ page, request }, testInfo) => {
  test.setTimeout(300_000)
  const marker = await request.get('/api/validation/fixture')
  expect(marker.ok(), 'mutations require the isolated offline fixture stack').toBe(true)
  expect((await marker.json()).offline_fixture).toBe(true)
  page.setDefaultTimeout(15_000)
  await page.route('https://*.amap.com/**', route => route.abort())

  const username = `imp_${Date.now().toString(36)}_${crypto.randomUUID().slice(0, 6)}`
  const password = 'isolated-improvement-123'
  await test.step('register, log out and log in through the actual UI', async () => {
    await page.goto('/login')
    await page.getByRole('button', { name: '去注册', exact: true }).click()
    await page.getByPlaceholder('用户名', { exact: true }).fill(username)
    await page.getByPlaceholder('密码', { exact: true }).fill(password)
    await page.getByPlaceholder('确认密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '注册并登录', exact: true }).click()
    await expect(page.getByRole('button', { name: /^退\s*出$/ })).toBeVisible()
    await page.getByRole('button', { name: /^退\s*出$/ }).click()
    await expect(page.getByRole('button', { name: /^退\s*出$/ })).toHaveCount(0)
    await expect.poll(() => page.evaluate(() => Boolean(sessionStorage.getItem('access_token')))).toBe(false)
    await page.goto('/login')
    await page.getByPlaceholder('用户名', { exact: true }).fill(username)
    await page.getByPlaceholder('密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '登 录', exact: true }).click()
    await expect(page.getByRole('button', { name: /^退\s*出$/ })).toBeVisible()
  })

  expect(await page.evaluate(() => sessionStorage.getItem('access_token')), 'Cookie login must not expose a JWT in Storage').toBeNull()
  const session = (await page.context().cookies()).find(cookie => cookie.name === 'trip_session')
  expect(session?.httpOnly, 'UI login must issue an actual HttpOnly browser session').toBe(true)
  expect(await page.evaluate(() => document.cookie.includes('trip_session='))).toBe(false)
  async function api(method: string, path: string, data?: unknown, status = 200, extraHeaders: Record<string, string> = {}) {
    const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'trip_csrf')?.value || ''
    const response = await page.request.fetch(path, { method, data, headers: { 'X-CSRF-Token': csrf, ...extraHeaders } })
    expect(response.status(), `${method} ${path}`).toBe(status)
    return response.json()
  }
  async function task(id: string) {
    let state: any
    await expect.poll(async () => {
      state = (await api('GET', `/api/trip/tasks/${id}`)).data
      return state.status
    }, { timeout: 75_000, intervals: [300, 600, 1000] }).toMatch(/^(succeeded|needs_attention|failed|cancelled)$/)
    expect(state.status, `${state.error_code || ''}: ${state.message || ''}`).toMatch(/^(succeeded|needs_attention)$/)
    expect(Number(state.result?.id)).toBeGreaterThan(0)
    return state
  }
  async function allTasks() {
    await page.goto('/history?tab=tasks')
    await expect(page.getByText('当前显示需要操作的任务', { exact: true })).toBeVisible()
    await page.getByText('全部任务', { exact: true }).click()
    await expect(page.getByText('当前显示全部任务，包括已保存结果与未完成草稿', { exact: true })).toBeVisible()
  }

  let tripId = 0
  let submittedTaskId = ''
  await test.step('enter a two-day request and reconnect after refreshing while it runs', async () => {
    await page.getByPlaceholder('例如: 北京', { exact: true }).fill('北京')
    const dates = page.getByPlaceholder('选择日期', { exact: true })
    await dates.nth(0).click()
    await page.locator('.ant-picker-dropdown:visible').getByTitle('2026-11-04', { exact: true }).click()
    await dates.nth(1).click()
    await page.locator('.ant-picker-dropdown:visible').getByTitle('2026-11-05', { exact: true }).click()
    await page.getByPlaceholder('请输入您的额外要求,例如:想去看升旗、需要无障碍设施、对海鲜过敏等...').fill('北京两日游，每天保留休息时间，未知的营业、餐饮和路线条件请明确提示。')
    const submitted = page.waitForResponse(response => new URL(response.url()).pathname === '/api/trip/tasks' && response.request().method() === 'POST')
    await page.getByRole('button', { name: '开始规划我的旅行' }).click()
    const response = await submitted
    expect(response.status()).toBe(202)
    submittedTaskId = (await response.json()).data.id
    await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('pendingTripTask') || 'null')?.id)).toBe(submittedTaskId)
    await page.reload()
    await expect(page).toHaveURL(/\/result(?:\?|$)/, { timeout: 90_000 })
    tripId = Number(await page.evaluate(() => sessionStorage.getItem('tripPlanId')))
    expect(tripId).toBeGreaterThan(0)
    const record = (await api('GET', `/api/history/${tripId}`)).data
    expect(record.plan.days).toHaveLength(2)
    expect((await task(submittedTaskId)).result.id).toBe(tripId)
    await expect(page.locator('.timeline').first()).toBeVisible()
    await expect(page.locator('.timeline').first()).toContainText('部分条件待核实')
    await expect(page.getByText('开放与预约等事实仍需核实', { exact: false })).toBeVisible()
  })

  let before: any
  let proposal: any
  let proposalTaskId = ''
  let conversation = ''
  await test.step('lock an activity and create a real scoped assistant proposal', async () => {
    const activityId = await page.locator('[data-testid^="activity-"]').filter({ has: page.getByRole('button', { name: '锁定时段', exact: true }) }).first().getAttribute('data-testid')
    expect(activityId).toBeTruthy()
    const activity = page.getByTestId(activityId!)
    await activity.getByRole('button', { name: '锁定时段', exact: true }).click()
    await expect(activity.getByText('已锁定', { exact: true })).toBeVisible()
    before = (await api('GET', `/api/history/${tripId}`)).data
    const planning = (await api('GET', `/api/trips/${tripId}/planning-input`)).data
    conversation = (await api('POST', '/api/assistant/conversations', { active_trip_id: tripId })).id
    const accepted = await api('POST', `/api/assistant/conversations/${conversation}/messages`, {
      content: '只调整第2天，景点之间多留一些时间，第1天和锁定的安排保持不变。',
      mode: 'revise', record_id: tripId, version: before.version, day_index: 1,
      planning_revision: planning.planning_revision, revision_scope: { day_indices: [1] },
    }, 202, { 'Idempotency-Key': crypto.randomUUID() })
    proposalTaskId = accepted.task_id
    const state = await task(proposalTaskId)
    expect(state.status).toBe('needs_attention')
    proposal = (await api('GET', `/api/history/${state.result.id}`)).data
    expect(proposal.quality.assistant_proposal_status).toBe('pending')
    expect(proposal.quality.validated_outcome).toMatch(/^(complete|degraded)$/)
    expect(proposal.plan.days[0]).toEqual(before.plan.days[0])
    const original = (await api('GET', `/api/history/${tripId}`)).data
    expect(original.version).toBe(before.version)
    expect(original.plan).toEqual(before.plan)
  })

  await test.step('all tasks offers proposal review while direct generic recovery is rejected', async () => {
    await allTasks()
    const row = page.getByTestId(`task-${proposalTaskId}`)
    await expect(row.getByRole('button', { name: '查看助手方案', exact: true })).toBeVisible()
    await expect(row.getByRole('button', { name: /恢复有效阶段|重新提交/ })).toHaveCount(0)
    for (const path of [`/api/tasks/${proposalTaskId}/resume`, `/api/trip/tasks/${proposalTaskId}/retry`]) {
      const rejected = await api('POST', path, {}, 409, { 'Idempotency-Key': crypto.randomUUID() })
      expect(rejected.code).toBe('ASSISTANT_PROPOSAL_REVIEW_REQUIRED')
    }
    await row.getByRole('button', { name: '查看助手方案', exact: true }).click()
    await expect(page.getByTestId('proposal-diff')).toBeVisible()
    await expect(page.getByText(`待确认的助手改排方案；原行程 #${tripId} 尚未更新。`, { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '确认应用到原行程', exact: true }).click()
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('tripPlanId'))).toBe(String(tripId))
    await expect(page.getByRole('button', { name: '确认应用到原行程', exact: true })).toHaveCount(0)
    const confirmed = (await api('GET', `/api/history/${tripId}`)).data
    expect(confirmed.version).toBe(before.version + 1)
    expect(confirmed.plan).toEqual(proposal.plan)
  })

  await test.step('download and open the real offline export with activity names, times and caveats', async () => {
    const record = (await api('GET', `/api/history/${tripId}`)).data
    const named = record.plan.days[0].timeline.find((item: any) => item.kind === 'attraction')
    expect(named).toBeTruthy()
    const time = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(named.start_at))
    const downloading = page.waitForEvent('download')
    await page.getByRole('button', { name: /导出行程/ }).click()
    await page.getByRole('menuitem', { name: /导出离线网页/ }).click()
    const download = await downloading
    expect(download.suggestedFilename()).toMatch(/\.html$/)
    const output = testInfo.outputPath('itinerary-offline.html')
    await download.saveAs(output)
    const html = await readFile(output, 'utf8')
    expect(html).toContain(named.title)
    expect(html).toContain(time)
    expect(html).toContain('估算和未核实安排不等于已确认营业或完成预订')
    const offline = await page.context().newPage()
    try {
      await offline.goto(pathToFileURL(output).href)
      await expect(offline.locator('.offline-day')).toHaveCount(2)
      await expect(offline.locator('.timeline').first()).toContainText(named.title)
      await expect(offline.locator('.timeline').first()).toContainText(time)
      await expect(offline.getByText('估算和未核实安排不等于已确认营业或完成预订。点击有位置的活动可在地图定位。').first()).toBeVisible()
    } finally { await offline.close() }
    await testInfo.attach('offline-html', { path: output, contentType: 'text/html' })
  })

  let inputConflictTaskId = ''
  await test.step('a real planning revision change disables confirmation with its own reason and permits discard', async () => {
    const original = (await api('GET', `/api/history/${tripId}`)).data
    const planning = (await api('GET', `/api/trips/${tripId}/planning-input`)).data
    const accepted = await api('POST', `/api/assistant/conversations/${conversation}/messages`, {
      content: '只调整第2天的游览节奏，第1天与锁定安排保持不变，先给我查看提案。',
      mode: 'revise', record_id: tripId, version: original.version, day_index: 1,
      planning_revision: planning.planning_revision, revision_scope: { day_indices: [1] },
    }, 202, { 'Idempotency-Key': crypto.randomUUID() })
    inputConflictTaskId = accepted.task_id
    const state = await task(inputConflictTaskId)
    expect(state.status).toBe('needs_attention')
    const proposalId = Number(state.result.id)
    const pending = (await api('GET', `/api/history/${proposalId}`)).data
    expect(pending.quality.assistant_proposal_status).toBe('pending')
    const proposalPath = `/api/assistant/conversations/${conversation}/proposals/${proposalId}`
    expect((await api('GET', proposalPath)).data.stale).toBe(false)

    const changedInput = structuredClone(planning.planning_input)
    changedInput.day_end = changedInput.day_end === '19:30' ? '20:00' : '19:30'
    const changed = (await api('PUT', `/api/trips/${tripId}/planning-input`, {
      planning_input: changedInput, planning_revision: planning.planning_revision,
    }, 200, { 'If-Match': String(planning.trip_version) })).data
    expect(changed.trip_version).toBe(original.version)
    expect(changed.planning_revision).toBe(planning.planning_revision + 1)
    expect(changed.planning_input.day_end).toBe(changedInput.day_end)
    // The intentional input edit may mark schedules stale. This persisted
    // post-edit state, not the earlier plan, is the rollback comparison.
    const afterInputChange = (await api('GET', `/api/history/${tripId}`)).data
    expect(afterInputChange.version).toBe(original.version)
    const stale = (await api('GET', proposalPath)).data
    expect(stale.stale).toBe(true)
    expect(stale.invalidation_reason.split(',')).toEqual(['PLANNING_INPUT_CONFLICT'])

    await allTasks()
    await page.getByTestId(`task-${inputConflictTaskId}`).getByRole('button', { name: '查看助手方案', exact: true }).click()
    const explanation = page.getByTestId('proposal-stale')
    await expect(explanation).toContainText('规划约束或预订事项已变化，请重新加载原行程并生成提案。')
    await expect(explanation).not.toContainText('版本已变化')
    await expect(explanation).not.toContainText('事实已过期')
    await expect(page.getByRole('button', { name: '确认应用到原行程', exact: true })).toBeDisabled()
    const rejected = await api('POST', `${proposalPath}/confirm`, {}, 409, { 'If-Match': String(pending.version) })
    expect(rejected.code).toBe('PLANNING_INPUT_CONFLICT')
    const stillPending = (await api('GET', `/api/history/${proposalId}`)).data
    expect(stillPending.quality.assistant_proposal_status).toBe('pending')
    expect(stillPending.version).toBe(pending.version)
    expect(stillPending.plan).toEqual(pending.plan)
    const afterRejection = (await api('GET', `/api/history/${tripId}`)).data
    expect(afterRejection.version).toBe(afterInputChange.version)
    expect(afterRejection.plan).toEqual(afterInputChange.plan)
    expect(afterRejection.quality).toEqual(afterInputChange.quality)
    expect((await api('GET', `/api/trips/${tripId}/planning-input`)).data.planning_revision).toBe(changed.planning_revision)

    const discarding = page.waitForResponse(response => new URL(response.url()).pathname === `${proposalPath}/discard` && response.request().method() === 'POST')
    await page.getByRole('button', { name: '放弃方案', exact: true }).click()
    const discarded = await discarding
    expect(discarded.status()).toBe(200)
    const discardResult = await discarded.json()
    expect(discardResult.quality.assistant_proposal_status).toBe('discarded')
    expect(discardResult.message).toBeTruthy()
    await expect(page).toHaveURL(/\/history$/)
    await expect(page.getByText(discardResult.message, { exact: true })).toBeVisible()
    const afterDiscard = (await api('GET', `/api/history/${tripId}`)).data
    expect(afterDiscard.version).toBe(afterInputChange.version)
    expect(afterDiscard.plan).toEqual(afterInputChange.plan)
    expect(afterDiscard.quality).toEqual(afterInputChange.quality)
    const discardedRecord = (await api('GET', `/api/history/${proposalId}`)).data
    expect(discardedRecord.quality.assistant_proposal_status).toBe('discarded')
    expect(discardedRecord.version).toBe(pending.version + 1)
  })

  let draftTaskId = ''
  let resumedId = ''
  await test.step('a genuine infeasible ordinary draft remains recoverable as a new linked execution', async () => {
    const submitted = await api('POST', '/api/trip/tasks', {
      city: '北京', start_date: '2026-11-06', end_date: '2026-11-06', travel_days: 1,
      transportation: '公共交通', accommodation: '经济型酒店', preferences: [],
      constraints: { must_visit: ['验证景点0'] },
      planning_input: { timezone: 'Asia/Shanghai', day_start: '09:00', day_end: '09:01' },
      free_text_input: '此离线边界用例刻意要求在一分钟内完成必去景点，不应被当作完整可执行方案。',
    }, 202, { 'Idempotency-Key': crypto.randomUUID() })
    draftTaskId = submitted.data.id
    const draft = await task(draftTaskId)
    expect(draft.status).toBe('needs_attention')
    expect(draft.result.quality.outcome).toBe('draft')
    expect(draft.result.quality.assistant_proposal_status).toBeFalsy()
    await allTasks()
    const row = page.getByTestId(`task-${draftTaskId}`)
    await expect(row.getByRole('button', { name: '恢复有效阶段', exact: true })).toBeVisible()
    await expect(row.getByRole('button', { name: '查看结果', exact: true })).toBeVisible()
    const accepted = page.waitForResponse(response => new URL(response.url()).pathname === `/api/tasks/${draftTaskId}/resume` && response.request().method() === 'POST')
    await row.getByRole('button', { name: '恢复有效阶段', exact: true }).click()
    const response = await accepted
    expect(response.status()).toBe(200)
    resumedId = (await response.json()).data.id
    expect(resumedId).not.toBe(draftTaskId)
    const resumed = await task(resumedId)
    expect(resumed.resumed_from).toBe(draftTaskId)
    // The constraints remain impossible: recovery must not turn a failed
    // requirement into a passing itinerary or rewrite the original execution.
    expect(resumed.status).toBe('needs_attention')
    expect(resumed.result.quality.outcome).toBe('draft')
    expect((await api('GET', `/api/trip/tasks/${draftTaskId}`)).data.status).toBe('needs_attention')
  })

  await testInfo.attach('improvement-http-summary', { contentType: 'application/json', body: JSON.stringify({
    mode: 'real_browser_real_http_offline_providers', passed: true, provider_calls: 0,
    checks: ['UI registration/logout/login', 'two-day input and reload reconnect', 'timeline caveats and lock', 'scoped pending proposal preserves original', 'proposal-specific task action and API recovery denial', 'exact preview confirmed', 'downloaded HTML opened and checked', 'planning revision conflict explained and rejected without changing either record; UI discard preserves original', 'ordinary draft linked recovery remains a draft'],
    task_ids: { submitted: submittedTaskId, proposal: proposalTaskId, planning_conflict: inputConflictTaskId, draft: draftTaskId, resumed: resumedId },
    boundary: 'No business API mocks; only the external AMap SDK is blocked. Model and map providers are isolated fixtures. No live quality or external-user feedback claim.',
  }, null, 2) })
})

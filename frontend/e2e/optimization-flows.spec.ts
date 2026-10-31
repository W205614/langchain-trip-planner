import { expect, test, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test'
import { createHmac, randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import { guardOfflineResources, installOfflineBrowserBoundary } from './offline-provider-boundary'
installOfflineBrowserBoundary()

// Real browser + Java HTTP + PostgreSQL + Agent pipeline. Providers are replaced
// only inside the explicitly isolated validation deployment. Browser requests to
// external resources are blocked; business responses are never intercepted or
// fulfilled, and no Storage-token injection or fabricated business records occur.
// Required for the admin part of flow 3: OPTIMIZATION_ADMIN_USERNAME,
// OPTIMIZATION_ADMIN_PASSWORD and OPTIMIZATION_ADMIN_TOTP_SECRET when enrolled.
const password = 'Isolated!Browser_2026_57'
const stamp = () => `${Date.now().toString(36)}_${randomUUID().slice(0, 5)}`

async function isolated(request: APIRequestContext) {
  const response = await request.get('/api/validation/fixture')
  expect(response.status(), 'Refuse mutations outside the isolated provider-fixture deployment').toBe(200)
  expect((await response.json()).offline_fixture).toBe(true)
}

async function register(request: APIRequestContext, role: string) {
  const username = `opt_${stamp()}_${role}`
  const response = await request.post('/api/auth/register', { data: { username, password } })
  expect(response.status(), `register isolated ${role}`).toBe(200)
  return username
}

function totp(secret: string) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const char of secret.toUpperCase().replace(/=+$/, '')) {
    const position = alphabet.indexOf(char)
    expect(position, 'Admin TOTP secret must be valid base32').toBeGreaterThanOrEqual(0)
    bits += position.toString(2).padStart(5, '0')
  }
  const bytes: number[] = []
  for (let offset = 0; offset + 8 <= bits.length; offset += 8) bytes.push(parseInt(bits.slice(offset, offset + 8), 2))
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30_000)))
  const digest = createHmac('sha1', Buffer.from(bytes)).update(counter).digest()
  const offset = digest[digest.length - 1] & 15
  return ((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).toString().padStart(6, '0')
}

async function login(page: Page, username: string, credential = password, secret?: string) {
  await page.goto('/login')
  await page.getByPlaceholder('用户名', { exact: true }).fill(username)
  await page.getByPlaceholder('密码', { exact: true }).fill(credential)
  if (secret) {
    // Enrollment and prior role flows may have consumed this step. Use a new
    // real time step so the browser experience also respects replay protection.
    await page.waitForTimeout(30_100 - Date.now() % 30_000)
    await page.getByPlaceholder('验证器中的6位数字', { exact: true }).fill(totp(secret))
  }
  const loggedIn = page.waitForResponse(response => new URL(response.url()).pathname === '/api/auth/login' && response.request().method() === 'POST')
  await page.getByRole('button', { name: /登\s*录/, exact: true }).click()
  expect((await loggedIn).status(), `${username} UI login`).toBe(200)
  await expect(page).toHaveURL(/\/$/)
  const session = (await page.context().cookies()).find(cookie => cookie.name === 'trip_session')
  expect(session?.httpOnly, 'Actual browser login must issue an HttpOnly session').toBe(true)
  expect(await page.evaluate(() => sessionStorage.getItem('access_token'))).toBeNull()
}

async function api(page: Page, method: string, path: string, data?: unknown, status = 200, extra: Record<string, string> = {}) {
  const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'trip_csrf')?.value || ''
  const response = await page.request.fetch(path, { method, data, headers: { 'X-CSRF-Token': csrf, ...extra } })
  expect(response.status(), `${method} ${path}`).toBe(status)
  return response.json()
}

async function terminal(page: Page, taskId: string) {
  let state: any
  await expect.poll(async () => {
    state = (await api(page, 'GET', `/api/trip/tasks/${taskId}`)).data
    return state.status
  }, { timeout: 90_000, intervals: [300, 600, 1000] }).toMatch(/^(succeeded|needs_attention|failed|cancelled)$/)
  expect(state.status, `${state.error_code || ''}: ${state.message || ''}`).toMatch(/^(succeeded|needs_attention)$/)
  expect(Number(state.result?.id)).toBeGreaterThan(0)
  return state
}

function dates() {
  const start = new Date(Date.now() + 10 * 86_400_000)
  const end = new Date(start.getTime() + 86_400_000)
  const format = (value: Date) => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(value)
  return { start: format(start), end: format(end) }
}

async function seedTrip(page: Page) {
  const { start, end } = dates()
  const submitted = await api(page, 'POST', '/api/trip/tasks', {
    city: '北京', start_date: start, end_date: end, travel_days: 2,
    transportation: '公共交通', accommodation: '经济型酒店', preferences: [],
    planning_input: { timezone: 'Asia/Shanghai', day_start: '09:00', day_end: '20:00' },
  }, 202, { 'Idempotency-Key': randomUUID() })
  return Number((await terminal(page, submitted.data.id)).result.id)
}

async function historyCard(page: Page, id: number) {
  await page.goto('/history')
  const card = page.locator('.record-card').filter({ has: page.getByText(`行程 #${id}`, { exact: true }) })
  await expect(card).toHaveCount(1)
  return card
}

test('owner UI planning, durable clarification and proposal review preserve the original until a decision', async ({ page, request }, testInfo) => {
  test.setTimeout(300_000)
  await isolated(request)
  const username = await register(request, 'owner')
  await login(page, username)
  const { start, end } = dates()
  await page.getByPlaceholder('例如: 北京', { exact: true }).fill('北京')
  const dateInputs = page.getByPlaceholder('选择日期', { exact: true })
  for (const [index, value] of [start, end].entries()) {
    const input = dateInputs.nth(index)
    const visiblePopups = page.locator('.ant-picker-dropdown:visible')
    await expect(visiblePopups).toHaveCount(0)
    await input.click()
    await expect(visiblePopups).toHaveCount(1)
    const calendar = page.locator('.ant-picker-dropdown:not(.ant-picker-dropdown-hidden):not(.ant-slide-up-leave):not(.ant-slide-up-leave-active):visible')
    await expect(calendar).toHaveCount(1)
    const day = calendar.locator(`td.ant-picker-cell-in-view[title="${value}"]:visible`)
    for (let next = 0; next < 3 && !await day.count(); next++) {
      const header = calendar.locator('.ant-picker-header-view')
      const previousMonth = await header.textContent()
      await calendar.locator('.ant-picker-header-next-btn').click()
      await expect(header).not.toHaveText(previousMonth || '')
    }
    await expect(day).toHaveCount(1)
    await expect(day).toBeVisible()
    await day.click()
    await expect(input).toHaveValue(value)
    await expect(visiblePopups).toHaveCount(0)
  }
  await expect(dateInputs.nth(0)).toHaveValue(start)
  await expect(dateInputs.nth(1)).toHaveValue(end)
  const submitted = page.waitForResponse(response => new URL(response.url()).pathname === '/api/trip/tasks' && response.request().method() === 'POST')
  await page.getByRole('button', { name: '开始规划我的旅行' }).click()
  const planned = await submitted
  expect(planned.status()).toBe(202)
  const firstTask = (await planned.json()).data.id
  const tripId = Number((await terminal(page, firstTask)).result.id)
  await expect(page).toHaveURL(/\/result(?:\?|$)/, { timeout: 90_000 })
  const before = (await api(page, 'GET', `/api/history/${tripId}`)).data
  expect(before.plan.days).toHaveLength(2)
  await (await historyCard(page, tripId)).getByRole('button', { name: '💬 问攻略', exact: true }).click()

  const firstMessage = page.waitForResponse(response => /\/api\/assistant\/conversations\/[^/]+\/messages$/.test(new URL(response.url()).pathname) && response.request().method() === 'POST')
  await page.getByPlaceholder('例如：这份行程里的故宫如何预约？哪一天安排最合适？').fill('轻松一点')
  await page.getByRole('button', { name: '获取直接回答', exact: true }).click()
  const missing = await firstMessage
  expect(missing.status()).toBe(202)
  const first = await missing.json()
  expect(first.status).toBe('needs_clarification')
  expect(first.clarification.original_request).toBe('轻松一点')
  expect(first.clarification.missing_slots).toContain('target_day')
  const conversation = /\/conversations\/([^/]+)\/messages$/.exec(new URL(missing.url()).pathname)![1]
  await expect(page.getByText('正在补充：轻松一点', { exact: true })).toBeVisible()
  // Reload the actual page, reopen the trip-bound existing conversation and
  // verify the UI reads the persisted clarification rather than local state.
  await page.reload()
  await (await historyCard(page, tripId)).getByRole('button', { name: '💬 问攻略', exact: true }).click()
  await expect(page.getByText('正在补充：轻松一点', { exact: true })).toBeVisible()
  const stored = await api(page, 'GET', `/api/assistant/conversations/${conversation}/messages`)
  expect(stored.clarification.status).toBe('pending')

  const secondMessage = page.waitForResponse(response => new URL(response.url()).pathname === `/api/assistant/conversations/${conversation}/messages` && response.request().method() === 'POST')
  await page.getByPlaceholder('例如：这份行程里的故宫如何预约？哪一天安排最合适？').fill('第2天')
  await page.getByRole('button', { name: '获取直接回答', exact: true }).click()
  const second = await secondMessage
  expect(second.status()).toBe(202)
  const accepted = await second.json()
  expect(accepted.status).toBe('accepted')
  const proposalTask = accepted.task_id
  const completed = await terminal(page, proposalTask)
  const proposalId = Number(completed.result.id)
  const proposal = (await api(page, 'GET', `/api/history/${proposalId}`)).data
  expect(proposal.quality.assistant_conversation_id).toBe(conversation)
  expect(proposal.quality.assistant_proposal_status).toBe('pending')
  expect(proposal.plan.days[0]).toEqual(before.plan.days[0])
  const unchanged = (await api(page, 'GET', `/api/history/${tripId}`)).data
  expect(unchanged.version).toBe(before.version)
  expect(unchanged.plan).toEqual(before.plan)

  await page.goto('/history?tab=tasks')
  await page.getByText('全部任务', { exact: true }).click()
  await page.getByTestId(`task-${proposalTask}`).getByRole('button', { name: '查看助手方案', exact: true }).click()
  await expect(page.getByTestId('proposal-diff')).toBeVisible()
  await expect(page.getByText(`待确认的助手改排方案；原行程 #${tripId} 尚未更新。`, { exact: true })).toBeVisible()
  const rejected = page.waitForResponse(response => new URL(response.url()).pathname.endsWith(`/proposals/${proposalId}/discard`) && response.request().method() === 'POST')
  await page.getByRole('button', { name: '放弃方案', exact: true }).click()
  expect((await rejected).status()).toBe(200)
  await expect(page).toHaveURL(/\/history$/)
  const after = (await api(page, 'GET', `/api/history/${tripId}`)).data
  expect(after.version).toBe(before.version)
  expect(after.plan).toEqual(before.plan)
  expect((await api(page, 'GET', `/api/history/${proposalId}`)).data.quality.assistant_proposal_status).toBe('discarded')
  await testInfo.attach('proposal-flow-boundary', { body: JSON.stringify({ tripId, proposalId, realBusinessHttp: true, providerFixture: true, originalUnchangedAfterDiscard: true }), contentType: 'application/json' })
})

test('changed planning input disables obsolete time actions and real HTML export retains the stale warning', async ({ page, request }, testInfo) => {
  test.setTimeout(180_000)
  await isolated(request)
  await login(page, await register(request, 'stale'))
  const tripId = await seedTrip(page)
  const before = (await api(page, 'GET', `/api/history/${tripId}`)).data
  const timed = before.plan.days.flatMap((day: any) => day.timeline || []).find((item: any) => item.kind === 'attraction' && item.activity_id && item.start_at && item.end_at)
  expect(timed, 'Fixture must produce an actual saved timed activity').toBeTruthy()
  await page.goto(`/trips/${tripId}/operations`)
  await page.getByRole('tab', { name: '时间与约束', exact: true }).click()
  const input = await api(page, 'GET', `/api/trips/${tripId}/planning-input`)
  const changedEnd = input.data.planning_input.day_end === '19:30' ? '20:00' : '19:30'
  await page.getByLabel('每日最晚结束', { exact: true }).fill(changedEnd)
  const saving = page.waitForResponse(response => new URL(response.url()).pathname === `/api/trips/${tripId}/planning-input` && response.request().method() === 'PUT')
  await page.getByTestId('planning-save').click()
  expect((await saving).status()).toBe(200)
  await expect(page.getByText('规划输入已保存；当前行程未自动改排', { exact: true })).toBeVisible()
  const changed = (await api(page, 'GET', `/api/trips/${tripId}/planning-input`)).data
  expect(changed.planning_revision).toBe(input.data.planning_revision + 1)
  expect(changed.plan.days.some((day: any) => day.schedule_stale)).toBe(true)
  const staleRecord = (await api(page, 'GET', `/api/history/${tripId}`)).data
  for (const body of [{ locked: true, lock_mode: 'time' }, { completed: true }]) {
    const denied = await api(page, 'POST', `/api/trips/${tripId}/activities/${encodeURIComponent(timed.activity_id)}/state`, {
      ...body, planning_revision: changed.planning_revision,
    }, 409, { 'If-Match': String(changed.trip_version) })
    expect(denied.code).toBe('SCHEDULE_STALE')
  }
  const afterDenials = (await api(page, 'GET', `/api/trips/${tripId}/planning-input`)).data
  expect(afterDenials.planning_revision).toBe(changed.planning_revision)
  expect(afterDenials.planning_input.completed_activity_ids).not.toContain(timed.activity_id)
  await page.getByRole('tab', { name: '行前核验', exact: true }).click()
  const factEditor = page.getByTestId('fact-completion')
  const factChecklist = factEditor.getByTestId('fact-checklist')
  await expect(factChecklist).toBeVisible()
  const missingOpening = factChecklist.locator('li').filter({ hasText: '营业与最后入场' }).first()
  await missingOpening.getByRole('button', { name: '补充或更新', exact: true }).click()
  const selectedPoi = await factEditor.getByRole('combobox', { name: '地点', exact: true }).inputValue()
  expect(selectedPoi).not.toBe('')
  await factEditor.getByLabel('来源名称或链接', { exact: true }).fill('隔离浏览器合成公告，不是真实旅行事实')
  await factEditor.getByLabel('有效至', { exact: true }).fill('2020-01-01T10:00')
  await factEditor.getByRole('checkbox', { name: /我已核对以上信息/ }).check()
  await factEditor.getByRole('button', { name: '加入待保存事实', exact: true }).click()
  await expect(factEditor.getByRole('alert')).toContainText('有效期须晚于获取时间和当前时间')
  // Return to a current declaration; this stays in the editable input until
  // the owner explicitly saves and requests a new proposal.
  await missingOpening.getByRole('button', { name: '补充或更新', exact: true }).click()
  await factEditor.getByLabel('来源名称或链接', { exact: true }).fill('隔离浏览器合成公告，不是真实旅行事实')
  await factEditor.getByRole('checkbox', { name: /我已核对以上信息/ }).check()
  await factEditor.getByRole('button', { name: '加入待保存事实', exact: true }).click()
  await expect(factEditor.getByRole('alert')).toHaveCount(0)
  await expect(factChecklist).toContainText('清单为空不代表行程已核实可行')
  expect((await api(page, 'GET', `/api/trips/${tripId}/planning-input`)).data.planning_revision).toBe(changed.planning_revision)
  await page.screenshot({ path: testInfo.outputPath('fact-checklist.png'), fullPage: true })
  await page.getByRole('button', { name: '打开行程并按日改排', exact: true }).click()
  await expect(page.locator('.timeline').first().getByText('此时间轴的条件或事实已过期，仅供对照；请补充事实并重新规划后再锁定时段或标记完成。', { exact: true })).toBeVisible()
  const activity = page.getByTestId(`activity-${timed.activity_id}`).first()
  await expect(activity.getByRole('button', { name: '锁定时段', exact: true })).toBeDisabled()
  await expect(activity.getByRole('button', { name: '标记完成', exact: true })).toBeDisabled()
  const downloading = page.waitForEvent('download')
  await page.getByRole('button', { name: /导出行程/ }).click()
  await page.getByRole('menuitem', { name: /导出离线网页/ }).click()
  const download = await downloading
  expect(download.suggestedFilename()).toMatch(/\.html$/)
  const path = testInfo.outputPath('obsolete-timeline.html')
  await download.saveAs(path)
  const html = await readFile(path, 'utf8')
  expect(html).toContain('时间轴已过期或依赖条件已变化')
  expect(html).toContain(timed.title)
  expect(html).not.toContain('trip_session=')
  const offline = await page.context().newPage()
  try {
    await offline.goto(pathToFileURL(path).href)
    await expect(offline.getByRole('note').filter({ hasText: '时间轴已过期或依赖条件已变化' })).toBeVisible()
  } finally { await offline.close() }
  expect((await api(page, 'GET', `/api/history/${tripId}`)).data.version).toBe(staleRecord.version)
  await testInfo.attach('obsolete-timeline-html', { path, contentType: 'text/html' })
})

test('separate guest, member and admin browser contexts enforce the actual authorization boundaries', async ({ browser, request }, testInfo) => {
  test.setTimeout(300_000)
  await isolated(request)
  const adminName = process.env.OPTIMIZATION_ADMIN_USERNAME
  const adminPassword = process.env.OPTIMIZATION_ADMIN_PASSWORD
  expect(adminName, 'Set explicit isolated administrator username; do not skip this role').toBeTruthy()
  expect(adminPassword, 'Set isolated administrator password without storing it in artifacts').toBeTruthy()
  const contexts: BrowserContext[] = []
  const pages: Record<string, Page> = {}
  const names: Record<string, string> = {}
  try {
    for (const role of ['owner', 'editor', 'viewer', 'pending', 'removed', 'stranger']) {
      names[role] = await register(request, role)
      const context = await browser.newContext({ baseURL: testInfo.project.use.baseURL })
      await guardOfflineResources(context, testInfo.project.use.baseURL!)
      contexts.push(context)
      pages[role] = await context.newPage()
      await login(pages[role], names[role])
    }
    const owner = pages.owner
    const tripId = await seedTrip(owner)
    const memberIds: Record<string, number> = {}
    for (const role of ['editor', 'viewer', 'pending', 'removed']) {
      memberIds[role] = (await api(owner, 'POST', `/api/trips/${tripId}/members`, { username: names[role], role: role === 'editor' ? 'editor' : 'viewer' })).user_id
      if (role !== 'pending') await api(pages[role], 'POST', `/api/trips/${tripId}/members/accept`)
    }
    await api(owner, 'DELETE', `/api/trips/${tripId}/members/${memberIds.removed}`)
    const planning = (await api(owner, 'GET', `/api/trips/${tripId}/planning-input`)).data
    for (const role of ['owner', 'editor', 'viewer', 'pending', 'removed', 'stranger']) {
      const page = pages[role]
      const allowed = ['owner', 'editor', 'viewer'].includes(role)
      const workspaceResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/trips/${tripId}/workspace`)
      await page.goto(`/trips/${tripId}/operations`)
      expect((await workspaceResponse).status(), `${role} actual workspace read`).toBe(allowed ? 200 : 404)
      if (!allowed) {
        await expect(page.getByRole('tab', { name: '行前核验', exact: true })).toHaveCount(0)
      } else {
        await expect(page.getByRole('tab', { name: '行前核验', exact: true })).toBeVisible()
        if (role !== 'owner') await expect(page.getByRole('tab', { name: '时间与约束', exact: true })).toHaveCount(0)
        if (role === 'editor') {
          await page.getByRole('tab', { name: '同行协作', exact: true }).click()
          await expect(page.getByRole('button', { name: '保存顺序', exact: true })).toBeVisible()
          await page.getByRole('tab', { name: '预订与费用', exact: true }).click()
          await page.getByPlaceholder('实际金额', { exact: true }).fill('12.30')
          await page.getByPlaceholder('支出说明', { exact: true }).fill('editor真实UI记账')
          const writing = page.waitForResponse(response => new URL(response.url()).pathname === `/api/trips/${tripId}/expenses` && response.request().method() === 'POST')
          await page.getByRole('button', { name: '记一笔', exact: true }).click()
          expect((await writing).status()).toBe(200)
          await expect(page.getByText('editor真实UI记账', { exact: true })).toBeVisible()
        }
        if (role === 'viewer') {
          await page.getByRole('tab', { name: '预订与费用', exact: true }).click()
          await expect(page.getByRole('button', { name: '添加待确认事项', exact: true })).toHaveCount(0)
        }
      }
      if (role !== 'owner') {
        await api(page, 'PUT', `/api/trips/${tripId}/planning-input`, {
          planning_input: planning.planning_input, planning_revision: planning.planning_revision,
        }, 404, { 'If-Match': String(planning.trip_version) })
      }
    }
    const viewerWrite = { day_index: 0, category: 'meal', amount: '12.30', note: '权限反例，不应落库' }
    await api(pages.viewer, 'POST', `/api/trips/${tripId}/expenses`, viewerWrite, 404, { 'Idempotency-Key': randomUUID() })
    await api(pages.removed, 'POST', `/api/trips/${tripId}/expenses`, viewerWrite, 404, { 'Idempotency-Key': randomUUID() })
    const expenses = await api(owner, 'GET', `/api/trips/${tripId}/expenses`)
    expect(expenses.data).toHaveLength(1)
    expect(expenses.summary.actual_cents).toBe(1230)
    await api(owner, 'DELETE', `/api/trips/${tripId}/members/${memberIds.editor}`)
    await pages.editor.reload()
    await expect(pages.editor.getByRole('tab', { name: '预订与费用', exact: true })).toHaveCount(0)
    await api(pages.editor, 'POST', `/api/trips/${tripId}/expenses`, viewerWrite, 404, { 'Idempotency-Key': randomUUID() })
    expect((await api(owner, 'GET', `/api/trips/${tripId}/expenses`)).data).toHaveLength(1)

    const share = await api(owner, 'POST', `/api/trips/${tripId}/shares`, { expires_days: 1 })
    const guestContext = await browser.newContext({ baseURL: testInfo.project.use.baseURL })
    await guardOfflineResources(guestContext, testInfo.project.use.baseURL!)
    contexts.push(guestContext)
    const guest = await guestContext.newPage()
    await guest.goto(`/shared/${encodeURIComponent(share.token)}`)
    await expect(guest.locator('.timeline').first()).toBeVisible()
    await expect(guest.getByRole('button', { name: '复制到我的行程', exact: true })).toHaveCount(0)
    await expect(guest.getByRole('button', { name: /锁定时段|标记完成|编辑行程/ })).toHaveCount(0)
    expect((await guest.request.get(`/api/trips/${tripId}/workspace`)).status()).toBe(401)
    await api(owner, 'DELETE', `/api/trips/${tripId}/shares/${share.id}`)
    await guest.reload()
    await expect(guest.getByText('分享不可用', { exact: true })).toBeVisible()

    const adminContext = await browser.newContext({ baseURL: testInfo.project.use.baseURL })
    await guardOfflineResources(adminContext, testInfo.project.use.baseURL!)
    contexts.push(adminContext)
    const admin = await adminContext.newPage()
    await login(admin, adminName!, adminPassword!, process.env.OPTIMIZATION_ADMIN_TOTP_SECRET)
    expect((await api(admin, 'GET', '/api/auth/me')).is_admin).toBe(true)
    const privilegedRead = admin.waitForResponse(response => new URL(response.url()).pathname === '/api/knowledge/admin/submissions')
    await admin.goto('/knowledge/admin')
    expect((await privilegedRead).status(), 'Explicit prepared admin must pass the real privileged endpoint').toBe(200)
    await expect(admin.getByRole('heading', { name: '公共知识审核', exact: true })).toBeVisible()
    await api(pages.stranger, 'GET', '/api/knowledge/admin/submissions', undefined, 403)
    await api(admin, 'GET', `/api/trips/${tripId}/workspace`, undefined, 404)
    await testInfo.attach('isolated-role-boundaries', { body: JSON.stringify({ tripId, roles: ['owner', 'editor', 'viewer', 'pending', 'removed', 'stranger', 'guest', 'admin'], businessHttpIntercepted: false, providerFixture: true }), contentType: 'application/json' })
  } finally { await Promise.all(contexts.map(context => context.close())) }
})

test('knowledge submission, original-file review and publication feed actual Research retrieval and explicit answering', async ({ page, browser, request }, testInfo) => {
  test.setTimeout(240_000)
  await isolated(request)
  const adminName = process.env.OPTIMIZATION_ADMIN_USERNAME
  const adminPassword = process.env.OPTIMIZATION_ADMIN_PASSWORD
  expect(adminName, 'This actual admin review flow requires a prepared isolated administrator').toBeTruthy()
  expect(adminPassword, 'This actual admin review flow requires the isolated administrator password').toBeTruthy()
  await login(page, await register(request, 'knowledge'))
  const marker = `核验标记${stamp()}`
  const title = `复核资料${marker}`
  const excerpt = '出发前请核对景区官方公告。'
  await page.goto('/knowledge')
  await expect(page.getByText(/已用.*MB.*待核对预留/)).toBeVisible()
  await page.getByPlaceholder('例如：北京', { exact: true }).fill('北京')
  await page.getByPlaceholder('例如：故宫参观攻略', { exact: true }).fill(title)
  // An actual valid PNG generated in this browser; uploads, original bytes,
  // extraction versions and review transitions still use real Java HTTP.
  const png = await page.evaluate((color: string) => {
    const canvas = document.createElement('canvas')
    canvas.width = 16; canvas.height = 16
    const context = canvas.getContext('2d')!
    context.fillStyle = color; context.fillRect(0, 0, 16, 16)
    return canvas.toDataURL('image/png').split(',')[1]
  }, `#${randomUUID().replace(/-/g, '').slice(0, 6)}`)
  await page.locator('input[type="file"]').setInputFiles({ name: `${marker}.png`, mimeType: 'image/png', buffer: Buffer.from(png, 'base64') })
  const uploading = page.waitForResponse(response => new URL(response.url()).pathname === '/api/knowledge/submissions' && response.request().method() === 'POST')
  await page.getByRole('button', { name: '提交审核', exact: true }).click()
  const upload = await uploading
  expect(upload.status()).toBe(201)
  const documentId = Number((await upload.json()).data.id)
  expect(documentId).toBeGreaterThan(0)
  await expect(page.getByText(title, { exact: true })).toBeVisible()
  const mine = (await api(page, 'GET', '/api/knowledge/submissions/mine')).data.find((item: any) => item.id === documentId)
  expect(mine.status).toBe('pending')
  await api(page, 'GET', `/api/knowledge/admin/submissions/${documentId}/preview`, undefined, 403)

  async function retrieve() {
    const received = page.waitForResponse(response => new URL(response.url()).pathname === '/api/research/retrieve' && response.request().method() === 'POST')
    await page.getByRole('button', { name: '仅检索资料', exact: true }).click()
    const response = await received
    expect(response.status()).toBe(200)
    const data = (await response.json()).data
    expect(data.mode).toBe('retrieval_only')
    expect(data.generation_calls).toBe(0)
    await expect(page.getByTestId('retrieval-call-boundary')).toBeVisible()
    await expect(page.getByTestId('research-answer')).toHaveCount(0)
    return data
  }
  await page.goto('/research')
  await page.getByPlaceholder('例如：北京', { exact: true }).fill('北京')
  await page.getByPlaceholder('例如：带孩子参观博物馆需要注意什么？', { exact: true }).fill(marker)
  expect((await retrieve()).evidence).toEqual([])

  const adminContext = await browser.newContext({ baseURL: testInfo.project.use.baseURL })
  await guardOfflineResources(adminContext, String(testInfo.project.use.baseURL))
  try {
    const admin = await adminContext.newPage()
    await login(admin, adminName!, adminPassword!, process.env.OPTIMIZATION_ADMIN_TOTP_SECRET)
    await admin.goto('/knowledge/admin')
    const item = admin.locator('.ant-list-item').filter({ has: admin.getByText(title, { exact: true }) })
    await expect(item).toHaveCount(1)
    const approving = admin.waitForResponse(response => new URL(response.url()).pathname === `/api/knowledge/admin/submissions/${documentId}/approve` && response.request().method() === 'POST')
    await item.getByRole('button', { name: '开始解析', exact: true }).click()
    expect((await approving).status()).toBe(200)
    await expect.poll(async () => (await api(admin, 'GET', `/api/knowledge/admin/submissions/${documentId}/preview`)).data.status,
      { timeout: 60_000, intervals: [300, 600, 1000] }).toBe('awaiting_review')
    const original = admin.waitForResponse(response => new URL(response.url()).pathname === `/api/knowledge/admin/submissions/${documentId}/original`)
    await item.getByRole('button', { name: '查看与复核', exact: true }).click()
    const originalResponse = await original
    expect(originalResponse.status()).toBe(200)
    expect(originalResponse.headers()['content-type']).toContain('image/png')
    const review = admin.getByRole('dialog', { name: '对照原件复核提取结果' })
    await expect(review.locator('iframe[title="原文件"]')).toHaveAttribute('src', /^blob:/)
    await expect(review.locator('textarea')).toHaveCount(1)
    await review.locator('textarea').fill(`### ${marker}\n${excerpt}`)
    const publishButton = review.getByRole('button', { name: '已对照原件，确认发布当前版本', exact: true })
    await expect(publishButton).toBeDisabled()
    const saving = admin.waitForResponse(response => new URL(response.url()).pathname === `/api/knowledge/admin/submissions/${documentId}/extraction` && response.request().method() === 'PUT')
    await review.getByRole('button', { name: '保存修订', exact: true }).click()
    expect((await saving).status()).toBe(200)
    await expect(publishButton).toBeEnabled()
    // Still private while awaiting review, even after an actual extraction edit.
    expect((await retrieve()).evidence).toEqual([])
    const publishing = admin.waitForResponse(response => new URL(response.url()).pathname === `/api/knowledge/admin/submissions/${documentId}/publish` && response.request().method() === 'POST')
    await publishButton.click()
    expect((await publishing).status()).toBe(200)
    await expect(review).toHaveCount(0)
    await expect.poll(async () => (await api(admin, 'GET', `/api/knowledge/admin/submissions/${documentId}/preview`)).data.status,
      { timeout: 60_000, intervals: [300, 600, 1000] }).toBe('published')
    const retrieved = await retrieve()
    expect(retrieved.evidence).toHaveLength(1)
    expect(retrieved.evidence[0]).toMatchObject({ source: title, page: 1 })
    expect(retrieved.evidence[0].content).toContain(excerpt)
    await expect(page.getByText(excerpt, { exact: false }).first()).toBeVisible()
    const answering = page.waitForResponse(response => new URL(response.url()).pathname === '/api/research/answer' && response.request().method() === 'POST')
    await page.getByRole('button', { name: '根据资料回答（使用模型）', exact: true }).click()
    const answerResponse = await answering
    expect(answerResponse.status()).toBe(200)
    const answer = (await answerResponse.json()).data
    expect(answer.mode).toBe('answer')
    expect(answer.generation_calls).toBe(1) // one offline provider invocation, not a paid call
    expect(answer.answer_status).toBe('supported_excerpt_answer')
    expect(answer.support_check.passed).toBe(true)
    expect(answer.semantic_truth_status).toBe('not_independently_evaluated')
    await expect(page.getByTestId('research-answer')).toContainText(excerpt)
    await expect(page.getByTestId('research-answer')).toContainText('未进行独立评估')
    await testInfo.attach('knowledge-research-real-http', { body: JSON.stringify({ documentId, actualMultipartUpload: true, actualOriginalRead: true,
      awaitingReviewWasInvisible: true, publishedIndexVisible: true, retrievalGenerationCalls: 0, offlineAnswerProviderCalls: 1,
      businessResponsesFulfilled: false, externalProviderValidation: false, paidCalls: 0 }), contentType: 'application/json' })
  } finally { await adminContext.close() }
})

test('community owner submission, admin review, independent Cookie member copy and guest login prompt use actual HTTP', async ({ page, browser, request }, testInfo) => {
  test.setTimeout(180_000)
  await isolated(request)
  const adminName = process.env.OPTIMIZATION_ADMIN_USERNAME
  const adminPassword = process.env.OPTIMIZATION_ADMIN_PASSWORD
  expect(adminName, 'Community moderation must run with the prepared isolated admin').toBeTruthy()
  expect(adminPassword, 'Community moderation needs an actual isolated admin login').toBeTruthy()
  const ownerName = await register(request, 'publisher')
  await login(page, ownerName)
  const tripId = await seedTrip(page)
  const original = (await api(page, 'GET', `/api/history/${tripId}`)).data
  const submitting = page.waitForResponse(response => new URL(response.url()).pathname === '/api/community/cards' && response.request().method() === 'POST')
  await (await historyCard(page, tripId)).getByRole('button', { name: '🌏 投稿广场', exact: true }).click()
  const submitted = await submitting
  expect(submitted.status()).toBe(200)
  const submission = await submitted.json()
  expect(submission.status).toBe('pending')
  const cardId = Number(submission.id)
  await api(page, 'GET', `/api/community/cards/${cardId}`, undefined, 404)
  await api(page, 'GET', '/api/community/admin/cards', undefined, 403)
  const contexts: BrowserContext[] = []
  try {
    async function contextPage() {
      const context = await browser.newContext({ baseURL: testInfo.project.use.baseURL })
      contexts.push(context)
      await guardOfflineResources(context, String(testInfo.project.use.baseURL))
      return context.newPage()
    }
    const admin = await contextPage()
    await login(admin, adminName!, adminPassword!, process.env.OPTIMIZATION_ADMIN_TOTP_SECRET)
    await admin.goto('/community/admin')
    const moderation = admin.locator('tr').filter({ hasText: ownerName })
    await expect(moderation).toHaveCount(1)
    await expect(moderation).toContainText('北京2日行程')
    const approving = admin.waitForResponse(response => new URL(response.url()).pathname === `/api/community/admin/cards/${cardId}/review` && response.request().method() === 'POST')
    await moderation.getByRole('button', { name: /^通\s*过$/, exact: true }).click()
    expect((await approving).status()).toBe(200)
    const publicCard = (await api(page, 'GET', `/api/community/cards/${cardId}`)).data
    expect(publicCard.record_version).toBe(original.version)
    expect(publicCard.snapshot.plan.days.map((day: any) => day.attractions)).toEqual(original.plan.days.map((day: any) => day.attractions))

    const guest = await contextPage()
    const guestCopies: string[] = []
    guest.on('request', request => {
      if (request.method() === 'POST' && /\/api\/community\/cards\/\d+\/copy$/.test(new URL(request.url()).pathname)) guestCopies.push(request.url())
    })
    await guest.goto('/community')
    await expect(guest.getByRole('button', { name: '登录后复制', exact: true })).toBeVisible()
    const guestCard = guest.locator('article.card').filter({ hasText: ownerName })
    await expect(guestCard).toHaveCount(1)
    await guestCard.getByRole('button', { name: '复制为我的草稿', exact: true }).click()
    await expect(guest).toHaveURL(/\/login\?redirect=(?:%2F|\/)community$/)
    expect(guestCopies).toEqual([])

    const member = await contextPage()
    await login(member, await register(request, 'copier'))
    await member.goto('/community')
    const memberCard = member.locator('article.card').filter({ hasText: ownerName })
    await expect(memberCard).toHaveCount(1)
    const copying = member.waitForResponse(response => new URL(response.url()).pathname === `/api/community/cards/${cardId}/copy` && response.request().method() === 'POST')
    await memberCard.getByRole('button', { name: '复制为我的草稿', exact: true }).click()
    const copiedResponse = await copying
    expect(copiedResponse.status()).toBe(200)
    const copiedId = Number((await copiedResponse.json()).id)
    expect(copiedId).toBeGreaterThan(0)
    expect(copiedId).not.toBe(tripId)
    await expect(member).toHaveURL(/\/history$/)
    const copied = (await api(member, 'GET', `/api/history/${copiedId}`)).data
    expect(copied.source).toBe('copied')
    expect(copied.quality.outcome).toBe('draft')
    expect(copied.quality.data_gaps).toContain('copied_community_plan_requires_reverification')
    expect(copied.plan.days.every((day: any) => !day.schedule_dependencies && day.schedule_stale)).toBe(true)
    expect(copied.plan.days.map((day: any) => day.attractions)).toEqual(original.plan.days.map((day: any) => day.attractions))
    await api(member, 'GET', `/api/history/${tripId}`, undefined, 404)
    await api(page, 'GET', `/api/history/${copiedId}`, undefined, 404)
    const unchanged = (await api(page, 'GET', `/api/history/${tripId}`)).data
    expect(unchanged.version).toBe(original.version)
    expect(unchanged.plan).toEqual(original.plan)
    await testInfo.attach('community-role-real-http', { body: JSON.stringify({ tripId, cardId, copiedId, pendingInvisible: true,
      adminReviewUi: true, guestRedirectWithoutCopyPost: true, independentMemberDraft: true, ownerUnchanged: true,
      cookieContexts: true, businessResponsesFulfilled: false, externalProviderValidation: false }), contentType: 'application/json' })
  } finally { await Promise.all(contexts.map(context => context.close())) }
})

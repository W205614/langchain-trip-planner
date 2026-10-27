/* Prepare/run only inside the human-authorized daily maintenance window.
 * No Docker, forged JWT, fixture provider, model/embedding/map request or edit
 * to a pre-existing private trip. Operator performs original-column SQL proof
 * before this script. Root creates/logs in at most one smoke account BEFORE
 * the backup. DAILY_SMOKE_CREDENTIALS_PATH identifies its private JSON containing
 * username/password/old_bearer/user_id/copied_trip_id (the last may be null).
 * This POST-upgrade script never registers, copies or saves preferences.
 *
 * DAILY_ACCEPTANCE_OUTPUT must be a PRIVATE directory outside public evidence.
 * BASE_URL defaults to the actual daily loopback port 8380. Do not use 18088.
 */
'use strict'
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs/promises')
const repo = path.resolve(__dirname, '../../..')
const { chromium, expect } = require(path.join(repo, 'frontend/node_modules/@playwright/test'))
const origin = new URL(process.env.BASE_URL || 'http://127.0.0.1:8380').origin
assert.equal(origin, 'http://127.0.0.1:8380', 'Only the authorized daily loopback endpoint is accepted')
assert.ok(process.env.DAILY_ACCEPTANCE_OUTPUT, 'Set a private output directory explicitly')
const output = path.resolve(process.env.DAILY_ACCEPTANCE_OUTPUT)
assert.ok(!output.startsWith(path.join(repo, 'docs') + path.sep), 'Private output must not enter public docs/evidence')
const report = { passed: false, started_at: new Date().toISOString(), scope: 'daily affected Cookie/auth/preferences/history/version/export readback',
  checks: [], coverage_gaps: [], browser_provider_requests_aborted: 0,
  boundary: { real_business_http: true, fixture_provider_enabled: false, jwt_injection: false,
    direct_provider_calls: 0, backend_paid_calls: 'not_measured_here', original_private_trip_mutations: 0,
    original_column_sql_preservation: 'operator must compare before smoke login; not established by this script',
    runtime_env_and_true_agent_uvicorn: 'operator runtime inspection required; readiness alone is not full RAG proof' } }
let browser, context, page, credentials
const checked = name => report.checks.push({ name, passed: true })
const same = (left, right) => JSON.stringify(left) === JSON.stringify(right)
function safeError(error) {
  let message = String(error.message || error)
  for (const key of ['password', 'totp_code', 'old_bearer']) if (credentials?.[key]) message = message.split(credentials[key]).join('[REDACTED]')
  return { type: error.name, message: message.slice(0, 1800) }
}
async function privateJson(name, value) {
  await fs.writeFile(path.join(output, name), JSON.stringify(value, null, 2) + '\n', { mode: 0o600 })
}
function responseFor(method, pathname) {
  const promise = page.waitForResponse(response => response.request().method() === method &&
    new URL(response.url()).pathname === pathname, { timeout: 30_000 })
  promise.catch(error => { report.waiter_errors ||= []; report.waiter_errors.push(safeError(error)) })
  return promise
}
async function api(method, endpoint, data, expected = 200, csrf = true, extra = {}) {
  assert.match(endpoint, /^\/api\/(auth\/(?:me|logout|security)$|preferences\/me$|history(?:\?|\/|$)|trips\/\d+\/(?:versions|planning-input|activities\/))/,
    'This script may not call generation/provider endpoints')
  const token = (await context.cookies()).find(cookie => cookie.name === 'trip_csrf')?.value || ''
  const response = await context.request.fetch(endpoint, { method, data, headers: { ...(csrf ? { 'X-CSRF-Token': token } : {}), ...extra } })
  assert.equal(response.status(), expected, `${method} ${new URL(endpoint, origin).pathname}`)
  return response.json()
}

;(async () => {
  await fs.mkdir(output, { recursive: true })
  assert.ok(process.env.DAILY_SMOKE_CREDENTIALS_PATH, 'Root pre-backup legitimate smoke credentials required; no post-upgrade registration fallback')
  credentials = JSON.parse(await fs.readFile(process.env.DAILY_SMOKE_CREDENTIALS_PATH, 'utf8'))
  assert.ok(credentials.username && credentials.password && credentials.old_bearer && Number(credentials.user_id) > 0)
  report.account_mode = 'root_prebackup_legitimate_smoke_old_bearer_and_password'
  report.new_account_count = 0
  browser = await chromium.launch({ headless: true })
  context = await browser.newContext({ baseURL: origin, acceptDownloads: true, viewport: { width: 1440, height: 1000 } })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    const provider = /^\/api\/(?:map\/|poi\/photo\/|research\/)/.test(url.pathname) ||
      (url.pathname === '/api/trip/tasks' && route.request().method() === 'POST')
    if ((['http:', 'https:'].includes(url.protocol) && url.origin !== origin) || provider) {
      report.browser_provider_requests_aborted++; return route.abort('blockedbyclient')
    }
    return route.continue()
  })
  const ready = await context.request.get('/readyz')
  assert.equal(ready.status(), 200)
  const marker = await context.request.get('/api/validation/fixture')
  assert.ok([401, 403, 404].includes(marker.status()), 'Daily must not expose an active validation fixture')
  checked('daily_readiness_and_no_fixture_endpoint')
  const old = await api('GET', '/api/auth/me', undefined, 200, false, { Authorization: `Bearer ${credentials.old_bearer}` })
  assert.equal(Number(old.id), Number(credentials.user_id))
  assert.equal(old.username, credentials.username)
  checked('preupgrade_real_login_bearer_still_reads_its_own_identity')
  page = await context.newPage(); await page.goto('/login')
  await page.getByPlaceholder('用户名', { exact: true }).fill(credentials.username)
  await page.getByPlaceholder('密码', { exact: true }).fill(credentials.password)
  if (credentials.totp_code) await page.getByPlaceholder('验证器中的6位数字', { exact: true }).fill(credentials.totp_code)
  const login = responseFor('POST', '/api/auth/login')
  await page.getByRole('button', { name: /登\s*录/, exact: true }).click()
  assert.equal((await login).status(), 200)
  await expect(page).toHaveURL(`${origin}/`)
  const cookies = await context.cookies()
  assert.equal(cookies.find(cookie => cookie.name === 'trip_session')?.httpOnly, true)
  assert.ok(cookies.find(cookie => cookie.name === 'trip_csrf')?.value)
  const me = await api('GET', '/api/auth/me')
  assert.equal(me.username, credentials.username)
  assert.equal(Number(me.id), Number(credentials.user_id))
  assert.equal(me.is_admin, false, 'Use an ordinary smoke account; do not reuse a private administrator')
  await privateJson('smoke-identity.private.json', { user_id: me.id, registered_before_backup_by_root: true })
  const security = await api('GET', '/api/auth/security')
  assert.equal(security.admin_mfa_required, true)
  const authenticatedMarker = await context.request.get('/api/validation/fixture')
  assert.equal(authenticatedMarker.status(), 404)
  checked('real_cookie_ui_login_current_identity_admin_mfa_policy')
  const preferencesBefore = await api('GET', '/api/preferences/me')
  // The deliberately denied request cannot mutate preferences. This checks
  // the actual Cookie CSRF gate without a token/Bearer shortcut.
  const denied = await api('PUT', '/api/preferences/me', { preferences: ['自然风光'], transportation: '公共交通', accommodation: '经济型酒店' }, 403, false)
  assert.equal(denied.code, 'CSRF_REQUIRED')
  assert.ok(same(await api('GET', '/api/preferences/me'), preferencesBefore), 'Denied CSRF request changed preferences')
  checked('prebackup_smoke_preferences_read_only_and_csrf_rejection')
  const history = await api('GET', '/api/history?page_size=10')
  assert.ok(Array.isArray(history.data))
  const recordId = Number(credentials.copied_trip_id || history.data[0]?.id || 0)
  const copied = Number(credentials.copied_trip_id) > 0
  if (recordId) {
    const original = (await api('GET', `/api/history/${recordId}`)).data
    const versions = await api('GET', `/api/trips/${recordId}/versions`)
    assert.ok(Array.isArray(versions.data))
    if (versions.data.length) {
      const version = await api('GET', `/api/trips/${recordId}/versions/${versions.data[0].version}`)
      assert.ok(version.data.plan && version.data.quality)
    } else report.coverage_gaps.push('Legacy smoke record has no stored version snapshot; versions list only read successfully')
    const input = (await api('GET', `/api/trips/${recordId}/planning-input`)).data
    if (copied) {
      assert.equal(original.quality.outcome, 'draft')
      const scheduled = original.plan.days.filter(day => (day.timeline || []).length ||
        Object.keys(day.schedule_dependencies || {}).length || day.schedule_stale === true ||
        (day.feasibility?.unknown || []).includes('schedule_unknown_or_stale'))
      assert.ok(scheduled.every(day => day.schedule_stale), 'Existing or explicitly invalidated schedules in the old copy must require reverification')
      if (!scheduled.length) report.coverage_gaps.push('Legacy copy has no timeline or solver binding; per-day stale projection cannot establish solver freshness or exercise a timed schedule')
      const timed = original.plan.days.flatMap(day => day.timeline || []).find(item => item.kind === 'attraction' && item.activity_id)
      if (timed) {
        const deniedLock = await api('POST', `/api/trips/${recordId}/activities/${encodeURIComponent(timed.activity_id)}/state`,
          { locked: true, lock_mode: 'time', planning_revision: input.planning_revision }, 409, true, { 'If-Match': String(input.trip_version) })
        assert.equal(deniedLock.code, 'SCHEDULE_STALE')
        const latest = (await api('GET', `/api/trips/${recordId}/planning-input`)).data
        assert.equal(latest.planning_revision, input.planning_revision)
        assert.ok(same(latest.planning_input.locks, input.planning_input.locks), 'Denied stale lock changed protection')
        checked('copied_stale_time_lock_rejected_without_mutation')
      } else report.coverage_gaps.push('Public copy has no stable timed activity; time-lock denial was not exercised')
    }
    await page.goto('/history')
    const card = page.locator('.record-card').filter({ has: page.getByText(`行程 #${recordId}`, { exact: true }) })
    await card.getByRole('button', { name: '👁️ 查看行程', exact: true }).click()
    await expect(page).toHaveURL(/\/result(?:\?|$)/)
    const download = page.waitForEvent('download'); download.catch(() => {})
    await page.getByRole('button', { name: /导出行程/ }).hover()
    await page.getByRole('menuitem', { name: /导出离线网页/ }).click()
    const file = await download
    const destination = path.join(output, 'owned-trip.private.html')
    await file.saveAs(destination); await fs.chmod(destination, 0o600)
    const html = await fs.readFile(destination, 'utf8')
    assert.ok(html.includes('<html') && !html.includes('trip_session='), 'Owned HTML export invalid or contains a session cookie')
    const after = (await api('GET', `/api/history/${recordId}`)).data
    assert.equal(after.version, original.version)
    assert.ok(same(after.plan, original.plan), 'Read/export modified owned private plan')
    await privateJson('readback-identity.private.json', { record_id: recordId, copied_from_public: copied, version: original.version })
    checked('owned_history_version_ui_and_private_html_export_preserve_original')
  } else report.coverage_gaps.push('No owned history or published public card; no fabricated/model-generated trip was inserted. Original private preservation relies on operator SQL aggregate proof.')
  await page.goto('/')
  const loggingOut = responseFor('POST', '/api/auth/logout')
  await page.getByRole('button', { name: /退\s*出/, exact: true }).click()
  assert.equal((await loggingOut).status(), 200)
  await api('GET', '/api/auth/me', undefined, 401)
  await api('GET', '/api/history', undefined, 401)
  await api('GET', '/api/auth/me', undefined, 401, false, { Authorization: `Bearer ${credentials.old_bearer}` })
  checked('real_ui_logout_revokes_cookie_and_preupgrade_bearer_and_blocks_private_reads')
  report.passed = true
})().catch(async error => {
  report.error = safeError(error); process.exitCode = 1
  await fs.mkdir(output, { recursive: true }); await privateJson('report.private.json', report)
}).finally(async () => {
  if (context) await context.close().catch(() => {})
  if (browser) await browser.close().catch(() => {})
  report.finished_at = new Date().toISOString()
  await fs.mkdir(output, { recursive: true }); await privateJson('report.private.json', report)
  await privateJson('public-summary.json', { passed: report.passed, checks: report.checks,
    coverage_gaps: report.coverage_gaps, boundary: report.boundary, new_account_count: report.new_account_count || 0 })
  console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, coverage_gaps: report.coverage_gaps,
    private_report: path.join(output, 'report.private.json') }))
})

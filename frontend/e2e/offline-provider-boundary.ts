import { test, type BrowserContext } from '@playwright/test'

// The Java/Agent fixture deployment remains the real business HTTP path.
// Block only external browser resources, including the billable map SDK.
// This deliberately leaves the map unavailable; it never fabricates a map.
export function installOfflineBrowserBoundary() {
  test.beforeEach(async ({ context, baseURL }) => {
    await guardOfflineResources(context, baseURL!)
  })
}

export async function guardOfflineResources(context: BrowserContext, baseURL: string) {
  context.setDefaultTimeout(15_000)
  const permitted = new URL(baseURL).origin
  await context.route('**/*', route => {
    const target = new URL(route.request().url())
    return !['http:', 'https:'].includes(target.protocol) || target.origin === permitted
      ? route.continue() : route.abort('blockedbyclient')
  })
}

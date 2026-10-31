import { expect, test } from '@playwright/test'
import { createServer, type Server } from 'node:http'
import { once } from 'node:events'
import { mkdir, writeFile } from 'node:fs/promises'
import { runInNewContext } from 'node:vm'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

async function listen(server: Server) {
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('Expected loopback TCP listener')
  return `http://127.0.0.1:${address.port}`
}

async function close(server: Server) {
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
}

test('follow-redirects removes a caller-declared sensitive header from a cross-realm array', async () => {
  const target = createServer((request, response) => response.end(JSON.stringify(request.headers)))
  const targetUrl = await listen(target)
  const source = createServer((_request, response) => { response.writeHead(302, { Location: targetUrl }); response.end() })
  const sourceUrl = await listen(source)
  try {
    const headers = await new Promise<Record<string, string>>((resolve, reject) => {
      const request = require('follow-redirects').http.get(sourceUrl, {
        headers: { 'X-Travel-Test': 'synthetic-regression-value', 'X-Harmless': 'kept' },
        sensitiveHeaders: runInNewContext("['X-Travel-Test']"),
      }, (response: import('node:http').IncomingMessage) => {
        let body = ''
        response.setEncoding('utf8')
        response.on('data', chunk => { body += chunk })
        response.on('end', () => resolve(JSON.parse(body)))
        response.on('error', reject)
      })
      request.on('error', reject)
    })
    expect(headers['x-travel-test']).toBeUndefined()
    expect(headers['x-harmless']).toBe('kept')
  } finally { await close(source); await close(target) }
})

test('Vite denies query variants of a blocked file in an isolated temporary root', async ({}, testInfo) => {
  const { createServer: createViteServer } = await import('vite')
  const root = testInfo.outputPath('isolated-vite-root')
  await mkdir(root, { recursive: true })
  const content = 'TRAVEL_FIXTURE_ONLY=synthetic-regression-content'
  await writeFile(`${root}/.env`, content)
  const server = await createViteServer({ root, configFile: false, logLevel: 'silent',
    server: { host: '127.0.0.1', port: 0, watch: null, fs: { allow: [root], deny: ['**/.env'] } } })
  try {
    await server.listen()
    const address = server.httpServer!.address()
    if (!address || typeof address === 'string') throw new Error('Expected loopback Vite listener')
    for (const query of ['raw', 'import&raw', 'import&url&inline']) {
      const response = await fetch(`http://127.0.0.1:${address.port}/.env?${query}`)
      const body = await response.text()
      expect(response.status, query).toBe(403)
      expect(body, query).not.toContain(content)
    }
  } finally { await server.close() }
})

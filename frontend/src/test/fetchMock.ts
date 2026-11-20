import { vi } from 'vitest'

const requests: { method: string; path: string; body: unknown }[] = []

export function mockFetch(responses: Record<string, unknown>) {
  requests.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string, options?: RequestInit) => {
    const path = new URL(input, 'http://localhost').pathname
    const method = options?.method ?? 'GET'
    requests.push({ method, path, body: options?.body ? JSON.parse(String(options.body)) : undefined })
    const value = responses[`${method} ${path}`] ?? responses[path]
    return value === undefined ? new Response(null, { status: 404 }) : Response.json(value)
  }))
}

export function lastRequest(method: string, path: string) {
  return [...requests].reverse().find((request) => request.method === method && request.path === path)?.body
}

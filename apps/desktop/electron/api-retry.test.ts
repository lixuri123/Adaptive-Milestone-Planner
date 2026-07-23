import assert from 'node:assert/strict'
import test from 'node:test'

import { apiRequestError, describeApiRequest, shouldRetryApiRead } from './api-retry'

test('retries connection resets only for read-only API requests', () => {
  const reset = Object.assign(new Error('read ECONNRESET'), { code: 'ECONNRESET' })

  assert.equal(shouldRetryApiRead({ path: '/api/status' }, reset), true)
  assert.equal(shouldRetryApiRead({ method: 'HEAD', path: '/api/status' }, reset), true)
  assert.equal(shouldRetryApiRead({ method: 'POST', path: '/api/cron/jobs' }, reset), false)
  assert.equal(shouldRetryApiRead({ method: 'DELETE', path: '/api/cron/jobs/1' }, reset), false)
  assert.equal(shouldRetryApiRead({ path: '/api/status' }, Object.assign(new Error('boom'), { code: 'EIO' })), false)
})

test('describes requests without logging query values', () => {
  assert.equal(describeApiRequest({ path: '/api/search?q=private', method: 'get' }), 'GET /api/search')
})

test('adds request context while preserving the network error code', () => {
  const wrapped = apiRequestError(
    { path: '/api/profiles?include=all' },
    Object.assign(new Error('read ECONNRESET'), { code: 'ECONNRESET' })
  )

  assert.equal(wrapped.code, 'ECONNRESET')
  assert.match(wrapped.message, /Hermes API GET \/api\/profiles failed/)
  assert.doesNotMatch(wrapped.message, /include=all/)
})

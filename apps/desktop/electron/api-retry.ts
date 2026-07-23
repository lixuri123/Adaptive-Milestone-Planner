const RETRYABLE_READ_ERROR_CODES = new Set(['ECONNRESET'])
const READ_ONLY_HTTP_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

function apiRequestMethod(request: any): string {
  return String(request?.method || 'GET').toUpperCase()
}

function apiRequestPath(request: any): string {
  const rawPath = String(request?.path || '/')

  return rawPath.split('?', 1)[0] || '/'
}

function errorCode(error: any): string {
  return typeof error?.code === 'string' ? error.code.toUpperCase() : ''
}

function shouldRetryApiRead(request: any, error: any): boolean {
  return READ_ONLY_HTTP_METHODS.has(apiRequestMethod(request)) && RETRYABLE_READ_ERROR_CODES.has(errorCode(error))
}

function describeApiRequest(request: any): string {
  return `${apiRequestMethod(request)} ${apiRequestPath(request)}`
}

function apiRequestError(request: any, error: any): Error & { code?: string } {
  const detail = error instanceof Error ? error.message : String(error)
  const wrapped = new Error(`Hermes API ${describeApiRequest(request)} failed: ${detail}`) as Error & { code?: string }
  const code = errorCode(error)

  if (code) {
    wrapped.code = code
  }

  return wrapped
}

export { apiRequestError, describeApiRequest, shouldRetryApiRead }

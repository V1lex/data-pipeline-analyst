const BASE = '/api/v1';
export async function request(path, options = {}) {
  const multipart = typeof FormData !== 'undefined' && options.body instanceof FormData;
  let response;
  try { response = await fetch(BASE + path, {
    ...options,
    headers: { ...(multipart ? {} : { 'Content-Type': 'application/json' }), ...options.headers },
    body: options.body === undefined ? undefined : multipart ? options.body : JSON.stringify(options.body),
  }); } catch (cause) {
    if (cause?.name === 'AbortError') throw cause;
    const error = new Error('Не удалось связаться с приложением. Проверьте соединение и повторите.');
    error.code = 'NETWORK_ERROR';
    error.retryable = true;
    throw error;
  }
  if (!response.ok) {
    let payload;
    try { payload = await response.json(); } catch { /* fallback below */ }
    const message = payload?.error?.message || (Array.isArray(payload?.detail) ? payload.detail.map(e => e.msg).join('; ') : payload?.detail);
    const error = new Error(message || `Ошибка API: ${response.status}`);
    error.code = payload?.error?.code;
    error.retryable = payload?.error?.retryable;
    error.details = payload?.error?.details;
    throw error;
  }
  return response.json();
}
export const artifactUrl = (spec, artifact) => `${BASE}/specifications/${spec}/artifacts/${artifact}`;

export function errorText(error) {
  return [error.message, error.details?.answer, ...(Array.isArray(error.details?.questions) ? error.details.questions : [])]
    .filter(value => typeof value === 'string' && value.trim()).join('\n');
}

import test from 'node:test';
import assert from 'node:assert/strict';
import { request, errorText } from './client.js';

test('model clarification keeps actual questions visible to the user', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 409, json: async () => ({ error: {
    code: 'NEEDS_CLARIFICATION', message: 'Требования нужно уточнить.',
    details: { answer: 'Нужен режим записи.', questions: ['Перезаписывать результат или добавлять строки?'] },
  } }) });
  try {
    await assert.rejects(request('/specifications/example/generate', { method: 'POST' }), error => {
      assert.equal(error.code, 'NEEDS_CLARIFICATION');
      assert.deepEqual(error.details.questions, ['Перезаписывать результат или добавлять строки?']);
      assert.match(errorText(error), /Нужен режим записи/);
      assert.match(errorText(error), /Перезаписывать результат или добавлять строки/);
      return true;
    });
  } finally { globalThis.fetch = original; }
});

test('network errors give a safe actionable message', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };
  try {
    await assert.rejects(request('/projects'), error => {
      assert.equal(error.code, 'NETWORK_ERROR');
      assert.equal(error.retryable, true);
      assert.match(error.message, /соединение/i);
      return true;
    });
  } finally { globalThis.fetch = original; }
});

test('multipart source upload lets browser set its own content type', async () => {
  const original = globalThis.fetch;
  const body = new FormData();
  body.append('file', new Blob(['a,b\n1,2']), 'sample.csv');
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.body, body);
    assert.equal(options.headers['Content-Type'], undefined);
    return { ok: true, json: async () => ({ id: 'file' }) };
  };
  try { assert.equal((await request('/projects/test/sources', { method: 'POST', body })).id, 'file'); }
  finally { globalThis.fetch = original; }
});

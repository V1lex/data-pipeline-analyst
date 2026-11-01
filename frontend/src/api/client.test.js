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

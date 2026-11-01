import test from 'node:test';
import assert from 'node:assert/strict';
import { WorkspaceController, selectWorkspace } from './state.js';

function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}
function backend() {
  const projects = [];
  const specs = new Map();
  const attempts = [];
  let failMessage = false;
  let generationError = null;
  const api = async (path, options = {}) => {
    if (path === '/projects' && options.method === 'POST') {
      const project = { id: `p${projects.length + 1}`, name: options.body.name, messages: [], specifications: [], analyst: {} };
      projects.push(project);
      return structuredClone(project);
    }
    if (path === '/projects') return structuredClone(projects);
    const turn = path.match(/^\/projects\/(\w+)\/messages$/);
    if (turn) {
      if (failMessage) { failMessage = false; throw Object.assign(new Error('Уточните путь'), { code: 'NEEDS_CLARIFICATION', details: { questions: ['Где лежат заказы?'] } }); }
      const project = projects.find(p => p.id === turn[1]);
      project.messages.push({ id: 'u1', role: 'user', content: options.body.content }, { id: 'a1', role: 'assistant', content: 'Уточним данные.' });
      return {};
    }
    const project = path.match(/^\/projects\/(\w+)$/);
    if (project) return structuredClone(projects.find(p => p.id === project[1]));
    const generation = path.match(/^\/specifications\/(\w+)\/generate$/);
    if (generation) {
      attempts.push(options.body.request_id);
      if (generationError) { const error = generationError; generationError = null; throw error; }
      return { spec_id: generation[1], artifact_id: 'a1', filename: 'pipeline.py', content: 'print(1)', answer: 'Готово' };
    }
    const spec = path.match(/^\/specifications\/(\w+)$/);
    if (spec) return structuredClone(specs.get(spec[1]));
    throw new Error(`Unexpected API path ${path}`);
  };
  return { api, projects, specs, attempts, failMessage: () => { failMessage = true; }, failGeneration: error => { generationError = error; } };
}
function verified(id = 's1', project_id = 'p1') {
  return { id, project_id, version: 1, status: 'APPROVED', context_json: { pipeline_specification: { steps: [{ id: 'read' }] } }, artifacts: [], verifications: [{ id: 'run1', status: 'FINISHED', result_json: { status: 'PROPERTY_HOLDS', engine: 'TLC' } }] };
}

test('first message creates a conversation and displays the persisted answer', async () => {
  const db = backend();
  const store = new WorkspaceController({ api: db.api, storage: null });
  store.setText('Посчитай выручку по городам');
  await store.send();
  assert.equal(store.getSnapshot().projectId, 'p1');
  assert.equal(store.getSnapshot().project.messages.at(-1).content, 'Уточним данные.');
  assert.equal(store.getSnapshot().text, '');
  assert.equal(store.getSnapshot().projects.length, 1);
  assert.equal(store.getSnapshot().pendingMessage, null);
});

test('failed first turn keeps the text and retries inside the same created project', async () => {
  const db = backend();
  const store = new WorkspaceController({ api: db.api, storage: null });
  db.failMessage();
  store.setText('Выручка по городам');
  await store.send();
  assert.equal(store.getSnapshot().projectId, 'p1');
  assert.equal(store.getSnapshot().text, 'Выручка по городам');
  assert.match(store.getSnapshot().error, /Где лежат заказы/);
  assert.equal(store.getSnapshot().busy, '');
  await store.send();
  assert.equal(db.projects.length, 1);
  assert.equal(store.getSnapshot().project.messages.filter(m => m.role === 'user').length, 1);
});

test('out-of-order project reads cannot replace the selected conversation', async () => {
  const slow = deferred();
  const api = async path => path === '/projects/old' ? slow.promise : { id: 'new', name: 'New', messages: [], specifications: [] };
  const store = new WorkspaceController({ api, storage: null });
  const first = store.selectProject('old');
  await store.selectProject('new');
  slow.resolve({ id: 'old', name: 'Old', messages: [], specifications: [] });
  await first;
  assert.equal(store.getSnapshot().project.id, 'new');
  assert.equal(store.getSnapshot().projectId, 'new');
});

test('historical specification without a pipeline context never shows the current draft', async () => {
  const db = backend();
  db.projects.push({ id: 'p1', name: 'P', messages: [], analyst: { current_draft: { steps: [{id:'latest'}] } }, specifications: [{ id: 'old' }] });
  db.specs.set('old', { id: 'old', version: 1, status: 'APPROVED', context_json: null, verifications: [], artifacts: [] });
  const store = new WorkspaceController({ api: db.api, storage: null });
  await store.selectProject('p1');
  assert.equal(selectWorkspace(store.getSnapshot()).pipeline, null);
  await store.selectSpecification(null);
  assert.equal(selectWorkspace(store.getSnapshot()).pipeline.steps[0].id, 'latest');
});

test('ambiguous generation failure keeps UUID, recorded failure rotates it on next action', async () => {
  const db = backend();
  db.projects.push({ id: 'p1', name: 'P', messages: [], specifications: [{id:'s1'}] });
  db.specs.set('s1', verified());
  let sequence = 0;
  const store = new WorkspaceController({ api: db.api, storage: null, uuid: () => `request-${++sequence}` });
  await store.selectProject('p1');
  db.failGeneration(new TypeError('Network response lost'));
  await store.generate();
  assert.equal(store.getSnapshot().generated, null);
  db.failGeneration(Object.assign(new Error('Saved provider failure'), { code: 'LLM_TRANSIENT_ERROR' }));
  await store.generate();
  await store.generate();
  assert.deepEqual(db.attempts, ['request-1', 'request-1', 'request-2']);
  assert.equal(store.getSnapshot().generated.spec_id, 's1');
});

test('generation action is blocked for queued verification and unapproved specifications', async () => {
  const db = backend();
  db.projects.push({ id: 'p1', name: 'P', messages: [], specifications: [{id:'s1'}] });
  const spec = verified();
  spec.verifications.push({ id: 'run2', status: 'QUEUED' });
  db.specs.set('s1', spec);
  const store = new WorkspaceController({ api: db.api, storage: null });
  await store.selectProject('p1');
  assert.equal(selectWorkspace(store.getSnapshot()).verified, false);
  await store.generate();
  assert.equal(db.attempts.length, 0);
});

test('new dialogue clears selected version, draft text and stored selection', async () => {
  const db = backend();
  db.projects.push({ id: 'p1', name: 'P', messages: [], specifications: [] });
  const values = new Map();
  const storage = { getItem: key => values.get(key), setItem: (key,value) => values.set(key,value), removeItem: key => values.delete(key) };
  const store = new WorkspaceController({ api: db.api, storage });
  await store.selectProject('p1');
  store.setText('old unsent text');
  store.newConversation();
  assert.equal(store.getSnapshot().projectId, null);
  assert.equal(store.getSnapshot().specId, null);
  assert.equal(store.getSnapshot().text, '');
  assert.equal(values.size, 0);
});

test('an acknowledged message is never reposted after history refresh fails', async () => {
  const db = backend();
  let failRead = true;
  const api = async (path, options) => {
    if (path === '/projects/p1' && failRead) { failRead = false; throw new TypeError('Read failed after committed turn'); }
    return db.api(path, options);
  };
  const store = new WorkspaceController({ api, storage: null });
  store.setText('Согласованные данные');
  await store.send();
  assert.equal(store.getSnapshot().text, '');
  await store.send();
  assert.equal(db.projects[0].messages.filter(m => m.role === 'user').length, 1);
});

test('storage acknowledgment failure keeps the generation UUID for recovery', async () => {
  const db = backend();
  db.projects.push({id:'p1',name:'P',messages:[],specifications:[{id:'s1'}]});
  db.specs.set('s1',verified());
  let number = 0;
  const store = new WorkspaceController({ api:db.api,storage:null,uuid:()=>`request-${++number}` });
  await store.selectProject('p1');
  db.failGeneration(Object.assign(new Error('Storage ACK failed'), {code:'STORAGE_ERROR'}));
  await store.generate();
  await store.generate();
  assert.deepEqual(db.attempts,['request-1','request-1']);
});

test('a delayed project list preserves conversations created while the list was loading', async () => {
  const db=backend();
  const oldList=deferred();
  const api=(path,options)=>path==='/projects' && !options ? oldList.promise : db.api(path,options);
  const store=new WorkspaceController({api,storage:null});
  const loading=store.loadProjects();
  store.setText('Новый диалог');
  await store.send();
  oldList.resolve([{id:'older',name:'Older project'}]);
  await loading;
  assert.deepEqual(new Set(store.getSnapshot().projects.map(p=>p.id)),new Set(['p1','older']));
});

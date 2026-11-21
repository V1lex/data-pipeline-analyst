import { request, errorText } from '../api/client.js';

const SELECTION_KEY = 'pipeline:last-conversation';

function conversationTitle(content) {
  const line = content.split('\n')[0].trim().replace(/\s+/g, ' ');
  if (line.length <= 44) return line;
  const prefix = line.slice(0, 44);
  const boundary = prefix.lastIndexOf(' ');
  return (boundary > 20 ? prefix.slice(0, boundary) : prefix).trimEnd() + '…';
}

export function selectWorkspace(state) {
  const snapshot = state.project?.analyst || {};
  const run = state.spec?.verifications?.at(-1);
  const pending = Boolean(run && ['QUEUED', 'RUNNING'].includes(run.status));
  const verdict = run?.result_json;
  return {
    snapshot, run, verdict, pending,
    pipeline: state.specId ? state.spec?.context_json?.pipeline_specification || null : snapshot.current_draft || null,
    verified: state.spec?.status === 'APPROVED' && run?.status === 'FINISHED' && verdict?.status === 'PROPERTY_HOLDS',
    canBuild: snapshot.state === 'draft_complete' && snapshot.last_response?.is_complete === true,
  };
}

export class WorkspaceController {
  constructor({ api = request, storage = null, uuid = () => globalThis.crypto.randomUUID() } = {}) {
    this.api = api;
    this.storage = storage;
    this.uuid = uuid;
    this.listeners = new Set();
    this.projectRead = 0;
    this.projectsRevision = 0;
    this.deletedProjectIds = new Set();
    this.specRead = 0;
    this.generationIds = new Map();
    this.state = {
      projects: [], projectId: null, project: null, specId: null, spec: null,
      text: '', busy: '', error: '', pendingMessage: null, generated: null, attachments: [],
      loadingProject: false, loadingSpec: false,
    };
  }
  getSnapshot = () => this.state;
  subscribe = listener => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  _set(patch) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()); }
  _remember(id) {
    try { if (id) this.storage?.setItem(SELECTION_KEY, id); else this.storage?.removeItem(SELECTION_KEY); } catch { /* storage may be unavailable */ }
  }
  setText(value) { this._set({ text: value }); }
  attachFiles(files) {
    if (this.state.busy) return;
    const accepted = [];
    for (const file of files) {
      if (!/\.(csv|json|jsonl|parquet)$/i.test(file.name) || file.size > 10 * 1024 * 1024) {
        this._set({ error: 'Поддерживаются CSV, JSON, JSONL и Parquet размером до 10 МБ.' });
        continue;
      }
      accepted.push({ id: this.uuid(), file, uploadedId: null });
    }
    this._set({ attachments: [...this.state.attachments, ...accepted] });
  }
  removeAttachment(id) {
    if (!this.state.busy) this._set({ attachments: this.state.attachments.filter(item => item.id !== id) });
  }
  dismissError() { this._set({ error: '' }); }
  async loadProjects({ restore = false } = {}) {
    const revision = this.projectsRevision;
    try {
      const received = await this.api('/projects');
      const projects = (revision === this.projectsRevision ? received : [...new Map([...received, ...this.state.projects].map(project => [project.id, project])).values()])
        .filter(project => !this.deletedProjectIds.has(project.id));
      this._set({ projects });
      let previous;
      try { previous = restore && this.storage?.getItem(SELECTION_KEY); } catch { /* optional local selection */ }
      if (previous && !this.state.projectId && projects.some(project => project.id === previous)) await this.selectProject(previous);
    } catch (error) { this._set({ error: errorText(error) }); }
  }
  async selectProject(id) {
    if (this.state.busy) return;
    const read = ++this.projectRead;
    const revision = this.projectsRevision;
    ++this.specRead;
    this._remember(id);
    this._set({ projectId: id, project: null, specId: null, spec: null, generated: null,
      text: '', error: '', pendingMessage: null, attachments: [], loadingProject: Boolean(id), loadingSpec: false });
    if (!id) return;
    try {
      const project = await this.api(`/projects/${id}`);
      if (read !== this.projectRead) return;
      if (revision !== this.projectsRevision) {
        const current = this.state.projects.find(item => item.id === id);
        if (current) project.name = current.name;
      }
      this._set({ project, loadingProject: false });
      await this.selectSpecification(project.specifications?.at(-1)?.id || null);
    } catch (error) {
      if (read === this.projectRead) this._set({ error: errorText(error), loadingProject: false });
    }
  }
  newConversation() { if (!this.state.busy) void this.selectProject(null); }
  async refreshProjectSnapshot(id) {
    const project = await this.api(`/projects/${id}`);
    if (this.state.projectId === id) this._set({ project });
  }
  async renameProject(id, name) {
    name = name.trim();
    if (!name || name.length > 200) {
      this._set({ error: 'Название должно содержать от 1 до 200 символов.' });
      return false;
    }
    return Boolean(await this._perform('Сохраняем название…', async () => {
      const renamed = await this.api(`/projects/${id}`, { method: 'PATCH', body: { name } });
      ++this.projectsRevision;
      this._set({
        projects: this.state.projects.map(project => project.id === id ? { ...project, name: renamed.name } : project),
        project: this.state.project?.id === id ? { ...this.state.project, name: renamed.name } : this.state.project,
      });
      return true;
    }));
  }
  async deleteProject(id) {
    return Boolean(await this._perform('Удаляем диалог…', async () => {
      await this.api(`/projects/${id}`, { method: 'DELETE' });
      ++this.projectsRevision;
      this.deletedProjectIds.add(id);
      const patch = { projects: this.state.projects.filter(project => project.id !== id) };
      if (this.state.projectId === id) {
        ++this.projectRead;
        ++this.specRead;
        this._remember(null);
        Object.assign(patch, { projectId: null, project: null, specId: null, spec: null,
          generated: null, text: '', pendingMessage: null, loadingProject: false, loadingSpec: false });
      }
      this._set(patch);
      return true;
    }));
  }
  async selectSpecification(id) {
    const read = ++this.specRead;
    const projectId = this.state.projectId;
    this._set({ specId: id, spec: null, generated: null, loadingSpec: Boolean(id) });
    if (!id) return;
    try {
      const spec = await this.api(`/specifications/${id}`);
      if (read !== this.specRead || projectId !== this.state.projectId) return;
      if (spec.project_id && spec.project_id !== projectId) throw new Error('Эта версия принадлежит другому диалогу.');
      this._set({ spec, loadingSpec: false });
    } catch (error) {
      if (read === this.specRead && projectId === this.state.projectId) this._set({ error: errorText(error), loadingSpec: false });
    }
  }
  async _perform(label, action) {
    if (this.state.busy) return null;
    this._set({ busy: label, error: '' });
    try { return await action(); }
    catch (error) { this._set({ error: errorText(error) }); return null; }
    finally { this._set({ busy: '', pendingMessage: null }); }
  }
  async send() {
    const content = this.state.text.trim() || (this.state.attachments.length ? 'Проанализируй прикреплённые файлы.' : '');
    if (!content || this.state.busy || this.state.loadingProject) return null;
    return this._perform('Аналитик отвечает…', async () => {
      this._set({ pendingMessage: { content, attachments: this.state.attachments.map(item => ({ id: item.id, filename: item.file.name })) } });
      let id = this.state.projectId;
      if (!id) {
        const name = conversationTitle(content);
        const created = await this.api('/projects', { method: 'POST', body: { name } });
        id = created.id;
        ++this.projectRead;
        ++this.projectsRevision;
        this._remember(id);
        this._set({ projectId: id, project: { ...created, messages: [], specifications: [] }, projects: [created, ...this.state.projects] });
      }
      if (this.state.attachments.length) {
        for (const item of this.state.attachments) {
          if (item.uploadedId) continue;
          const body = new FormData(); body.append('file', item.file); body.append('request_id', item.id);
          const uploaded = await this.api(`/projects/${id}/sources`, { method: 'POST', body });
          this._set({ attachments: this.state.attachments.map(entry => entry.id === item.id ? { ...entry, uploadedId: uploaded.id } : entry) });
        }
        const selected = await this.api(`/projects/${id}/sources/selection`, { method: 'GET' });
        const ids = [...new Set([...selected.map(item => item.id), ...this.state.attachments.map(item => item.uploadedId)])];
        await this.api(`/projects/${id}/sources/selection`, { method: 'PUT', body: { file_ids: ids } });
      }
      const attached = this.state.attachments.map(item => ({ id: item.uploadedId, filename: item.file.name }));
      const turn = await this.api(`/projects/${id}/messages`, { method: 'POST', body: { content, file_ids: attached.map(item => item.id) } });
      // The server acknowledged this turn. A later read failure must never offer
      // the same text as an unsent message and accidentally post it twice.
      const previous = this.state.project;
      const messages = [...(previous?.messages || []), { id: `${id}:ack-user:${previous?.messages?.length || 0}`, role: 'user', content, attachments: attached }];
      if (turn.response?.message) messages.push({ id: `${id}:ack-assistant:${messages.length}`, role: 'assistant', content: turn.response.message });
      ++this.specRead;
      this._set({ text: '', attachments: [], pendingMessage: null, specId: null, spec: null, generated: null,
        project: { ...previous, messages, analyst: turn.response ? { ...previous?.analyst, last_response: turn.response, state: turn.state } : previous?.analyst } });
      const project = await this.api(`/projects/${id}`);
      this._set({ project, text: '', pendingMessage: null, projects: this.state.projects.map(item => item.id === id ? { ...item, name: project.name } : item) });
      return project;
    });
  }
  async openRevision(spec) {
    const projectId = this.state.projectId;
    ++this.specRead;
    this._set({ specId: spec.id, spec: { ...spec, verifications: [], artifacts: [] }, generated: null, loadingSpec: false });
    try { await this.refreshProjectSnapshot(projectId); }
    catch (error) { this._set({ error: errorText(error) }); }
  }
  async build() {
    if (!this.state.projectId || !selectWorkspace(this.state).canBuild) return null;
    return this._perform('Готовим спецификацию…', async () => {
      const id = this.state.projectId;
      const spec = await this.api(`/projects/${id}/specifications/build`, { method: 'POST' });
      const project = await this.api(`/projects/${id}`);
      ++this.specRead;
      this._set({ project, specId: spec.id, spec: { ...spec, verifications: [], artifacts: [] }, generated: null });
      return spec;
    });
  }
  async versionAction(type) {
    const id = this.state.specId;
    if (!id || !this.state.spec || this.state.loadingSpec) return null;
    if (type === 'verify' && (this.state.spec.status !== 'APPROVED' || selectWorkspace(this.state).pending)) return null;
    return this._perform(type === 'approve' ? 'Согласуем спецификацию…' : 'Запускаем проверку…', async () => {
      await this.api(`/specifications/${id}/${type}`, { method: 'POST' });
      if (type === 'verify') {
        for (const key of this.generationIds.keys()) if (key.startsWith(`${id}:`)) this.generationIds.delete(key);
        this._set({ generated: null });
      }
      const spec = await this.api(`/specifications/${id}`);
      const project = await this.api(`/projects/${this.state.projectId}`);
      this._set({ spec, project });
      return spec;
    });
  }
  async refreshVerification() {
    const { specId, projectId } = this.state;
    const read = this.specRead;
    if (!specId || !selectWorkspace(this.state).pending) return;
    try {
      const spec = await this.api(`/specifications/${specId}`);
      if (this.state.specId === specId && this.state.projectId === projectId && this.specRead === read) this._set({ spec });
    } catch (error) {
      if (this.state.specId === specId && this.specRead === read) this._set({ error: errorText(error) });
    }
  }
  async generate() {
    const view = selectWorkspace(this.state);
    const specId = this.state.specId;
    if (!specId || !view.verified || this.state.loadingSpec) return null;
    return this._perform('Готовим код…', async () => {
      const key = `${specId}:${view.run.id}`;
      if (!this.generationIds.has(key) || this.state.generated) this.generationIds.set(key, this.uuid());
      let generated;
      try {
        generated = await this.api(`/specifications/${specId}/generate`, { method: 'POST', body: { request_id: this.generationIds.get(key) } });
      } catch (error) {
        if (error.code && !['GENERATION_IN_PROGRESS', 'VERIFICATION_REQUIRED', 'STORAGE_ERROR'].includes(error.code)) this.generationIds.delete(key);
        throw error;
      }
      this._set({ generated });
      // A failed history refresh must not hide the already returned saved file.
      try { this._set({ spec: await this.api(`/specifications/${specId}`) }); }
      catch (error) { this._set({ error: errorText(error) }); }
      return generated;
    });
  }
}

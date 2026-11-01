import { useEffect, useRef, useState } from 'react';
import { request, artifactUrl, errorText } from './api/client';
import PipelineGraph from './components/PipelineGraph';

const statusName = { DRAFT: 'На согласовании', APPROVED: 'Согласована', QUEUED: 'В очереди', RUNNING: 'Проверяется', FINISHED: 'Проверка завершена', PROPERTY_HOLDS: 'Свойства выполнены', PROPERTY_VIOLATED: 'Нарушено свойство', MODEL_INVALID: 'Ошибка модели', TIMEOUT: 'Превышено время', ENGINE_ERROR: 'Ошибка TLC', INFRASTRUCTURE_ERROR: 'Ошибка окружения', draft_complete: 'Требования собраны', awaiting_approval: 'Нужно согласование', insufficient_data: 'Уточняем требования' };
const name = status => statusName[status] || status || 'Ожидает требований';

export default function App() {
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(null);
  const [project, setProject] = useState(null);
  const [specId, setSpecId] = useState(null);
  const [spec, setSpec] = useState(null);
  const [text, setText] = useState('');
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [tab, setTab] = useState('graph');
  const [code, setCode] = useState(null);
  const generationIds = useRef({});
  const chatEnd = useRef(null);

  useEffect(() => { request('/projects').then(setProjects).catch(e => setError(errorText(e))); }, []);
  useEffect(() => {
    let alive = true;
    setProject(null); setSpec(null); setSpecId(null); setCode(null); setError('');
    if (projectId) request(`/projects/${projectId}`).then(p => { if (alive) { setProject(p); setSpecId(p.specifications.at(-1)?.id || null); } }).catch(e => alive && setError(errorText(e)));
    return () => { alive = false; };
  }, [projectId]);
  useEffect(() => {
    let alive = true;
    setSpec(null); setCode(null);
    if (specId) request(`/specifications/${specId}`).then(s => alive && setSpec(s)).catch(e => alive && setError(errorText(e)));
    return () => { alive = false; };
  }, [specId]);
  const run = spec?.verifications?.at(-1);
  const pending = run && ['QUEUED', 'RUNNING'].includes(run.status);
  useEffect(() => {
    if (!pending || !specId) return;
    let alive = true;
    const timer = setInterval(() => request(`/specifications/${specId}`).then(s => alive && setSpec(s)).catch(e => alive && setError(errorText(e))), 1800);
    return () => { alive = false; clearInterval(timer); };
  }, [specId, pending]);
  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [project?.messages?.length, busy]);

  async function action(label, fn) {
    setBusy(label); setError('');
    try { await fn(); } catch (e) { setError(errorText(e)); } finally { setBusy(''); }
  }
  async function refreshProject() {
    const p = await request(`/projects/${projectId}`); setProject(p); return p;
  }
  async function create(event) {
    event.preventDefault(); if (!newName.trim()) return;
    await action('Создаём проект', async () => {
      const p = await request('/projects', { method: 'POST', body: { name: newName.trim() } });
      setProjects(await request('/projects')); setProjectId(p.id); setNewName(''); setCreating(false);
    });
  }
  async function send(event) {
    event.preventDefault(); if (!text.trim() || busy) return;
    await action('Аналитик отвечает…', async () => {
      await request(`/projects/${projectId}/messages`, { method: 'POST', body: { content: text.trim() } });
      setText(''); await refreshProject();
    });
  }
  async function build() {
    await action('Создаём версию…', async () => {
      const result = await request(`/projects/${projectId}/specifications/build`, { method: 'POST' });
      await refreshProject(); setSpecId(result.id); setTab('graph');
    });
  }
  async function versionAction(type) {
    await action(type === 'approve' ? 'Согласуем…' : 'Ставим на проверку…', async () => {
      await request(`/specifications/${specId}/${type}`, { method: 'POST' });
      setSpec(await request(`/specifications/${specId}`));
      await refreshProject(); if (type === 'verify') { delete generationIds.current[specId]; setCode(null); setTab('verification'); }
    });
  }
  async function generate() {
    await action('Генерируем PySpark…', async () => {
      if (!generationIds.current[specId] || code) generationIds.current[specId] = crypto.randomUUID();
      let result;
      try {
        result = await request(`/specifications/${specId}/generate`, { method: 'POST', body: { request_id: generationIds.current[specId] } });
      } catch (error) {
        // A known failed/expired attempt requires a new explicit user action.
        // An ambiguous network response keeps the UUID for a safe retry.
        if (error.code && !['GENERATION_IN_PROGRESS', 'VERIFICATION_REQUIRED'].includes(error.code)) delete generationIds.current[specId];
        throw error;
      }
      setCode(result); setTab('code'); setSpec(await request(`/specifications/${specId}`));
    });
  }
  const snapshot = project?.analyst || {};
  const lastResponse = snapshot.last_response;
  const pipeline = specId ? spec?.context_json?.pipeline_specification : snapshot.current_draft;
  const verdict = run?.result_json;
  const verified = run?.status === 'FINISHED' && verdict?.status === 'PROPERTY_HOLDS';
  const states = verdict?.counterexample?.steps || [];

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="Pipeline Analyst"><span className="brand-mark">P<span>·</span></span><div>Pipeline<span>Analyst</span></div></a>
      <div className="workspace-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <div className="project-heading"><span>Проекты <i>{projects.length}</i></span><button className="icon-button" onClick={() => setCreating(!creating)} aria-label="Новый проект">＋</button></div>
      {creating && <form className="new-project-form" onSubmit={create}><label className="sr-only" htmlFor="project-name">Название проекта</label><input id="project-name" autoFocus placeholder="Название проекта" value={newName} onChange={e => setNewName(e.target.value)} maxLength={200}/><button disabled={!!busy || !newName.trim()}>Создать</button></form>}
      <nav className="project-list" aria-label="Проекты">{projects.map(p => <button key={p.id} className={p.id === projectId ? 'selected' : ''} onClick={() => setProjectId(p.id)} disabled={!!busy}><span>◇</span><div>{p.name}</div></button>)}</nav>
      {!projects.length && <p className="sidebar-empty">Создайте проект, чтобы начать работу с конвейером.</p>}
      <div className="sidebar-footer"><span className="online-dot"/>Локальное рабочее пространство<div>MVP · FastAPI / TLC / PySpark</div></div>
    </aside>
    <main className="main">
      <header className="topbar"><div className="breadcrumb">Рабочее пространство <span>/</span> <strong>{project?.name || 'Обзор'}</strong></div><span className="mvp-label">MVP <span>0.2</span></span></header>
      {error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setError('')} aria-label="Закрыть ошибку">×</button></div>}
      {!projectId ? <section className="welcome"><div className="eyebrow">ОТ ТРЕБОВАНИЙ К КОНВЕЙЕРУ</div><h1>Опишите задачу.<br/><span>Соберите решение.</span></h1><p>Уточните требования с аналитиком, проверьте модель процесса и получите PySpark для согласованной версии.</p><button className="primary" onClick={() => setCreating(true)}>＋ Создать проект</button><div className="welcome-steps"><span><b>01</b> Уточнение</span><span><b>02</b> Спецификация</span><span><b>03</b> Проверка TLC</span><span><b>04</b> Код PySpark</span></div></section> : !project ? <div className="loading">Открываем проект…</div> : <>
        <section className="project-title"><div><div className="eyebrow">КОНВЕЙЕР ДАННЫХ</div><h1>{project.name}</h1><p>Требования, модель процесса и код — в одном проекте.</p></div><span className={`status-pill ${verified ? 'positive' : ''}`}>{verified ? '● ' : '○ '}{verified ? name(verdict.status) : name(snapshot.state)}</span></section>
        <div className="workflow-strip">{['Требования', 'Спецификация', 'Проверка TLC', 'PySpark'].map((label, i) => <div key={label} className={(i === 0 || (i === 1 && spec) || (i === 2 && run) || (i === 3 && spec?.artifacts?.length)) ? 'active' : ''}><span>{String(i + 1).padStart(2, '0')}</span>{label}</div>)}</div>
        <div className="workspace-grid">
          <section className="panel chat-panel"><div className="panel-heading"><h2>Диалог с аналитиком</h2><span className="small-tag">GEMINI</span></div><div className="chat-scroll">
            {!project.messages.length && <div className="chat-intro"><div className="analyst-avatar">✳</div><h3>Начнём с вашей задачи</h3><p>Какие данные нужно обработать и какой результат вы хотите получить?</p><button className="example-prompt" onClick={() => setText('Посчитай выручку по городам. Помоги уточнить источники, формулу и результат.')}>Например: выручка по городам ↗</button></div>}
            {project.messages.map((m, i) => <div key={m.id} className={`chat-message ${m.role}`}><div className="message-label">{m.role === 'user' ? 'Вы' : '✳ Аналитик'}</div><div className="message-content">{m.content}</div>{i === project.messages.length - 1 && m.role === 'assistant' && lastResponse?.clarification_questions?.length > 0 && <ul className="questions">{lastResponse.clarification_questions.map((q, index) => <li key={index}><strong>{q.question}</strong>{q.suggestion && <span>Предложение: {q.suggestion}</span>}</li>)}</ul>}</div>)}
            {busy && <div className="thinking" role="status"><span className="pulse-dot"/>{busy}</div>}<div ref={chatEnd}/>
          </div><form className="chat-form" onSubmit={send}><label className="sr-only" htmlFor="message">Сообщение аналитику</label><textarea id="message" placeholder="Опишите задачу или ответьте на вопросы…" value={text} onChange={e => setText(e.target.value)} rows={3} maxLength={30000} disabled={!!busy} onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send(e); }}/><div><span>⌘ / Ctrl + Enter — отправить</span><button className="send-button" disabled={!!busy || !text.trim()} aria-label="Отправить сообщение">↑</button></div></form></section>
          <section className="panel model-panel"><div className="panel-heading"><div><h2>Модель конвейера</h2><span className="panel-subtitle">{spec ? `Версия ${spec.version} · ${name(spec.status)}` : 'Черновик из диалога'}</span></div><select aria-label="Версия спецификации" value={specId || ''} onChange={e => setSpecId(e.target.value || null)} disabled={!!busy}><option value="">Текущий черновик</option>{project.specifications.map(s => <option key={s.id} value={s.id}>Версия {s.version} · {name(s.status)}</option>)}</select></div>
            <div className="tabs" role="tablist">{[['graph','Граф'],['json','Спецификация'],['verification','Проверка'],['code','PySpark']].map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)} className={tab === id ? 'active' : ''}>{label}{id === 'verification' && pending && <span className="pulse-dot"/>}</button>)}</div>
            <div className="model-body">
              {tab === 'graph' && <><div className="graph-container"><PipelineGraph specification={pipeline}/></div><div className="graph-legend"><span><i className="source-key"/> Источник</span><span><i/> Преобразование</span><span><i className="sink-key"/> Результат</span><span className="graph-count">{pipeline?.steps?.length || 0} шагов</span></div></>}
              {tab === 'json' && <div className="json-view"><h3>{spec ? `Спецификация · версия ${spec.version}` : 'Рабочий черновик'}</h3><p>Проверьте источники, формулы и модель перед согласованием версии.</p><pre>{JSON.stringify(spec?.context_json?.pipeline_specification || pipeline || {}, null, 2)}</pre></div>}
              {tab === 'verification' && <div className="verification-view">{!run ? <div className="empty-state"><span>◎</span><h3>Модель ещё не проверялась</h3><p>Согласуйте спецификацию и запустите проверку TLC.</p></div> : <><div className={`result-card ${verified ? 'success' : verdict?.status === 'PROPERTY_VIOLATED' ? 'violation' : ''}`}><div className="eyebrow">TLA+ / TLC · ЗАПУСК {run.id.slice(0, 8)}</div><h3>{name(verdict?.status || run.status)}</h3>{verdict?.property_id && <p>Свойство: <code>{verdict.property_id}</code></p>}{verdict?.error && <p>{typeof verdict.error === 'string' ? verdict.error : JSON.stringify(verdict.error)}</p>}{pending && <p>Воркер выполняет проверку. Результат обновится автоматически.</p>}{verdict?.statistics && <div className="stats">{Object.entries(verdict.statistics).map(([k,v]) => <div key={k}><b>{v}</b><span>{({states_generated:'Состояний',distinct_states:'Уникальных',depth:'Глубина'})[k] || k}</span></div>)}</div>}</div>{states.length > 0 && <div className="trace"><h3>Контрпример</h3>{states.map((s,i) => <div className="trace-step" key={i}><span>{s.number}</span><div><strong>{s.transition || 'Начальное состояние'}</strong><pre>{JSON.stringify(s.state, null, 2)}</pre></div></div>)}</div>}{verdict?.errors && <pre>{JSON.stringify(verdict.errors, null, 2)}</pre>}<details><summary>История проверок · {spec.verifications.length}</summary>{spec.verifications.map(r => <p key={r.id}>{new Date(r.queued_at).toLocaleString('ru-RU')} — {name(r.result_json?.status || r.status)}</p>)}</details></>}</div>}
              {tab === 'code' && <div className="code-view">{code ? <><h3>{code.filename}</h3><p>{code.answer}</p><pre>{code.content}</pre><a className="primary download" href={artifactUrl(specId, code.artifact_id)}>↓ Скачать .py</a></> : <div className="empty-state"><span>⌘</span><h3>Код для проверенной версии</h3><p>После успешного TLC модель подготовит PySpark по согласованным требованиям.</p></div>}{spec?.artifacts?.length > 0 && <div className="artifact-list"><h3>Сохранённые файлы</h3>{spec.artifacts.map(a => <a key={a.id} href={artifactUrl(specId, a.id)}>↓ {a.filename}</a>)}</div>}<p className="scope-note">TLC проверяет модель процесса. Корректность сгенерированного Python автоматически не проверяется.</p></div>}
            </div>
            <div className="model-actions"><div>{spec ? <><b>Версия {spec.version}</b><span>{name(spec.status)}</span></> : <><b>Следующий шаг</b><span>Соберите и согласуйте требования в чате</span></>}</div><div className="action-buttons">{snapshot.state === 'draft_complete' && <button className={spec ? 'secondary' : 'primary'} onClick={build} disabled={!!busy}>{spec ? 'Новая версия' : 'Создать спецификацию'} ↗</button>}{spec?.status === 'DRAFT' && <button className="primary" disabled={!!busy} onClick={() => { setTab('json'); versionAction('approve'); }}>Согласовать версию</button>}{spec?.status === 'APPROVED' && <button className="secondary" disabled={!!busy || pending} onClick={() => versionAction('verify')}>{pending ? 'Проверяется…' : 'Проверить TLC'}</button>}{verified && <button className="primary" disabled={!!busy} onClick={generate}>{code ? 'Сгенерировать заново' : 'Получить PySpark'} ↗</button>}</div></div>
          </section>
        </div>
      </>}
      <footer className="main-footer">Pipeline Analyst <span>Спецификации версионируются. История и результаты сохраняются в PostgreSQL.</span></footer>
    </main>
  </div>;
}

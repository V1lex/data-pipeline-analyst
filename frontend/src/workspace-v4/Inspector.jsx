import { useEffect, useRef, useState } from 'react';
import PipelineGraph from '../components/PipelineGraph.jsx';
import { artifactUrl, request } from '../api/client.js';
import { Icon } from './icons.jsx';

const names = { DRAFT: 'На согласовании', APPROVED: 'Согласована', QUEUED: 'В очереди', RUNNING: 'Проверяется', PROPERTY_HOLDS: 'Модель проверена', PROPERTY_VIOLATED: 'Найдено нарушение', PROPERTY_UNREACHABLE: 'Состояние недостижимо', MODEL_INVALID: 'Нужно исправить модель', TIMEOUT: 'Проверка не завершилась вовремя', INFRASTRUCTURE_ERROR: 'Проверка недоступна', STATE_LIMIT_REACHED: 'Достигнут лимит состояний' };
export const statusLabel = status => names[status] || status || '';

export function VersionPicker({ state, onSelect }) {
  const versions = state.project?.specifications || [];
  if (!versions.length) return null;
  return <div className="pa-version-picker"><label htmlFor="specification-version">Спецификация</label><select id="specification-version" value={state.specId || ''} disabled={Boolean(state.busy || state.loadingSpec)} onChange={event => onSelect(event.target.value || null)}><option value="">Текущий черновик</option>{versions.map(item => <option key={item.id} value={item.id}>Версия {item.version} · {statusLabel(item.status)}</option>)}</select></div>;
}

export function SchemaPanel({ state, view, onBuild, onApprove, onVerify, onOpenVerification }) {
  const [tab, setTab] = useState('graph');
  const disabled = Boolean(state.busy || state.loadingSpec);
  return <div className="pa-inspector">
    <div className="pa-inspector-tabs" role="tablist" aria-label="Представление спецификации">
      <button type="button" role="tab" aria-selected={tab === 'graph'} onClick={() => setTab('graph')}>Схема</button>
      <button type="button" role="tab" aria-selected={tab === 'json'} onClick={() => setTab('json')}>Спецификация</button>
    </div>
    {state.loadingSpec ? <p role="status">Открываем спецификацию…</p> : tab === 'graph' ? <div className="pa-graph"><PipelineGraph specification={view.pipeline}/></div> : <div className="pa-specification-json"><pre>{JSON.stringify(view.pipeline || state.spec?.spec_json || {}, null, 2)}</pre></div>}
    {!state.specId && !view.pipeline && <p className="pa-inspector-muted">Схема появится, когда уточним требования в диалоге.</p>}
    {state.spec && <p className="pa-inspector-muted">{statusLabel(state.spec.status)}</p>}
    <div className="pa-inspector-actions">
      {view.canBuild && !state.specId && <button className={state.spec ? 'pa-button' : 'pa-button pa-button--primary'} onClick={onBuild} disabled={disabled}>{state.spec ? 'Новая спецификация' : 'Собрать спецификацию'}</button>}
      {state.spec?.status === 'DRAFT' && <button className="pa-button pa-button--primary" disabled={disabled} onClick={onApprove}>Согласовать</button>}
      {state.spec?.status === 'APPROVED' && <button className="pa-button" disabled={disabled || view.pending} onClick={onVerify}>{view.pending ? 'Проверяем модель…' : 'Проверить модель'}</button>}
      {view.run && <button className="pa-text-button" onClick={onOpenVerification}>Результат проверки</button>}
    </div>
  </div>;
}

export function VerificationPanel({ state, view, onVerify, onGenerate }) {
  const run = view.run;
  const result = view.verdict;
  const [explanation, setExplanation] = useState(null);
  const [explanationError, setExplanationError] = useState('');
  useEffect(() => {
    let active = true;
    setExplanation(null);
    setExplanationError('');
    if (state.specId && run?.id && run.status === 'FINISHED') {
      request(`/specifications/${state.specId}/verifications/${run.id}/explanation`)
        .then(data => { if (active) setExplanation(data); })
        .catch(error => { if (active) setExplanationError(error.message); });
    }
    return () => { active = false; };
  }, [state.specId, run?.id, run?.status]);
  if (!run) return <p className="pa-inspector-muted">Согласуйте спецификацию и запустите проверку модели.</p>;
  const trace = result?.counterexample?.steps || [];
  return <div className="pa-inspector pa-verification">
    <div className={`pa-verification-summary${view.verified ? ' pa-verification-summary--success' : ''}`} role="status">
      {view.verified && <Icon name="check" size={20}/>}
      <strong>{statusLabel(result?.status || run.status)}</strong>
    </div>
    {view.pending && <p>Результат появится здесь автоматически.</p>}
    {explanation && <section aria-label="Объяснение проверки"><h3>{explanation.title}</h3><p>{explanation.summary}</p></section>}
    {explanationError && <p className="pa-inspector-error">Объяснение недоступно: {explanationError}</p>}
    {result?.property_id && <p>Свойство: <code>{result.property_id}</code></p>}
    {result?.error && <p className="pa-inspector-error">{typeof result.error === 'string' ? result.error : JSON.stringify(result.error)}</p>}
    {result?.errors?.length > 0 && <ul className="pa-validation-errors">{result.errors.map((error, index) => <li key={index}><code>{error.location}</code> — {error.message}</li>)}</ul>}
    {trace.length > 0 && <div className="pa-trace"><h3>Контрпример</h3>{trace.map((step,index) => <details key={index} open={index === 0}><summary>{step.transition || 'Начальное состояние'}</summary><pre>{JSON.stringify(step.state,null,2)}</pre></details>)}</div>}
    <div className="pa-inspector-actions">
      {view.verified && <button className="pa-button pa-button--primary" disabled={Boolean(state.busy)} onClick={onGenerate}>{state.generated ? 'Сгенерировать заново' : 'Получить код'}</button>}
      {!view.pending && <button className="pa-button" disabled={Boolean(state.busy)} onClick={onVerify}>Повторить проверку</button>}
    </div>
    {(result || state.spec?.verifications?.length > 1) && <details className="pa-technical-details"><summary>Подробности проверки</summary>
      {result?.statistics && <dl>{Object.entries(result.statistics).map(([key,value]) => <div key={key}><dt>{({states_generated:'Состояний',distinct_states:'Уникальных состояний',depth:'Глубина'})[key] || key}</dt><dd>{value}</dd></div>)}</dl>}
      <p className="pa-inspector-muted">Проверена модель процесса. Это не проверка сгенерированного Python.</p>
      {state.spec?.verifications?.map(item => <p key={item.id}>{new Date(item.queued_at).toLocaleString('ru-RU')} · {statusLabel(item.result_json?.status || item.status)}</p>)}
    </details>}
  </div>;
}

export function FilesPanel({ state, view, onOpenSchema, onOpenVerification, onGenerate }) {
  const [preview, setPreview] = useState(null);
  const [content, setContent] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [sources, setSources] = useState([]);
  const [sourceError, setSourceError] = useState('');
  const [uploading, setUploading] = useState(false);
  const [sourcePreview, setSourcePreview] = useState(null);
  const [sourceEncoding, setSourceEncoding] = useState('utf-8-sig');
  const [sourceDelimiter, setSourceDelimiter] = useState('');
  const activeProject = useRef(state.projectId);
  activeProject.current = state.projectId;
  useEffect(() => {
    let alive = true;
    setSources([]); setSourcePreview(null); setSourceError(''); setUploading(false);
    if (state.projectId) request(`/projects/${state.projectId}/sources`)
      .then(data => { if (alive) setSources(data); })
      .catch(error => { if (alive) setSourceError(error.message); });
    return () => { alive = false; };
  }, [state.projectId]);
  async function uploadSources(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!state.projectId || !files.length) return;
    const projectId = state.projectId;
    setUploading(true); setSourceError('');
    try {
      for (const file of files) {
        const body = new FormData(); body.append('file', file); body.append('encoding', sourceEncoding);
        if (sourceDelimiter && file.name.toLowerCase().endsWith('.csv')) body.append('delimiter', sourceDelimiter);
        await request(`/projects/${projectId}/sources`, { method: 'POST', body });
      }
      const updated = await request(`/projects/${projectId}/sources`);
      if (activeProject.current === projectId) setSources(updated);
    } catch (error) {
      if (activeProject.current === projectId) {
        setSourceError(error.message);
        try { const updated = await request(`/projects/${projectId}/sources`); if (activeProject.current === projectId) setSources(updated); } catch { /* preserve original error */ }
      }
    } finally { if (activeProject.current === projectId) setUploading(false); }
  }
  const specId = state.spec?.id;
  const generated = state.generated?.spec_id === specId ? state.generated : null;
  const artifacts = new Map((state.spec?.artifacts || []).filter(file => file.spec_id === specId).map(file => [file.id,file]));
  if (generated) artifacts.set(generated.artifact_id,{id:generated.artifact_id,spec_id:generated.spec_id,filename:generated.filename});
  useEffect(() => {
    if (!preview || !specId) return;
    let alive = true;
    setLoading(true); setContent(''); setError('');
    if (generated?.artifact_id === preview.id) { setContent(generated.content); setLoading(false); return; }
    fetch(artifactUrl(specId,preview.id)).then(response => {
      if (!response.ok) throw new Error('Не удалось открыть файл. Попробуйте скачать его ещё раз.');
      return response.text();
    }).then(text => { if (alive) setContent(text); }).catch(error => alive && setError(error.message)).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  },[preview?.id,specId,generated?.artifact_id]);
  if (sourcePreview) return <div className="pa-file-preview"><button className="pa-text-button" onClick={() => setSourcePreview(null)}>← К файлам</button><h3>{sourcePreview.filename}</h3><p>Профиль по выборке: {sourcePreview.profile.sampled_rows} строк. Типы являются предварительными, кроме схемы Parquet.</p><h4>Колонки</h4><ul>{sourcePreview.profile.columns.map(column => <li key={column.name}>{column.name} · {column.type} · пропусков в выборке: {column.null_count}</li>)}</ul><h4>Образец строк</h4><pre>{JSON.stringify(sourcePreview.profile.sample, null, 2)}</pre>{sourcePreview.profile.warnings?.map(warning => <p key={warning}>{warning}</p>)}</div>;
  if (preview) return <div className="pa-file-preview"><button className="pa-text-button" onClick={() => setPreview(null)}>← К файлам</button><h3>{preview.filename}</h3>{loading && <p role="status">Открываем файл…</p>}{error && <p role="alert">{error}</p>}{!loading && !error && <pre>{content}</pre>}<a className="pa-button" href={artifactUrl(specId,preview.id)}><Icon name="download" size={16}/>Скачать .py</a></div>;
  return <div className="pa-files">
    <h3>Исходные данные</h3>
    {state.projectId ? <label className="pa-button">{uploading ? 'Загружаем…' : 'Загрузить файлы'}<input type="file" accept=".csv,.json,.jsonl,.parquet" multiple disabled={uploading} onChange={uploadSources} style={{position:'absolute',width:1,height:1,opacity:0}} /></label> : <p className="pa-files-empty">Начните диалог, чтобы загрузить файлы.</p>}
    {state.projectId && <details><summary>Параметры чтения</summary><label>Кодировка <select value={sourceEncoding} onChange={event => setSourceEncoding(event.target.value)}><option value="utf-8-sig">UTF-8</option><option value="cp1251">Windows-1251</option></select></label><label> Разделитель CSV <select value={sourceDelimiter} onChange={event => setSourceDelimiter(event.target.value)}><option value="">Определить автоматически</option><option value=",">Запятая</option><option value=";">Точка с запятой</option><option value={'\t'}>Табуляция</option><option value="|">Вертикальная черта</option></select></label></details>}
    {sourceError && <p role="alert" className="pa-inspector-error">{sourceError}</p>}
    {sources.map(source => <button type="button" className="pa-file-row" key={source.id} onClick={() => setSourcePreview(source)}><Icon name="file" size={24}/><span>{source.filename}</span><small>Профиль</small></button>)}
    <h3>Артефакты</h3>
    {(state.spec || view.pipeline) && <button className="pa-file-row" type="button" onClick={onOpenSchema}><Icon name="file" size={24}/><span>Спецификация</span><small>Открыть</small></button>}
    {[...artifacts.values()].map(file => <div className="pa-file-row" key={file.id}><Icon name="code" size={24}/><button className="pa-file-name" onClick={() => setPreview(file)}>{file.filename}</button><a className="pa-file-download" href={artifactUrl(file.spec_id,file.id)} aria-label={`Скачать ${file.filename}`} title="Скачать"><Icon name="download" size={20}/></a></div>)}
    {generated?.answer && <p className="pa-generated-answer">{generated.answer}</p>}
    {!artifacts.size && <p className="pa-files-empty">{state.spec ? 'Код появится после проверки модели и генерации.' : 'В этом диалоге пока нет файлов.'}</p>}
    {view.run && <button className="pa-file-row pa-file-row--subtle" type="button" onClick={onOpenVerification}><span>Результат проверки</span><Icon name="chevron-down" size={18}/></button>}
    {view.verified && <button className="pa-button pa-button--primary" disabled={Boolean(state.busy)} onClick={onGenerate}>{generated ? 'Сгенерировать заново' : 'Получить код'}</button>}
  </div>;
}

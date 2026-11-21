import { useEffect, useRef, useState } from 'react';
import PipelineGraph from '../components/PipelineGraph.jsx';
import { artifactUrl, request } from '../api/client.js';
import { Icon } from './icons.jsx';

const names = { DRAFT: 'На согласовании', APPROVED: 'Согласована', QUEUED: 'В очереди', RUNNING: 'Проверяется', PROPERTY_HOLDS: 'Модель проверена', PROPERTY_VIOLATED: 'Найдено нарушение', PROPERTY_UNREACHABLE: 'Состояние недостижимо', MODEL_INVALID: 'Нужно исправить модель', TIMEOUT: 'Проверка не завершилась вовремя', INFRASTRUCTURE_ERROR: 'Проверка недоступна', STATE_LIMIT_REACHED: 'Достигнут лимит состояний' };
export const statusLabel = status => names[status] || status || '';

function ExpandedGraph({ specification, onClose }) {
  const ref = useRef(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog className="pa-graph-dialog" ref={ref} aria-label="Схема конвейера" onClose={onClose}>
    <header className="pa-graph-dialog__header"><div><h2>Схема конвейера</h2><p>Перемещайте схему и меняйте масштаб кнопками или колесом мыши</p></div><button type="button" className="pa-graph-dialog__close" onClick={() => ref.current?.close()} aria-label="Закрыть схему"><Icon name="close" size={22}/></button></header>
    <div className="pa-graph pa-graph--expanded"><PipelineGraph specification={specification} expanded/></div>
  </dialog>;
}

export function VersionPicker({ state, onSelect }) {
  const versions = state.project?.specifications || [];
  if (!versions.length) return null;
  return <div className="pa-version-picker"><label htmlFor="specification-version">Спецификация</label><select id="specification-version" value={state.specId || ''} disabled={Boolean(state.busy || state.loadingSpec)} onChange={event => onSelect(event.target.value || null)}><option value="">Текущий черновик</option>{versions.map(item => <option key={item.id} value={item.id}>Версия {item.version} · {statusLabel(item.status)}</option>)}</select></div>;
}

export function SchemaPanel({ state, view, onBuild, onApprove, onVerify, onOpenVerification }) {
  const [tab, setTab] = useState('graph');
  const [expanded, setExpanded] = useState(false);
  const disabled = Boolean(state.busy || state.loadingSpec);
  return <div className="pa-inspector">
    {state.spec?.status === 'DRAFT' && <p className="pa-inspector-muted">Это план обработки, а не готовый результат. Проверьте его и согласуйте, чтобы запустить проверку модели.</p>}
    <div className="pa-inspector-tabs" role="tablist" aria-label="Представление спецификации">
      <button type="button" role="tab" aria-selected={tab === 'graph'} onClick={() => setTab('graph')}>Схема</button>
      <button type="button" role="tab" aria-selected={tab === 'json'} onClick={() => setTab('json')}>Спецификация</button>
    </div>
    {state.loadingSpec ? <p role="status">Открываем спецификацию…</p> : tab === 'graph' ? <><div className="pa-graph"><PipelineGraph specification={view.pipeline}/></div>{view.pipeline && <button className="pa-graph-expand" type="button" onClick={() => setExpanded(true)}><Icon name="expand" size={17}/>На весь экран</button>}{expanded && <ExpandedGraph specification={view.pipeline} onClose={() => setExpanded(false)}/>}</> : <div className="pa-specification-json"><pre>{JSON.stringify(view.pipeline || state.spec?.spec_json || {}, null, 2)}</pre></div>}
    {!state.specId && !view.pipeline && <p className="pa-inspector-muted">Схема появится, когда уточним требования в диалоге.</p>}
    {state.spec && <p className="pa-inspector-muted">{statusLabel(state.spec.status)}</p>}
    <div className="pa-inspector-actions">
      {view.canBuild && !state.specId && <button className={state.spec ? 'pa-button' : 'pa-button pa-button--primary'} onClick={onBuild} disabled={disabled}>{state.spec ? 'Новая спецификация' : 'Подготовить к проверке'}</button>}
      {state.spec?.status === 'DRAFT' && <button className="pa-button pa-button--primary" disabled={disabled} onClick={onApprove}>Согласовать</button>}
      {state.spec?.status === 'APPROVED' && <button className="pa-button" disabled={disabled || view.pending} onClick={onVerify}>{view.pending ? 'Проверяем модель…' : 'Проверить модель'}</button>}
      {view.run && <button className="pa-text-button" onClick={onOpenVerification}>Результат проверки</button>}
    </div>
  </div>;
}

export function VerificationPanel({ state, view, onVerify, onGenerate, onRevisionCreated }) {
  const run = view.run;
  const result = view.verdict;
  const [explanation, setExplanation] = useState(null);
  const [explanationError, setExplanationError] = useState('');
  const [proposal, setProposal] = useState(null);
  const [proposalError, setProposalError] = useState('');
  const [proposalBusy, setProposalBusy] = useState(false);
  const activeVerification = useRef('');
  activeVerification.current = `${state.specId || ''}:${run?.id || ''}`;
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
  const canPropose = ['PROPERTY_VIOLATED', 'PROPERTY_UNREACHABLE'].includes(result?.status);
  useEffect(() => {
    let active = true;
    setProposal(null); setProposalError(''); setProposalBusy(false);
    if (state.specId && run?.id && canPropose) request(`/specifications/${state.specId}/verifications/${run.id}/proposals`)
      .then(data => { if (active) setProposal(data); })
      .catch(error => { if (active) setProposalError(error.message); });
    return () => { active = false; };
  }, [state.specId, run?.id, canPropose]);
  async function proposalAction(action) {
    const key = activeVerification.current;
    const specId = state.specId;
    setProposalBusy(true); setProposalError('');
    try {
      if (action === 'create') {
        const created = await request(`/specifications/${specId}/verifications/${run.id}/proposals`, {method:'POST'});
        if (activeVerification.current === key) setProposal(created);
      } else if (action === 'reject') {
        await request(`/specifications/${specId}/proposals/${proposal.id}/reject`, {method:'POST'});
        if (activeVerification.current === key) setProposal({...proposal, status:'REJECTED'});
      } else {
        const revised = await request(`/specifications/${specId}/proposals/${proposal.id}/accept`, {method:'POST'});
        if (activeVerification.current === key) await onRevisionCreated(revised);
      }
    } catch (error) { if (activeVerification.current === key) setProposalError(error.message); }
    finally { if (activeVerification.current === key) setProposalBusy(false); }
  }
  if (!run) return <p className="pa-inspector-muted">Согласуйте спецификацию и запустите проверку модели.</p>;
  const trace = result?.counterexample?.steps || [];
  return <div className="pa-inspector pa-verification">
    <div className={`pa-verification-summary${view.verified ? ' pa-verification-summary--success' : ''}`} role="status">
      {view.verified && <Icon name="check" size={20}/>}
      <strong>{statusLabel(result?.status || run.status)}</strong>
    </div>
    {view.pending && <p>Результат появится здесь автоматически.</p>}
    {explanation && <section aria-label="Объяснение проверки"><p>{result?.status === 'PROPERTY_UNREACHABLE' ? 'Проверка не нашла путь от начала до указанного состояния в модели. Это ошибка плана проверки, а не результат обработки файлов.' : explanation.summary}</p></section>}
    {explanationError && <p className="pa-inspector-error">Объяснение недоступно: {explanationError}</p>}
    {result?.error && result?.status !== 'PROPERTY_UNREACHABLE' && <p className="pa-inspector-error">{typeof result.error === 'string' ? result.error : JSON.stringify(result.error)}</p>}
    {result?.errors?.length > 0 && <ul className="pa-validation-errors">{result.errors.map((error, index) => <li key={index}><code>{error.location}</code> — {error.message}</li>)}</ul>}
    {trace.length > 0 && <div className="pa-trace"><h3>Контрпример</h3>{trace.map((step,index) => <details key={index} open={index === 0}><summary>{step.transition || 'Начальное состояние'}</summary><pre>{JSON.stringify(step.state,null,2)}</pre></details>)}</div>}
    {canPropose && <section aria-label="Исправление модели"><h3>Что можно сделать</h3><p>Можно попросить предложить изменение модели или уточнить задачу в чате. Предложение нужно проверить: оно ещё не подтверждено проверкой TLC.</p>{proposalError && <p className="pa-inspector-error" role="alert">{proposalError}</p>}{proposal?.status === 'PROPOSED' ? <div><p>{proposal.proposal.proposed_change}</p><details><summary>Подробности предложения</summary><p>{proposal.proposal.issue_summary}</p><p>{proposal.proposal.rationale}</p><h4>До</h4><pre>{JSON.stringify(proposal.proposal.before_transitions,null,2)}</pre><h4>После</h4><pre>{JSON.stringify(proposal.proposal.after_transitions,null,2)}</pre></details><button className="pa-button pa-button--primary" disabled={proposalBusy || Boolean(state.busy)} onClick={() => proposalAction('accept')}>Создать версию для проверки</button><button className="pa-button" disabled={proposalBusy || Boolean(state.busy)} onClick={() => proposalAction('reject')}>Отклонить</button></div> : <button className="pa-button" disabled={proposalBusy || Boolean(state.busy)} onClick={() => proposalAction('create')}>{proposalBusy ? 'Готовим предложение…' : 'Предложить исправление'}</button>}</section>}
    <div className="pa-inspector-actions">
      {view.verified && <button className="pa-button pa-button--primary" disabled={Boolean(state.busy)} onClick={onGenerate}>{state.generated ? 'Сгенерировать заново' : 'Получить код'}</button>}
      {!view.pending && <button className="pa-button" disabled={Boolean(state.busy)} onClick={onVerify}>Повторить проверку</button>}
    </div>
    {(result || state.spec?.verifications?.length > 1) && <details className="pa-technical-details"><summary>Подробности проверки</summary>
      {result?.property_id && <p>Свойство: <code>{result.property_id}</code></p>}
      {result?.status === 'PROPERTY_UNREACHABLE' && result.error && <p>{result.error}</p>}
      {result?.statistics && <dl>{Object.entries(result.statistics).map(([key,value]) => <div key={key}><dt>{({states_generated:'Состояний',distinct_states:'Уникальных состояний',depth:'Глубина'})[key] || key}</dt><dd>{value}</dd></div>)}</dl>}
      <p className="pa-inspector-muted">Проверена модель процесса. Это не проверка сгенерированного Python.</p>
      {state.spec?.verifications?.map(item => <p key={item.id}>{new Date(item.queued_at).toLocaleString('ru-RU')} · {statusLabel(item.result_json?.status || item.status)}</p>)}
    </details>}
  </div>;
}

function ResultPreview({ preview }) {
  const rows = preview?.rows || [];
  if (!rows.length) return <p>В предпросмотре нет строк.</p>;
  const columns = preview.columns?.map(column => column.name) || Object.keys(rows[0]);
  return <div className="pa-result-table"><table><thead><tr>{columns.map(column => <th key={column} scope="col">{column}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{columns.map(column => <td key={column}>{row[column] == null ? '—' : String(row[column])}</td>)}</tr>)}</tbody></table></div>;
}

export function FilesPanel({ state, view, onOpenSchema, onOpenVerification }) {
  const [preview, setPreview] = useState(null);
  const [content, setContent] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [sources, setSources] = useState([]);
  const [sourceError, setSourceError] = useState('');
  const [sourcePreview, setSourcePreview] = useState(null);
  const [executionRuns, setExecutionRuns] = useState([]);
  const [executionError, setExecutionError] = useState('');
  const [executing, setExecuting] = useState(false);
  const [preparingExecution, setPreparingExecution] = useState(false);
  const [executionInputs, setExecutionInputs] = useState({});
  const [extraParameters, setExtraParameters] = useState([]);
  const executionRequest = useRef(null);
  const activeProject = useRef(state.projectId);
  activeProject.current = state.projectId;
  useEffect(() => {
    let alive = true;
    setSources([]); setSourcePreview(null); setSourceError('');
    if (state.projectId) request(`/projects/${state.projectId}/sources`)
      .then(data => { if (alive) setSources(data); })
      .catch(error => { if (alive) setSourceError(error.message); });
    return () => { alive = false; };
  }, [state.projectId]);
  const specId = state.spec?.id;
  const versionSources = state.spec?.context_json?.source_files || [];
  useEffect(() => {
    let alive = true;
    setExecutionRuns([]); setExecutionError('');
    if (!specId) return () => { alive = false; };
    const refresh = () => request(`/specifications/${specId}/executions`)
      .then(runs => { if (alive) setExecutionRuns(runs); })
      .catch(error => { if (alive) setExecutionError(error.message); });
    void refresh();
    const timer = setInterval(refresh, 2500);
    return () => { alive = false; clearInterval(timer); };
  }, [specId]);
  function prepareExecution() {
    const flags = {};
    versionSources.forEach((file, index) => {
      const stem = file.filename.replace(/\.[^.]+$/, '').toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/^-+|-+$/g, '');
      flags[file.id] = `--${/^[a-z]/.test(stem) ? stem.slice(0, 35) : `input-${index + 1}`}`;
    });
    setExecutionInputs(flags); setExtraParameters([]); setExecutionError(''); setPreparingExecution(true);
  }
  async function confirmExecution() {
    const pairs = versionSources.map(file => [executionInputs[file.id]?.trim(), file.id]);
    if (pairs.some(([flag]) => !/^--[a-z][a-z0-9-]{0,40}$/.test(flag || '') || flag === '--output') || new Set(pairs.map(([flag]) => flag)).size !== pairs.length) {
      setExecutionError('Укажите разные аргументы вида --orders; --output задаётся автоматически.'); return;
    }
    const extras = extraParameters.map(item => [item.flag.trim(), item.value]);
    const names = [...pairs.map(([flag]) => flag), ...extras.map(([flag]) => flag)];
    if (extras.some(([flag, value]) => !/^--[a-z][a-z0-9-]{0,40}$/.test(flag) || flag === '--output' || value.length > 512 || value.includes('\0')) || new Set(names).size !== names.length) {
      setExecutionError('Дополнительные аргументы должны иметь уникальные имена и значения длиной до 512 символов.'); return;
    }
    const inputs = Object.fromEntries(pairs);
    const parameters = Object.fromEntries(extras);
    const key = JSON.stringify([specId, preview.id, inputs, parameters]);
    if (executionRequest.current?.key !== key) executionRequest.current = { key, id: crypto.randomUUID() };
    setExecuting(true); setExecutionError('');
    try {
      await request(`/specifications/${specId}/executions`, { method: 'POST', body: { artifact_id: preview.id, request_id: executionRequest.current.id, inputs, parameters } });
      executionRequest.current = null; setPreparingExecution(false); setPreview(null);
      setExecutionRuns(await request(`/specifications/${specId}/executions`));
    } catch (error) { setExecutionError(error.message); }
    finally { setExecuting(false); }
  }
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
  if (sourcePreview) return <div className="pa-file-preview"><button className="pa-text-button" onClick={() => setSourcePreview(null)}>← К файлам</button><h3>{sourcePreview.filename}</h3><p>Профиль по выборке: {sourcePreview.profile.sampled_rows} строк. Типы являются предварительными, кроме схемы Parquet.</p><h4>Колонки</h4><ul>{sourcePreview.profile.columns.map(column => <li key={column.name}>{column.name} · {column.type} · пропусков в выборке: {column.null_count}</li>)}</ul><h4>Образец строк</h4><ResultPreview preview={{columns:sourcePreview.profile.columns, rows:sourcePreview.profile.sample}}/>{sourcePreview.profile.warnings?.map(warning => <p key={warning}>{warning}</p>)}</div>;
  if (preview) return <div className="pa-file-preview"><button className="pa-text-button" onClick={() => { setPreview(null); setPreparingExecution(false); }}>← К файлам</button><h3>{preview.filename}</h3>{loading && <p role="status">Открываем файл…</p>}{error && <p role="alert">{error}</p>}{!loading && !error && <pre>{content}</pre>}<a className="pa-button" href={artifactUrl(specId,preview.id)}><Icon name="download" size={16}/>Скачать .py</a>
    {view.verified && versionSources.length > 0 && !preparingExecution && <button className="pa-button pa-button--primary" disabled={loading || Boolean(error)} onClick={prepareExecution}>Подготовить запуск</button>}
    {preparingExecution && <section aria-label="Подтверждение запуска"><h3>Подтвердите выполнение</h3><p>Проверьте код выше и укажите аргумент каждого файла. Результат будет сохранён отдельно от проверки TLC.</p>{versionSources.map(file => <label key={file.id}>{file.filename} <input value={executionInputs[file.id] || ''} onChange={event => setExecutionInputs({...executionInputs, [file.id]: event.target.value})} aria-label={`Аргумент для ${file.filename}`}/></label>)}<p>Аргумент --output будет задан автоматически.</p><h4>Дополнительные параметры</h4>{extraParameters.map((item, index) => <div key={index}><label>Аргумент <input value={item.flag} onChange={event => setExtraParameters(extraParameters.map((row, i) => i === index ? {...row, flag:event.target.value} : row))} placeholder="--minimum"/></label><label> Значение <input value={item.value} onChange={event => setExtraParameters(extraParameters.map((row, i) => i === index ? {...row, value:event.target.value} : row))}/></label><button type="button" className="pa-text-button" onClick={() => setExtraParameters(extraParameters.filter((_, i) => i !== index))}>Удалить</button></div>)}<button type="button" className="pa-text-button" disabled={extraParameters.length >= 20} onClick={() => setExtraParameters([...extraParameters, {flag:'', value:''}])}>Добавить параметр</button>{executionError && <p role="alert">{executionError}</p>}<button className="pa-button pa-button--primary" disabled={executing} onClick={confirmExecution}>{executing ? 'Отправляем…' : 'Подтвердить запуск'}</button><button className="pa-button" disabled={executing} onClick={() => setPreparingExecution(false)}>Отмена</button></section>}
  </div>;
  return <div className="pa-files">
    <h3>Исходные данные</h3>
    <p className="pa-files-empty">Прикрепляйте CSV, JSON, JSONL или Parquet к сообщению с помощью скрепки.</p>
    {sourceError && <p role="alert" className="pa-inspector-error">{sourceError}</p>}
    {sources.map(source => <div className="pa-file-row" key={source.id}><Icon name="file" size={20}/><span className="pa-source-name">{source.filename}</span><button type="button" className="pa-text-button" onClick={() => setSourcePreview(source)}>Профиль</button></div>)}
    {executionRuns.length > 0 && <section aria-label="Выполнение конвейера"><h3>Выполнение конвейера</h3>{executionRuns.map(run => <article key={run.id}><strong>{({QUEUED:'В очереди',RUNNING:'Выполняется',SUCCEEDED:'Выполнено',FAILED:'Ошибка выполнения',TIMEOUT:'Превышено время'})[run.status] || run.status}</strong><details><summary>Код и параметры</summary><p>Артефакт: {run.artifact_id}</p><pre>{JSON.stringify({источники:run.inputs, параметры:run.parameters}, null, 2)}</pre></details>{run.error && <pre>{run.error}</pre>}{run.result_preview?.warning && <p>{run.result_preview.warning}</p>}{run.result_preview?.rows && <ResultPreview preview={run.result_preview}/>}{run.status === 'SUCCEEDED' && <p><a href={`/api/v1/specifications/${specId}/executions/${run.id}/result`}>Скачать архив</a>{run.result_preview?.csv_available && <> · <a href={`/api/v1/specifications/${specId}/executions/${run.id}/result.csv`}>Скачать CSV</a></>}</p>}</article>)}</section>}
    {executionError && <p role="alert" className="pa-inspector-error">{executionError}</p>}
    <h3>Артефакты</h3>
    {(state.spec || view.pipeline) && <button className="pa-file-row" type="button" onClick={onOpenSchema}><Icon name="file" size={24}/><span>Спецификация</span><small>Открыть</small></button>}
    {[...artifacts.values()].map(file => <div className="pa-file-row" key={file.id}><Icon name="code" size={24}/><button className="pa-file-name" onClick={() => setPreview(file)}>{file.filename}</button><a className="pa-file-download" href={artifactUrl(file.spec_id,file.id)} aria-label={`Скачать ${file.filename}`} title="Скачать"><Icon name="download" size={20}/></a></div>)}
    {!artifacts.size && <p className="pa-files-empty">{state.spec ? 'Код появится после проверки модели и генерации.' : 'В этом диалоге пока нет файлов.'}</p>}
    {view.run && <button className="pa-file-row pa-file-row--subtle" type="button" onClick={onOpenVerification}><span>Результат проверки</span><Icon name="chevron-down" size={18}/></button>}
  </div>;
}

import { useEffect, useRef, useState } from 'react';
import { request } from '../api/client.js';
import './file-ingestion.css';

const labels = {
  NEW_VALID: 'Новый файл: проверка пройдена',
  NEW_INVALID: 'Новый файл: найдены ошибки',
  ALREADY_LOADED: 'Файл уже загружался',
};

const traces = {
  NEW_VALID: 'Получен ID → файл новый → загрузка → проверка пройдена → успех',
  NEW_INVALID: 'Получен ID → файл новый → загрузка → проверка не пройдена → ошибка',
  ALREADY_LOADED: 'Получен ID → файл уже загружен → успех',
};

function IngestionDiagram() {
  return <details className="pa-ingestion__diagram"><summary>Схема процесса</summary><div className="pa-ingestion__diagram-scroll">
    <svg viewBox="0 0 760 490" role="img" aria-label="Схема приёмки: повторный файл сразу даёт успех, новый проверяется и получает статус успеха или ошибки">
      <defs><marker id="ingestion-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#637080"/></marker></defs>
      <g className="pa-ingestion__edges" fill="none" stroke="#637080" strokeWidth="2" markerEnd="url(#ingestion-arrow)">
        <path d="M380 58 V90"/><path d="M480 140 H690 V390"/><path d="M380 190 V225"/><path d="M380 275 V305"/><path d="M280 355 H140 V390"/><path d="M480 355 H620 V390"/>
      </g>
      <g className="pa-ingestion__nodes"><rect x="270" y="18" width="220" height="40" rx="8"/><path d="M380 90 L480 140 L380 190 L280 140 Z"/><rect x="270" y="225" width="220" height="50" rx="8"/><path d="M380 305 L480 355 L380 405 L280 355 Z"/><rect x="50" y="390" width="180" height="55" rx="8"/><rect x="530" y="390" width="180" height="55" rx="8"/></g>
      <g className="pa-ingestion__text" textAnchor="middle"><text x="380" y="43">Получить ID файла</text><text x="380" y="145">Уже загружен?</text><text x="380" y="255">Загрузить файл</text><text x="380" y="360">Проверка пройдена?</text><text x="140" y="423">Статус: ошибка</text><text x="620" y="423">Статус: успех</text><text x="505" y="130">да</text><text x="396" y="211">нет</text><text x="250" y="345">нет</text><text x="510" y="345">да</text></g>
    </svg>
  </div><p>После сохранения статуса обработка заканчивается. Это схема встроенного сценария, а не произвольных процессов.</p></details>;
}

export default function FileIngestionPanel({ projectId, sources }) {
  const [technicalId, setTechnicalId] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [columns, setColumns] = useState([]);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const requestRef = useRef(null);
  const source = sources.find(item => item.id === sourceId);
  useEffect(() => {
    let active = true;
    request(`/projects/${projectId}/file-ingestions`).then(data => { if (active) setEvents(data); }).catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [projectId]);
  async function submit(event) {
    event.preventDefault();
    if (busy || !technicalId.trim()) return;
    const payload = {technical_id: technicalId.trim(), source_id: sourceId || null, required_columns: columns};
    const key = JSON.stringify(payload);
    if (requestRef.current?.key !== key) requestRef.current = {key, id: crypto.randomUUID()};
    setBusy(true); setError('');
    try {
      const saved = await request(`/projects/${projectId}/file-ingestions`, {method:'POST', body:{...payload, request_id:requestRef.current.id}});
      setEvents(previous => [saved, ...previous.filter(item => item.id !== saved.id)]);
      requestRef.current = null;
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  return <section className="pa-ingestion" aria-label="Приёмка файла">
    <h3>Приёмка файла</h3>
    <p className="pa-ingestion__intro">Локальная проверка процесса из примера преподавателя: ID → повторная загрузка или проверка файла → статус.</p>
    <p className="pa-ingestion__notice">Статус сохраняется локально. Отправка во внешнюю систему пока не подключена.</p>
    <IngestionDiagram />
    <form onSubmit={submit} className="pa-ingestion__form">
      <label>Технический ID<input aria-label="Технический ID" value={technicalId} maxLength={200} disabled={busy} onChange={event => setTechnicalId(event.target.value)} placeholder="Например, file-001"/></label>
      <label>Исходный файл<select aria-label="Исходный файл" value={sourceId} disabled={busy} onChange={event => { setSourceId(event.target.value); setColumns([]); }}><option value="">Без файла — только повторный ID</option>{sources.map(item => <option value={item.id} key={item.id}>{item.filename}</option>)}</select></label>
      {source && <fieldset><legend>Обязательные непустые колонки</legend><p>Правило проверяется для каждой строки файла. Можно выбрать до 20 колонок.</p><div className="pa-ingestion__columns">{(source.profile?.columns || []).map(column => <label key={column.name}><input type="checkbox" checked={columns.includes(column.name)} disabled={busy || (columns.length >= 20 && !columns.includes(column.name))} onChange={event => setColumns(event.target.checked ? [...columns, column.name] : columns.filter(name => name !== column.name))}/><span>{column.name}</span></label>)}</div></fieldset>}
      {error && <p role="alert" className="pa-inspector-error">{error}</p>}
      <button className="pa-button" disabled={busy || !technicalId.trim()}>{busy ? 'Проверяем…' : 'Обработать сообщение'}</button>
    </form>
    {events.length > 0 && <div className="pa-ingestion__history"><h4>Статусы обработки</h4>{events.map(item => <article key={item.id}><strong>{labels[item.branch] || item.branch}</strong><p>ID: {item.technical_id} · Статус: {item.status === 'SUCCESS' ? 'успех' : 'ошибка'}</p><p>{traces[item.branch]}</p>{item.branch !== 'ALREADY_LOADED' && <p>Проверено строк: {item.validation?.checked_rows ?? 0}</p>}{item.validation?.errors?.map((issue, index) => <p key={index}>{issue.type === 'MISSING_COLUMN' ? `Нет колонки «${issue.column}»` : issue.type === 'EMPTY_VALUES' ? `Пустых значений в «${issue.column}»: ${issue.count}` : 'Файл не содержит строк'}</p>)}{item.branch === 'ALREADY_LOADED' && item.validation?.valid === false && <p>Предупреждение: предыдущая проверка файла завершилась ошибкой. Статус успеха здесь означает только факт предыдущей загрузки.</p>}<small>Статус сохранён локально, наружу не отправлен.</small></article>)}</div>}
  </section>;
}

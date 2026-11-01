import { artifactUrl } from '../../../api/client.js';
import { Icon } from '../../icons.jsx';
import './artifacts.css';

function verificationLabel(run) {
  if (run.status === 'QUEUED') return 'Проверка в очереди';
  if (run.status === 'RUNNING') return 'Проверяем модель…';
  if (run.status === 'FINISHED') {
    if (run.result_json?.status === 'PROPERTY_HOLDS') return 'Модель проверена';
    if (run.result_json?.status === 'PROPERTY_VIOLATED') return 'Найдено нарушение';
  }
  return 'Результат проверки';
}

export default function ContextualArtifacts({
  spec,
  generated,
  onOpenSpecification,
  onOpenVerification,
}) {
  if (!spec?.id) return null;

  const artifacts = new Map();
  for (const artifact of spec.artifacts || []) {
    if (artifact.spec_id === spec.id && artifact.id && artifact.filename?.trim()) {
      artifacts.set(artifact.id, artifact);
    }
  }
  if (
    generated?.spec_id === spec.id &&
    generated.artifact_id &&
    generated.filename?.trim() &&
    !artifacts.has(generated.artifact_id)
  ) {
    artifacts.set(generated.artifact_id, {
      id: generated.artifact_id,
      spec_id: generated.spec_id,
      filename: generated.filename,
    });
  }

  const run = spec.verifications?.at(-1);
  const verified = run?.status === 'FINISHED' && run.result_json?.status === 'PROPERTY_HOLDS';

  return (
    <div className="pa-artifacts">
      <div className="pa-artifacts__actions" role="group" aria-label="Действия со спецификацией">
        <button type="button" className="pa-artifacts__action" onClick={onOpenSpecification}>
          <Icon name="schema" size={16} />
          <span>Спецификация</span>
        </button>
        {run && (
          <button type="button" className="pa-artifacts__action pa-artifacts__verification" onClick={onOpenVerification}>
            {verified && <Icon name="check" size={16} />}
            <span>{verificationLabel(run)}</span>
          </button>
        )}
      </div>
      {artifacts.size > 0 && (
        <ul className="pa-artifacts__files" aria-label="Файлы спецификации">
          {[...artifacts.values()].map(artifact => (
            <li className="pa-artifacts__item" key={artifact.id}>
              <a
                className="pa-artifacts__file"
                href={artifactUrl(artifact.spec_id, artifact.id)}
                download={artifact.filename}
              >
                <Icon name="file" size={21} />
                <span className="pa-artifacts__filename">{artifact.filename}</span>
                <Icon name="download" size={18} className="pa-artifacts__download" />
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

import { useEffect, useId, useRef, useState } from 'react';
import { Icon } from '../../icons.jsx';
import './navigation.css';

const MOBILE_QUERY = '(max-width: 900px)';
const FOCUSABLE = 'button:not(:disabled):not([tabindex="-1"]), a[href], [tabindex="0"]';

export default function Navigation({
  projects = [],
  projectId,
  busy = false,
  open = false,
  onSelect,
  onNew,
  onClose,
}) {
  const [mobile, setMobile] = useState(() => (
    typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches
  ));
  const panelRef = useRef(null);
  const closeRef = useRef(null);
  const onCloseRef = useRef(onClose);
  const labelId = useId();
  const historyId = useId();
  const disabled = Boolean(busy);
  onCloseRef.current = onClose;

  useEffect(() => {
    const media = window.matchMedia(MOBILE_QUERY);
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  useEffect(() => {
    if (!mobile || !open) return undefined;

    const previousFocus = document.activeElement;
    const frame = window.requestAnimationFrame(() => closeRef.current?.focus());

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current?.();
        return;
      }
      if (event.key !== 'Tab') return;

      const panel = panelRef.current;
      const items = Array.from(panel?.querySelectorAll(FOCUSABLE) || []);
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;

      if (!first) {
        event.preventDefault();
        panel?.focus();
      } else if (!panel.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener('keydown', handleKeyDown);
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) {
        previousFocus.focus({ preventScroll: true });
      }
    };
  }, [mobile, open]);

  const closeAfter = (action) => {
    action?.();
    if (mobile) onClose?.();
  };

  return (
    <aside
      className={`pa-navigation${open ? ' pa-navigation--open' : ''}`}
      role={mobile ? 'dialog' : undefined}
      aria-modal={mobile && open ? true : undefined}
      aria-labelledby={labelId}
      aria-hidden={mobile && !open ? true : undefined}
      inert={mobile && !open ? true : undefined}
    >
      <button
        className="pa-navigation__scrim"
        type="button"
        tabIndex={-1}
        aria-label="Закрыть навигацию"
        onClick={onClose}
      />

      <div className="pa-navigation__panel" ref={panelRef} tabIndex={-1}>
        <div className="pa-navigation__header">
          <div className="pa-navigation__brand" id={labelId}>
            <Icon name="wave" size={29} />
            <span>Pipeline</span>
          </div>
          <button
            className="pa-navigation__close"
            type="button"
            ref={closeRef}
            aria-label="Закрыть навигацию"
            onClick={onClose}
          >
            <Icon name="close" size={20} />
          </button>
        </div>

        <button
          className="pa-navigation__new"
          type="button"
          disabled={disabled}
          onClick={() => closeAfter(onNew)}
        >
          <Icon name="plus" size={20} />
          <span>Новый диалог</span>
        </button>

        <nav className="pa-navigation__history" aria-labelledby={historyId}>
          <h2 className="pa-navigation__heading" id={historyId}>Недавние</h2>
          <ul className="pa-navigation__list">
            {projects.map((project) => (
              <li className="pa-navigation__item" key={project.id}>
                <button
                  className="pa-navigation__project"
                  type="button"
                  aria-current={project.id === projectId ? 'page' : undefined}
                  disabled={disabled}
                  title={project.name}
                  onClick={() => closeAfter(() => onSelect?.(project.id))}
                >
                  {project.name}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="pa-navigation__user">
          <span className="pa-navigation__avatar" aria-hidden="true">U</span>
          <span>user</span>
        </div>
      </div>
    </aside>
  );
}

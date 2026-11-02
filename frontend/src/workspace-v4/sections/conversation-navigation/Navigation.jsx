import { useEffect, useId, useRef, useState } from 'react';
import { Icon } from '../../icons.jsx';
import './navigation.css';

const MOBILE_QUERY = '(max-width: 900px)';
const FOCUSABLE = 'button:not(:disabled):not([tabindex="-1"]), input:not(:disabled), a[href], [tabindex="0"]';

export default function Navigation({
  projects = [],
  projectId,
  busy = false,
  open = false,
  onSelect,
  onNew,
  onClose,
  onRename,
  onDelete,
}) {
  const [mobile, setMobile] = useState(() => (
    typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches
  ));
  const [action, setAction] = useState(null);
  const panelRef = useRef(null);
  const closeRef = useRef(null);
  const newRef = useRef(null);
  const actionPanelRef = useRef(null);
  const actionInputRef = useRef(null);
  const actionFocusRef = useRef(null);
  const projectRefs = useRef(new Map());
  const toggleRefs = useRef(new Map());
  const actionStateRef = useRef(null);
  const actionSequenceRef = useRef(0);
  const focusFrameRef = useRef(null);
  const focusRequestRef = useRef(null);
  const mountedRef = useRef(true);
  const onCloseRef = useRef(onClose);
  const labelId = useId();
  const historyId = useId();
  const actionId = `${historyId}-actions`;
  const errorId = `${historyId}-error`;
  const warningId = `${historyId}-delete-warning`;
  const disabled = Boolean(busy) || Boolean(action?.pending);
  onCloseRef.current = onClose;
  actionStateRef.current = action;

  const updateAction = (next) => {
    actionStateRef.current = next;
    setAction(next);
  };

  const focusLater = (getTarget) => {
    focusRequestRef.current = getTarget;
    window.cancelAnimationFrame(focusFrameRef.current);
    focusFrameRef.current = window.requestAnimationFrame(() => {
      const target = focusRequestRef.current?.();
      if (target?.isConnected && !target.disabled) {
        target.focus({ preventScroll: true });
        focusRequestRef.current = null;
      }
    });
  };

  const focusNearby = (ids) => {
    focusLater(() => ids.map((id) => projectRefs.current.get(id))
      .find((node) => node?.isConnected && !node.disabled) || newRef.current);
  };

  const cancelAction = () => {
    const current = actionStateRef.current;
    if (!current || current.pending || busy) return;
    updateAction(null);
    focusLater(() => toggleRefs.current.get(current.id) || newRef.current);
  };

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      window.cancelAnimationFrame(focusFrameRef.current);
    };
  }, []);

  useEffect(() => {
    if (!disabled && focusRequestRef.current) focusLater(focusRequestRef.current);
  }, [disabled, projects]);

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
        if (event.defaultPrevented || actionStateRef.current) return;
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

  useEffect(() => {
    if (!action) return undefined;
    if (!projects.some((project) => project.id === action.id)) {
      updateAction(null);
      focusNearby(action.neighbors);
      return undefined;
    }
    if (mobile && !open) updateAction(null);
    return undefined;
  }, [projects, action?.id, mobile, open]);

  useEffect(() => {
    if (!action) return undefined;
    const frame = window.requestAnimationFrame(() => {
      if (action.mode === 'rename') {
        actionInputRef.current?.focus({ preventScroll: true });
        actionInputRef.current?.select();
      } else {
        actionFocusRef.current?.focus({ preventScroll: true });
      }
      actionPanelRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [action?.id, action?.mode]);

  useEffect(() => {
    if (!action) return undefined;
    const handlePointerDown = (event) => {
      const current = actionStateRef.current;
      if (current?.mode !== 'menu' || current.pending || busy) return;
      if (actionPanelRef.current?.contains(event.target)
        || toggleRefs.current.get(current.id)?.contains(event.target)) return;
      cancelAction();
    };
    const handleKeyDown = (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      cancelAction();
    };
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown, true);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown, true);
    };
  }, [action?.id, action?.mode, busy]);

  const openActions = (project) => {
    if (disabled) return;
    if (action?.id === project.id) {
      cancelAction();
      return;
    }
    const index = projects.findIndex((item) => item.id === project.id);
    updateAction({
      id: project.id,
      mode: 'menu',
      name: project.name,
      error: '',
      pending: false,
      token: ++actionSequenceRef.current,
      neighbors: [
        ...projects.slice(index + 1),
        ...projects.slice(0, index).reverse(),
      ].map((item) => item.id),
    });
  };

  const switchAction = (mode) => {
    if (!disabled && actionStateRef.current) {
      updateAction({ ...actionStateRef.current, mode, error: '' });
    }
  };

  const submitAction = async (event) => {
    event.preventDefault();
    const current = actionStateRef.current;
    if (!current || current.pending || busy) return;
    const name = current.name.trim();
    if (current.mode === 'rename' && !name) {
      updateAction({ ...current, error: 'Введите название диалога.' });
      actionInputRef.current?.focus();
      return;
    }
    updateAction({ ...current, pending: true, error: '' });
    let success = false;
    try {
      success = current.mode === 'rename'
        ? await onRename?.(current.id, name)
        : await onDelete?.(current.id);
    } catch {
      success = false;
    }
    if (!mountedRef.current || actionStateRef.current?.token !== current.token) return;
    if (success !== true) {
      updateAction({
        ...actionStateRef.current,
        pending: false,
        error: current.mode === 'rename'
          ? 'Не удалось переименовать диалог. Попробуйте ещё раз.'
          : 'Не удалось удалить диалог. Попробуйте ещё раз.',
      });
      focusLater(() => current.mode === 'rename' ? actionInputRef.current : actionFocusRef.current);
      return;
    }
    updateAction(null);
    if (current.mode === 'delete') focusNearby(current.neighbors);
    else focusLater(() => toggleRefs.current.get(current.id) || newRef.current);
  };

  const handleMenuKeys = (event) => {
    const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End'];
    if (!keys.includes(event.key)) return;
    const buttons = Array.from(actionPanelRef.current?.querySelectorAll('button:not(:disabled)') || []);
    if (!buttons.length) return;
    event.preventDefault();
    const index = buttons.indexOf(document.activeElement);
    let next = event.key === 'ArrowDown' ? (index + 1) % buttons.length
      : (index - 1 + buttons.length) % buttons.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = buttons.length - 1;
    buttons[next].focus();
  };

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
          ref={newRef}
          disabled={disabled}
          onClick={() => closeAfter(onNew)}
        >
          <Icon name="plus" size={20} />
          <span>Новый диалог</span>
        </button>

        <nav className="pa-navigation__history" aria-labelledby={historyId}>
          <h2 className="pa-navigation__heading" id={historyId}>Недавние</h2>
          <ul className="pa-navigation__list">
            {projects.map((project) => {
              const active = action?.id === project.id;
              const selected = project.id === projectId;
              return (
                <li className="pa-navigation__item" key={project.id}>
                  <div className={`pa-navigation__row${selected ? ' pa-navigation__row--selected' : ''}`}>
                    <button
                      className="pa-navigation__project"
                      type="button"
                      ref={(node) => {
                        if (node) projectRefs.current.set(project.id, node);
                        else projectRefs.current.delete(project.id);
                      }}
                      aria-current={selected ? 'page' : undefined}
                      disabled={disabled}
                      title={project.name}
                      onClick={() => closeAfter(() => onSelect?.(project.id))}
                    >
                      {project.name}
                    </button>
                    <button
                      className="pa-navigation__actions-toggle"
                      type="button"
                      ref={(node) => {
                        if (node) toggleRefs.current.set(project.id, node);
                        else toggleRefs.current.delete(project.id);
                      }}
                      aria-label={`Действия диалога ${project.name}`}
                      aria-expanded={active}
                      aria-controls={active ? actionId : undefined}
                      disabled={disabled}
                      onClick={() => openActions(project)}
                    >
                      <span aria-hidden="true">⋯</span>
                    </button>
                  </div>

                  {active && action.mode === 'menu' && (
                    <div
                      className="pa-navigation__popover"
                      ref={actionPanelRef}
                      id={actionId}
                      role="group"
                      aria-label={`Действия диалога ${project.name}`}
                      onKeyDown={handleMenuKeys}
                    >
                      <button type="button" ref={actionFocusRef} disabled={disabled} onClick={() => switchAction('rename')}>
                        Переименовать
                      </button>
                      <button className="pa-navigation__delete-action" type="button" disabled={disabled} onClick={() => switchAction('delete')}>
                        Удалить
                      </button>
                    </div>
                  )}

                  {active && action.mode !== 'menu' && (
                    <form
                      className="pa-navigation__action-form"
                      ref={actionPanelRef}
                      id={actionId}
                      aria-label={action.mode === 'rename' ? 'Переименовать диалог' : 'Удалить диалог'}
                      aria-busy={action.pending || undefined}
                      onSubmit={submitAction}
                    >
                      {action.mode === 'rename' ? (
                        <>
                          <label className="pa-navigation__form-label" htmlFor={`${historyId}-name`}>Название диалога</label>
                          <input
                            className="pa-navigation__name-input"
                            ref={actionInputRef}
                            id={`${historyId}-name`}
                            aria-label="Название диалога"
                            aria-describedby={action.error ? errorId : undefined}
                            maxLength={200}
                            value={action.name}
                            disabled={disabled}
                            onChange={(event) => updateAction({ ...actionStateRef.current, name: event.target.value, error: '' })}
                          />
                        </>
                      ) : (
                        <p className="pa-navigation__delete-warning" id={warningId}>
                          Диалог «{project.name}» будет удалён вместе с сообщениями, версиями и файлами. Это действие нельзя отменить.
                        </p>
                      )}
                      {action.error && <p className="pa-navigation__form-error" id={errorId} role="alert">{action.error}</p>}
                      <div className="pa-navigation__form-buttons">
                        <button
                          className={action.mode === 'delete' ? 'pa-navigation__confirm-delete' : 'pa-navigation__save'}
                          type="submit"
                          disabled={disabled || (action.mode === 'rename' && !action.name.trim())}
                          aria-describedby={action.mode === 'delete' ? warningId : undefined}
                        >
                          {action.mode === 'rename' ? 'Сохранить' : 'Удалить диалог'}
                        </button>
                        <button className="pa-navigation__cancel" type="button" ref={actionFocusRef} disabled={disabled} onClick={cancelAction}>
                          Отмена
                        </button>
                      </div>
                    </form>
                  )}
                </li>
              );
            })}
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

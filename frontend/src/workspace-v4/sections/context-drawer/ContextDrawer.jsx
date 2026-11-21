import { useEffect, useId, useRef, useState } from 'react';
import { Icon } from '../../icons.jsx';
import './context-drawer.css';

const MOBILE_QUERY = '(max-width: 1100px)';
const WIDTH_KEY = 'pipeline-context-width';

function widthBounds() {
  const screen = window.innerWidth;
  return { min: Math.min(320, screen), max: screen <= 1100 ? screen : Math.max(320, screen - 440) };
}

function clampWidth(value) {
  const { min, max } = widthBounds();
  return Math.round(Math.min(max, Math.max(min, value)));
}
const FOCUSABLE = [
  'a[href]',
  'area[href]',
  'button',
  'input:not([type="hidden"])',
  'select',
  'textarea',
  'iframe',
  '[contenteditable]:not([contenteditable="false"])',
  '[tabindex]',
].join(', ');

function isFocusable(element) {
  return element instanceof HTMLElement
    && element !== document.body
    && element.isConnected
    && element.tabIndex >= 0
    && !element.matches(':disabled')
    && !element.closest('[hidden], [inert], [aria-hidden="true"]')
    && element.getClientRects().length > 0
    && window.getComputedStyle(element).visibility === 'visible';
}

function getFocusableElements(panel) {
  return Array.from(panel?.querySelectorAll(FOCUSABLE) || []).filter(isFocusable);
}

export default function ContextDrawer({ mode, title, onClose, children, openerRef, fallbackFocusRef }) {
  const [width, setWidth] = useState(() => {
    let saved = 0;
    try { saved = Number(window.localStorage.getItem(WIDTH_KEY)); } catch { /* storage may be disabled */ }
    return clampWidth(saved > 0 && Number.isFinite(saved) ? saved : 520);
  });
  const dragRef = useRef(null);
  const [mobile, setMobile] = useState(() => (
    typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches
  ));
  const panelRef = useRef(null);
  const closeRef = useRef(null);
  const onCloseRef = useRef(onClose);
  const previousModeRef = useRef(mode);
  const titleId = useId();
  const open = Boolean(mode);
  onCloseRef.current = onClose;

  useEffect(() => {
    const update = () => setWidth(current => clampWidth(current));
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  function changeWidth(value) {
    const next = clampWidth(value);
    setWidth(next);
    try { window.localStorage.setItem(WIDTH_KEY, String(next)); } catch { /* storage may be disabled */ }
  }

  function onResizeKey(event) {
    const { min, max } = widthBounds();
    const target = event.key === 'ArrowLeft' ? width + 32 : event.key === 'ArrowRight' ? width - 32 : event.key === 'Home' ? min : event.key === 'End' ? max : null;
    if (target !== null) { event.preventDefault(); changeWidth(target); }
  }

  useEffect(() => {
    const media = window.matchMedia(MOBILE_QUERY);
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  useEffect(() => {
    if (!open) return undefined;

    const opener = openerRef?.current;
    const previousFocus = document.activeElement;
    closeRef.current?.focus({ preventScroll: true });

    return () => {
      const target = [opener, previousFocus, fallbackFocusRef?.current].find(isFocusable);
      target?.focus({ preventScroll: true });
    };
    // The opening element is captured once, so child updates and mode switches
    // cannot reset focus or replace the return destination.
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;

    const panel = panelRef.current;
    if (mobile && !panel?.contains(document.activeElement)) {
      closeRef.current?.focus({ preventScroll: true });
    }

    const handleKeyDown = (event) => {
      if (event.defaultPrevented) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current?.();
        return;
      }
      if (!mobile || event.key !== 'Tab') return;

      const items = getFocusableElements(panel);
      const first = items[0] || closeRef.current;
      const last = items[items.length - 1] || first;
      const active = document.activeElement;

      if (!panel?.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first?.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [mobile, open]);

  useEffect(() => {
    const changedMode = previousModeRef.current !== mode;
    previousModeRef.current = mode;
    if (open && changedMode && document.activeElement === document.body) {
      closeRef.current?.focus({ preventScroll: true });
    }
  }, [mode, open]);

  if (!open) return null;

  return (
    <aside
      className={`pa-context-drawer pa-context-drawer--${mode}`}
      style={{ '--pa-drawer-width': `${width}px` }}
      role={mobile ? 'dialog' : undefined}
      aria-modal={mobile ? true : undefined}
      aria-labelledby={titleId}
    >
      <button
        className="pa-context-drawer__scrim"
        type="button"
        aria-label="Закрыть панель"
        tabIndex={-1}
        onClick={() => onCloseRef.current?.()}
      />

      <div className="pa-context-drawer__panel" ref={panelRef}>
        <div className="pa-context-drawer__resize" role="separator" tabIndex={0} aria-label="Изменить ширину панели" title="Перетащите границу, чтобы изменить ширину" aria-orientation="vertical" aria-valuemin={widthBounds().min} aria-valuemax={widthBounds().max} aria-valuenow={width} aria-valuetext={`${width} пикселей`}
          onKeyDown={onResizeKey}
          onPointerDown={event => { if (event.button !== 0) return; dragRef.current = event.pointerId; event.currentTarget.setPointerCapture(event.pointerId); }}
          onPointerMove={event => { if (dragRef.current === event.pointerId) changeWidth(window.innerWidth - event.clientX); }}
          onPointerUp={event => { if (dragRef.current === event.pointerId) dragRef.current = null; }}
          onPointerCancel={() => { dragRef.current = null; }}><span aria-hidden="true"/></div>
        <header className="pa-context-drawer__header">
          <h2 className="pa-context-drawer__title" id={titleId}>{title}</h2>
          <button
            className="pa-context-drawer__close"
            type="button"
            aria-label="Закрыть панель"
            ref={closeRef}
            onClick={() => onCloseRef.current?.()}
          >
            <Icon name="close" size={22} />
          </button>
        </header>

        <div className="pa-context-drawer__body">{children}</div>
      </div>
    </aside>
  );
}

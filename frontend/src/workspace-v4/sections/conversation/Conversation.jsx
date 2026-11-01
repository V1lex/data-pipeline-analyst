import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { Icon } from '../../icons.jsx';
import './conversation.css';

function resizeTextarea(element) {
  if (!element) return;
  element.style.height = 'auto';
  const height = element.scrollHeight;
  element.style.height = `${Math.min(height, 200)}px`;
  element.style.overflowY = height > 200 ? 'auto' : 'hidden';
}

export default function Conversation({
  project,
  loading = false,
  text = '',
  busy = '',
  pendingMessage = null,
  error = '',
  onTextChange,
  onSend,
  onDismissError,
  actions,
  artifacts,
  generated,
}) {
  // Generated content belongs to the parent-provided artifact and action slots.
  const messages = Array.isArray(project?.messages) ? project.messages : [];
  const questions = project?.analyst?.last_response?.clarification_questions || [];
  const projectKey = project?.id ?? null;
  const lastMessage = messages.at(-1);
  const lastMessageKey = lastMessage?.id ?? lastMessage?.content ?? null;
  const lastAssistantIndex = messages.reduce((last, message, index) => (
    message.role === 'assistant' ? index : last
  ), -1);
  const empty = messages.length === 0 && !pendingMessage;
  const disabled = Boolean(busy || loading);
  const textareaId = useId();
  const textareaRef = useRef(null);
  const composerRef = useRef(null);
  const scrollRef = useRef(null);
  const nearBottomRef = useRef(true);
  const previousTranscriptRef = useRef(null);
  const composingRef = useRef(false);
  const copyAttemptRef = useRef(0);
  const copyTimerRef = useRef(null);
  const [copyStatus, setCopyStatus] = useState(null);

  useLayoutEffect(() => {
    resizeTextarea(textareaRef.current);
  }, [text]);

  useEffect(() => {
    const composer = composerRef.current;
    if (!composer || typeof ResizeObserver === 'undefined') return;
    let previousWidth = composer.getBoundingClientRect().width;
    const observer = new ResizeObserver(([entry]) => {
      if (Math.abs(entry.contentRect.width - previousWidth) < 1) return;
      previousWidth = entry.contentRect.width;
      resizeTextarea(textareaRef.current);
    });
    observer.observe(composer);
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    const previous = previousTranscriptRef.current;
    const changedProject = !previous || previous.projectKey !== projectKey;
    const newMessage = messages.length > (previous?.count ?? 0)
      || (messages.length > 0 && lastMessageKey !== previous?.lastMessageKey);
    const newPending = Boolean(pendingMessage) && pendingMessage !== previous?.pendingMessage;
    const scroller = scrollRef.current;

    if (scroller && (changedProject || (nearBottomRef.current && (newMessage || newPending)))) {
      const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      scroller.scrollTo({
        top: scroller.scrollHeight,
        behavior: changedProject || reducedMotion ? 'auto' : 'smooth',
      });
      nearBottomRef.current = true;
    }
    previousTranscriptRef.current = {
      projectKey,
      count: messages.length,
      lastMessageKey,
      pendingMessage,
    };
  }, [projectKey, messages.length, lastMessageKey, pendingMessage, loading]);

  useEffect(() => {
    setCopyStatus(null);
    return () => {
      copyAttemptRef.current += 1;
      window.clearTimeout(copyTimerRef.current);
    };
  }, [projectKey]);

  function trackScroll(event) {
    const element = event.currentTarget;
    nearBottomRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 88;
  }

  async function copyReply(message, key) {
    const attempt = ++copyAttemptRef.current;
    window.clearTimeout(copyTimerRef.current);
    setCopyStatus(null);
    let state = 'copied';
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(String(message.content ?? ''));
    } catch {
      state = 'failed';
    }
    if (attempt !== copyAttemptRef.current) return;
    setCopyStatus({ key, state });
    copyTimerRef.current = window.setTimeout(() => setCopyStatus(null), 3000);
  }

  function handleSubmit(event) {
    event.preventDefault();
    if (disabled || !text.trim()) return;
    onSend?.(event);
  }

  function handleKeyDown(event) {
    if (event.key !== 'Enter' || event.shiftKey || composingRef.current
      || event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!disabled && text.trim()) event.currentTarget.form?.requestSubmit();
  }

  return (
    <section
      className={`pa-conversation${empty && !loading ? ' pa-conversation--empty' : ''}`}
      aria-label="Переписка с аналитиком"
    >
      <div className="pa-conversation__scroll" ref={scrollRef} onScroll={trackScroll}>
        <div className="pa-conversation__thread">
          {loading ? (
            <div className="pa-conversation__loading" role="status">Открываем диалог…</div>
          ) : (
            <>
              {empty && (
                <div className="pa-conversation__welcome">
                  <Icon name="wave" size={34}/>
                  <h1>С чего начнём?</h1>
                </div>
              )}
              <div className="pa-conversation__messages" role="log" aria-label="Сообщения диалога" aria-live="polite" aria-relevant="additions">
                {messages.map((message, index) => {
                  const isUser = message.role === 'user';
                  const key = message.id ?? `message-${index}`;
                  const feedback = copyStatus?.key === key ? copyStatus.state : null;
                  return (
                    <article
                      className={`pa-conversation__message pa-conversation__message--${isUser ? 'user' : 'assistant'}`}
                      key={key}
                      aria-label={isUser ? 'Ваше сообщение' : 'Ответ аналитика'}
                    >
                      {!isUser && <span className="pa-conversation__mark"><Icon name="wave" size={25}/></span>}
                      <div className="pa-conversation__message-body">
                        <p className="pa-conversation__text">{message.content}</p>
                        {!isUser && index === lastAssistantIndex && questions.length > 0 && (
                          <ul className="pa-conversation__questions">
                            {questions.map((question, questionIndex) => (
                              <li key={questionIndex}>
                                <p>{question.question}</p>
                                {question.suggestion && (
                                  <p className="pa-conversation__suggestion">Предложение: {question.suggestion}</p>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                        {!isUser && (
                          <div className="pa-conversation__reply-tools">
                            <button
                              className="pa-conversation__copy"
                              type="button"
                              aria-label={feedback === 'copied' ? 'Ответ скопирован' : 'Скопировать ответ'}
                              title={feedback === 'copied' ? 'Скопировано' : 'Скопировать ответ'}
                              onClick={() => copyReply(message, key)}
                            >
                              <Icon name={feedback === 'copied' ? 'check' : 'copy'} size={17}/>
                            </button>
                            {feedback && (
                              <span className="pa-conversation__copy-feedback" role="status">
                                {feedback === 'copied' ? 'Скопировано' : 'Не удалось скопировать'}
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    </article>
                  );
                })}
                {pendingMessage && (
                  <article className="pa-conversation__message pa-conversation__message--user" aria-label="Ваше отправляемое сообщение">
                    <div className="pa-conversation__message-body"><p className="pa-conversation__text">{pendingMessage}</p></div>
                  </article>
                )}
              </div>
              {(artifacts || actions) && (
                <div className="pa-conversation__extras">{artifacts}{actions}</div>
              )}
              {busy && (
                <div className="pa-conversation__busy" role="status">
                  <span className="pa-conversation__busy-dot" aria-hidden="true"/>
                  <span>{busy}</span>
                </div>
              )}
            </>
          )}
        </div>
      </div>
      <div className="pa-conversation__composer-wrap">
        {error && (
          <div className="pa-conversation__error" role="alert">
            <span>{error}</span>
            {onDismissError && <button type="button" onClick={onDismissError}>Закрыть</button>}
          </div>
        )}
        <form className="pa-conversation__composer" onSubmit={handleSubmit} ref={composerRef}>
          <label className="pa-conversation__sr-only" htmlFor={textareaId}>Сообщение аналитику</label>
          <textarea
            id={textareaId}
            ref={textareaRef}
            value={text}
            onChange={event => onTextChange?.(event.target.value)}
            onKeyDown={handleKeyDown}
            onCompositionStart={() => { composingRef.current = true; }}
            onCompositionEnd={() => { composingRef.current = false; }}
            placeholder={empty ? 'Опишите задачу с данными…' : 'Что изменить в конвейере?'}
            rows={2}
            maxLength={30000}
            disabled={disabled}
          />
          <button
            className="pa-conversation__send"
            type="submit"
            aria-label="Отправить сообщение"
            disabled={disabled || !text.trim()}
          >
            <Icon name="arrow-up" size={24}/>
          </button>
        </form>
      </div>
    </section>
  );
}

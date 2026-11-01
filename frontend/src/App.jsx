import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import Navigation from './workspace-v4/sections/conversation-navigation/Navigation.jsx';
import Conversation from './workspace-v4/sections/conversation/Conversation.jsx';
import ContextDrawer from './workspace-v4/sections/context-drawer/ContextDrawer.jsx';
import ContextualArtifacts from './workspace-v4/sections/contextual-artifacts/ContextualArtifacts.jsx';
import { WorkspaceController, selectWorkspace } from './workspace-v4/state.js';
import { FilesPanel, SchemaPanel, VerificationPanel, VersionPicker } from './workspace-v4/Inspector.jsx';
import { Icon } from './workspace-v4/icons.jsx';

function useMedia(query) {
  const [matches,setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    media.addEventListener('change',update);
    return () => media.removeEventListener('change',update);
  },[query]);
  return matches;
}

export default function App() {
  const [store] = useState(() => new WorkspaceController({ storage: window.localStorage }));
  const state = useSyncExternalStore(store.subscribe,store.getSnapshot);
  const view = selectWorkspace(state);
  const [drawer,setDrawer] = useState(null);
  const [navigationOpen,setNavigationOpen] = useState(false);
  const openerRef = useRef(null);
  const composerFocusRef = useRef(null);
  useEffect(() => { composerFocusRef.current = document.querySelector('.pa-conversation__composer textarea'); },[]);
  const narrowDrawer = useMedia('(max-width: 1100px)');
  const narrowNavigation = useMedia('(max-width: 900px)');
  const modal = Boolean((drawer && narrowDrawer) || (navigationOpen && narrowNavigation));

  useEffect(() => { void store.loadProjects({restore:true}); },[store]);
  useEffect(() => {
    if (!view.pending || !state.specId) return;
    const timer = setInterval(() => void store.refreshVerification(),1800);
    return () => clearInterval(timer);
  },[store,view.pending,state.specId]);

  function openDrawer(mode,event) {
    if (event?.currentTarget) openerRef.current = event.currentTarget;
    else if (!drawer && document.activeElement instanceof HTMLElement) openerRef.current = document.activeElement;
    setNavigationOpen(false);
    setDrawer(mode);
  }
  function selectProject(id) { setDrawer(null); setNavigationOpen(false); void store.selectProject(id); }
  function newConversation() { setDrawer(null); setNavigationOpen(false); store.newConversation(); }
  async function build(event) {
    if (event?.currentTarget) openerRef.current = event.currentTarget;
    const result = await store.build();
    if (result) { setNavigationOpen(false); setDrawer('schema'); }
  }
  async function verify() { if (await store.versionAction('verify')) setDrawer('verification'); }
  async function generate() { if (await store.generate()) setDrawer('files'); }

  const titles = { schema:'Схема',files:'Файлы',verification:'Результат проверки' };
  const inspectorProps = {state,view,onBuild:build,onApprove:() => void store.versionAction('approve'),onVerify:verify,onGenerate:generate,onOpenVerification:() => openDrawer('verification'),onOpenSchema:() => openDrawer('schema')};
  return <div className={`pa-app${drawer ? ' pa-app--drawer-open' : ''}`}>
    <div className="pa-navigation-host" inert={drawer && narrowDrawer ? true : undefined} aria-hidden={drawer && narrowDrawer ? true : undefined}>
      <Navigation projects={state.projects} projectId={state.projectId} busy={state.busy} open={navigationOpen} onSelect={selectProject} onNew={newConversation} onClose={() => setNavigationOpen(false)}/>
    </div>
    <main className="pa-main" inert={modal ? true : undefined} aria-hidden={modal ? true : undefined}>
      <header className="pa-topbar">
        <button className="pa-icon-button pa-menu-button" onClick={() => { setDrawer(null); setNavigationOpen(true); }} aria-label="Открыть историю диалогов" aria-expanded={navigationOpen}><Icon name="menu"/></button>
        <div className="pa-conversation-title">{state.project?.name || (state.loadingProject ? 'Открываем диалог…' : 'Pipeline')}</div>
        {state.project && <div className="pa-header-actions">
          <button className={`pa-header-action${drawer === 'schema' ? ' is-active' : ''}`} onClick={event => openDrawer('schema',event)} aria-expanded={drawer === 'schema'}><Icon name="schema"/><span>Схема</span></button>
          <button className={`pa-header-action${drawer === 'files' ? ' is-active' : ''}`} onClick={event => openDrawer('files',event)} aria-expanded={drawer === 'files'}><Icon name="file"/><span>Файлы</span></button>
        </div>}
      </header>
      <Conversation project={state.project} loading={state.loadingProject} text={state.text} busy={state.busy} pendingMessage={state.pendingMessage} error={drawer ? '' : state.error} onTextChange={value => store.setText(value)} onSend={event => { event.preventDefault(); void store.send(); }} onDismissError={() => store.dismissError()}
        artifacts={<ContextualArtifacts spec={state.spec} generated={state.generated} onOpenSpecification={() => openDrawer('schema')} onOpenVerification={() => openDrawer('verification')}/>}
        actions={view.canBuild && !state.specId ? <button className="pa-context-action" disabled={Boolean(state.busy)} onClick={build}><Icon name="schema" size={18}/>Собрать спецификацию</button> : null}/>
    </main>
    <ContextDrawer mode={drawer} title={titles[drawer] || ''} onClose={() => setDrawer(null)} openerRef={openerRef} fallbackFocusRef={composerFocusRef}>
      {state.error && <div className="pa-drawer-error" role="alert"><p>{state.error}</p><button type="button" className="pa-text-button" onClick={() => store.dismissError()}>Закрыть ошибку</button></div>}
      {state.busy && <p className="pa-inspector-muted" role="status">{state.busy}</p>}
      <VersionPicker state={state} onSelect={id => void store.selectSpecification(id)}/>
      {drawer === 'schema' && <SchemaPanel key={state.specId || 'draft'} {...inspectorProps}/>}
      {drawer === 'verification' && <VerificationPanel {...inspectorProps}/>}
      {drawer === 'files' && <FilesPanel key={state.specId || 'draft'} {...inspectorProps}/>}
    </ContextDrawer>
  </div>;
}

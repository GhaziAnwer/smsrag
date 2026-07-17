// script.js – Simplified Clean UI

document.addEventListener('DOMContentLoaded', () => {

  /* ─── CONFIG & DOM REFS ────────────────────────────────────────── */
  const API_BASE = window.API_BASE || `${location.origin}/api`;
  const DOC_BASE = location.origin;

  const CLIENT_ID = window.CLIENT_ID || 'rsms';
  const STORAGE_PREFIX = `${CLIENT_ID}_`;
  
  let CONV_ID = sessionStorage.getItem(`${STORAGE_PREFIX}CONV_ID`) || newId();
  sessionStorage.setItem(`${STORAGE_PREFIX}CONV_ID`, CONV_ID);

  // DOM elements
  const navActions = document.getElementById('navActions');
  const histBtn    = document.getElementById('history-btn');
  const newBtn     = document.getElementById('new-chat');
  
  const histPane   = document.getElementById('history-pane');
  const histClose  = document.getElementById('history-close');
  const threadNav  = document.getElementById('thread-list');
  
  const messages   = document.getElementById('messages');
  const chatPanel  = document.querySelector('.chat');
  const form       = document.getElementById('chat-form');
  const input      = document.getElementById('chat-input');
  const sendBtn    = document.getElementById('send-btn');
  
  const viewer     = document.getElementById('doc-viewer');
  const frame      = document.getElementById('doc-frame');
  const docTitle   = document.getElementById('doc-title');
  const vClose     = document.getElementById('viewer-close');

  // Client badge
  const badge = document.createElement('span');
  badge.className = 'client-badge glass';
  badge.textContent = 'Client: ' + window.CLIENT_LABEL;
  navActions.append(badge);

  const welcomeScreen = document.getElementById('welcome-screen');
  const viewerToggle  = document.getElementById('viewer-toggle');
  const pageTitle     = document.getElementById('page-title');

  // Set when the user hides the viewer by hand, so a later answer does not
  // yank the panel back open against their wishes.
  let userCollapsedViewer = false;
  // Remember the most recently opened document so the header toggle can reopen
  // it after an X close. Cleared when the conversation changes.
  let lastDocUrl = null, lastDocTitle = null;

  /* ─── UTILITIES ────────────────────────────────────────────────── */
  function newId () {
    if (typeof globalThis.crypto?.randomUUID === 'function') {
      return 'c_' + globalThis.crypto.randomUUID();
    }
    return 'c_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 10);
  }

  /* ─── WELCOME SCREEN ───────────────────────────────────────────── */
  // .chat-empty on the chat panel collapses #messages and hides the
  // duplicate page title, so the welcome block centres in the full panel.
  function showWelcomeScreen () {
    if (welcomeScreen) welcomeScreen.classList.remove('hidden');
    chatPanel?.classList.add('chat-empty');
    clearPanelTitle();
    const titleEl = welcomeScreen?.querySelector('.welcome-title');
    if (titleEl) {
      const h = new Date().getHours();
      const part = h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening';
      titleEl.textContent = `Good ${part}`;
    }
  }

  function hideWelcomeScreen () {
    if (welcomeScreen) welcomeScreen.classList.add('hidden');
    chatPanel?.classList.remove('chat-empty');
  }

  // Clear the panel header name when returning to the empty/landing state.
  function clearPanelTitle () {
    if (pageTitle) pageTitle.textContent = '';
  }

  // Storage functions
  function saveThread(id){
    const arr = JSON.parse(localStorage.getItem(`${STORAGE_PREFIX}THREADS`)||'[]');
    if(!arr.includes(id)){ 
      arr.push(id); 
      localStorage.setItem(`${STORAGE_PREFIX}THREADS`, JSON.stringify(arr)); 
    }
  }
  
  // Words dropped when deriving a chat name from a question — interrogatives,
  // auxiliaries, articles/prepositions, and generic filler that carries no topic.
  const NAME_STOPWORDS = new Set([
    'what','whats','how','who','whom','when','where','why','which','whose',
    'is','are','am','be','was','were','been','the','a','an','of','for','to',
    'in','on','at','by','with','from','as','and','or','do','does','did','can',
    'could','should','would','will','shall','may','might','must','i','we','you',
    'my','our','your','me','us','it','its','this','that','these','those','if',
    'please','tell','explain','describe','list','give','show','about','regarding',
    'concerning','related','onboard','board','ship','vessel',
    'procedure','procedures','process','processes','requirement','requirements'
  ]);

  // Derive a short, topic-based chat name from a question (not the raw text).
  function generateChatName(question){
    const words = (question || '')
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, ' ')
      .split(/\s+/)
      .filter(w => w && !NAME_STOPWORDS.has(w));
    let picked = words.slice(0, 4);
    if (!picked.length) {
      // Nothing meaningful survived — fall back to the first words verbatim.
      picked = (question || 'Chat').replace(/[^a-z0-9\s]/gi, ' ')
        .split(/\s+/).filter(Boolean).slice(0, 4);
    }
    const name = picked
      .map(w => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ')
      .slice(0, 34)
      .trim();
    return name || 'Chat';
  }

  // If a chat name already exists on another conversation, append the next
  // number: "Fire Safety" -> "Fire Safety2" -> "Fire Safety3".
  function dedupeChatName(base, convId = CONV_ID){
    const ids = JSON.parse(localStorage.getItem(`${STORAGE_PREFIX}THREADS`)||'[]');
    const used = new Set(
      ids.filter(id => id !== convId)
         .map(id => localStorage.getItem(`${STORAGE_PREFIX}${id}_title`))
         .filter(Boolean)
    );
    if (!used.has(base)) return base;
    let n = 2;
    while (used.has(base + n)) n++;
    return base + n;
  }

  // Show the current conversation's name in the chat panel header.
  function updatePanelTitle(name){
    if (pageTitle) pageTitle.textContent = name || '';
  }

  function setTitle(question){
    const key = `${STORAGE_PREFIX}${CONV_ID}_title`;
    const cur = localStorage.getItem(key)||'';
    if(cur && cur !== 'Untitled'){ updatePanelTitle(cur); return; }
    // Instant heuristic placeholder so the header is never empty…
    const placeholder = dedupeChatName(generateChatName(question));
    localStorage.setItem(key, placeholder);
    saveThread(CONV_ID);
    updatePanelTitle(placeholder);
    // …then refine with an LLM-generated title in the background.
    refineTitleWithLLM(question, CONV_ID, placeholder);
  }

  // Ask the backend for a nicer LLM title and swap it in once it arrives.
  // Any failure leaves the heuristic placeholder in place.
  async function refineTitleWithLLM(question, convId, placeholder){
    let title = '';
    try {
      const res = await fetch(`${API_BASE}/title`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, client_id: CLIENT_ID })
      });
      if (!res.ok) return;
      title = ((await res.json()).title || '').trim();
    } catch (e) { return; }
    if (!title) return;
    const key = `${STORAGE_PREFIX}${convId}_title`;
    // Only replace if our placeholder is still the stored title (the user
    // hasn't renamed it and the conversation wasn't reset).
    if (localStorage.getItem(key) !== placeholder) return;
    title = dedupeChatName(title, convId);
    localStorage.setItem(key, title);
    if (convId === CONV_ID) updatePanelTitle(title);
    renderThreads();
  }
  
  async function deleteThread(id) {
    // Delete from server (SQLite)
    try {
      await fetch(`${API_BASE}/conversations/${id}`, { method: "DELETE" });
    } catch(e) { console.warn("Server delete failed:", e); }

    const arr = JSON.parse(localStorage.getItem(`${STORAGE_PREFIX}THREADS`)||'[]');
    const filtered = arr.filter(tid => tid !== id);
    localStorage.setItem(`${STORAGE_PREFIX}THREADS`, JSON.stringify(filtered));

    localStorage.removeItem(`${STORAGE_PREFIX}${id}_title`);

    if (id === CONV_ID) {
      CONV_ID = newId();
      sessionStorage.setItem(`${STORAGE_PREFIX}CONV_ID`, CONV_ID);
      messages.innerHTML = '';
      showWelcomeScreen();
      resetViewer();
    }

    await renderThreads();
  }
  
  async function clearAllThreads() {
    if (!confirm('Clear all chat history? This cannot be undone.')) return;

    const threads = JSON.parse(localStorage.getItem(`${STORAGE_PREFIX}THREADS`)||'[]');
    threads.forEach(id => {
      localStorage.removeItem(`${STORAGE_PREFIX}${id}_title`);
    });

    localStorage.setItem(`${STORAGE_PREFIX}THREADS`, '[]');

    CONV_ID = newId();
    sessionStorage.setItem(`${STORAGE_PREFIX}CONV_ID`, CONV_ID);
    messages.innerHTML = '';
    resetViewer();
    await renderThreads();
  }

  /* ─── SIMPLE REFERENCE LINKS ─────────────────────────────────── */
  function buildSimpleRefs(refs) {
    if (!refs || !refs.length) return '';
    
    const links = refs.map((r, idx) => {
      // Use breadcrumb for display (matches your design)
      const displayText = r.breadcrumb || r.title || 'Document';
      
      return `<a href="#" class="ref-link" data-url="${r.url}" data-title="${r.title}" data-idx="${idx}">${displayText}</a>`;
    }).join('');
    
    return `
      <div class="refs-section">
        <strong>References</strong>
        <div class="refs-list">${links}</div>
      </div>
    `;
  }

  // Copy control shown at the end of every assistant answer. Icon-only; the
  // label lives in the tooltip (title). Swaps to a check on success.
  const ICON_COPY = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>`;
  const ICON_CHECK = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>`;
  const COPY_BTN = `
      <div class="answer-actions">
        <button class="copy-btn" type="button" title="Copy" aria-label="Copy answer">${ICON_COPY}</button>
      </div>`;

  /* ─── SIMPLE ANSWER DISPLAY ───────────────────────────────────── */
  function buildAnswer(data) {
    const { answer, references } = data;
    const refsHTML = buildSimpleRefs(references);

    return `
      <div class="answer-content">${md.makeHtml(answer)}</div>
      ${refsHTML}
      ${COPY_BTN}
    `;
  }

  /* ─── HISTORY PANEL ────────────────────────────────────────────── */
  async function renderThreads(){
    // Fetch server-side conversation list
    let serverConvs = [];
    try {
      const res = await fetch(`${API_BASE}/conversations`);
      if (res.ok) serverConvs = await res.json();
    } catch(e) { console.warn('Could not fetch conversations:', e); }

    // Merge: build map of id -> title. Server supplies the id list/order, but a
    // locally-generated (intent-based) title takes precedence over the server's
    // raw-first-message title so the history panel shows the nice names.
    const merged = new Map();

    // Add server conversations first (already sorted by last_activity desc)
    for (const c of serverConvs) {
      merged.set(c.conversation_id, c.title || 'Untitled');
    }

    // Overlay localStorage titles (generated names win).
    const localList = JSON.parse(localStorage.getItem(`${STORAGE_PREFIX}THREADS`)||'[]');
    for (const id of localList) {
      const local = localStorage.getItem(`${STORAGE_PREFIX}${id}_title`);
      if (local) merged.set(id, local);
      else if (!merged.has(id)) merged.set(id, 'Untitled');
    }

    // Always include current conversation
    if (!merged.has(CONV_ID)) {
      merged.set(CONV_ID, localStorage.getItem(`${STORAGE_PREFIX}${CONV_ID}_title`) || 'Untitled');
    }

    threadNav.innerHTML = Array.from(merged.entries()).map(([id, t]) => `
        <div class="thread ${id===CONV_ID?'active':''}" data-id="${id}">
          <span class="thread-title">${t}</span>
          <button class="thread-delete" data-delete-id="${id}" title="Delete">✕</button>
        </div>
    `).join('');
  }

  histBtn.addEventListener('click', async ()=>{ await renderThreads(); histPane.classList.add('open'); });
  histClose.addEventListener('click', ()=> histPane.classList.remove('open'));
  
  document.addEventListener('click', e=>{
    if(histPane.classList.contains('open') &&
       !histPane.contains(e.target) &&
       !histBtn.contains(e.target)){
      histPane.classList.remove('open');
    }
  });
  
  threadNav.addEventListener('click', async e => {
    if(e.target.closest('.thread-delete')) {
      e.stopPropagation();
      const id = e.target.closest('.thread-delete').dataset.deleteId;
      if(confirm('Delete this conversation?')) {
        deleteThread(id);
      }
      return;
    }
    
    const d = e.target.closest('.thread');
    if(!d) return;
    
    const newConvId = d.dataset.id;
    const isSameConv = (newConvId === CONV_ID);

    console.log('🔄 Switching to conversation:', newConvId, isSameConv ? '(reload)' : '');

    CONV_ID = newConvId;
    sessionStorage.setItem(`${STORAGE_PREFIX}CONV_ID`, CONV_ID);
    histPane.classList.remove('open');
    await renderThreads();
    messages.innerHTML = '';       // clear before load
    await loadHistory();

    if (!messages.querySelector('.refs-list .ref-link')) resetViewer();
  });

  newBtn.addEventListener('click', async ()=>{
    CONV_ID = newId();
    sessionStorage.setItem(`${STORAGE_PREFIX}CONV_ID`, CONV_ID);
    messages.innerHTML = '';
    showWelcomeScreen();
    await renderThreads();
    resetViewer();
  });

  // Suggestion chips (now a standalone block below the composer) fill the input.
  chatPanel?.addEventListener('click', e => {
    const btn = e.target.closest('.suggestion-btn');
    if (!btn) return;
    input.value = btn.textContent.trim();
    autoGrow();
    updateSendVisibility();
    input.focus();
  });

  // Markdown converter
  const md = new showdown.Converter({
    simplifiedAutoLink: true,
    strikethrough: true,
    tables: true,
    emoji: true,
    smoothLivePreview: true,
    disableForced4SpacesIndentedSublists: true,
    noHeaderId: true
  });

  function esc (s) {
    return s.replace(/[&<>"']/g,ch=>(
      { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[ch]
    ));
  }

  /* ─── LOAD HISTORY ─────────────────────────────────────────────── */
  async function loadHistory(){
    // Clear messages first to prevent accumulation
    messages.innerHTML = '';
    
    console.log('🔄 Loading history for:', CONV_ID);
    
    // Show loading indicator
    const loadingLi = document.createElement('li');
    loadingLi.className = 'msg ai';
    loadingLi.innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
    messages.append(loadingLi);
    
    try {
      // Pass client_id to ensure proper isolation
      const res = await fetch(`${API_BASE}/history?conversation_id=${CONV_ID}&client_id=${CLIENT_ID}`);
      
      if(!res.ok) {
        console.log('History endpoint not configured, status:', res.status);
        loadingLi.remove();
        showWelcomeScreen();
        return;
      }

      const contentType = res.headers.get("content-type");
      if (!contentType || !contentType.includes("application/json")) {
        console.log('History endpoint not ready, content-type:', contentType);
        loadingLi.remove();
        showWelcomeScreen();
        return;
      }
      
      const rows = await res.json();
      console.log('📜 Loaded', rows.length, 'messages');
      
      // Remove loading indicator
      loadingLi.remove();
      
      // Only populate if we have data
      if (rows && rows.length > 0) {
        hideWelcomeScreen();
        // Show this conversation's name in the panel header. Fall back to a
        // generated name from the first user message for older conversations
        // that never stored one.
        let storedName = localStorage.getItem(`${STORAGE_PREFIX}${CONV_ID}_title`);
        if (!storedName || storedName === 'Untitled') {
          const firstUser = rows.find(r => r.role === 'user');
          if (firstUser) storedName = generateChatName(firstUser.content);
        }
        updatePanelTitle(storedName);
        messages.innerHTML = rows.map(r=>{
          if (r.role==='assistant' && r.content.startsWith('[REFS]')){
            return `<li class="msg refs">${r.content.slice(6)}</li>`;
          }
          if (r.role==='assistant'){
            return `<li class="msg ai"><div class="answer-content">${md.makeHtml(r.content)}</div>${COPY_BTN}</li>`;
          }
          return `<li class="msg u">${esc(r.content)}</li>`;
        }).join('');

        messages.scrollTop = messages.scrollHeight;
        // ▶️ Auto-open the first reference (if present in history)
        const firstRef = messages.querySelector('.refs-list .ref-link');
        if (firstRef && !userCollapsedViewer) {
          const url = firstRef.dataset.url;
          const title = firstRef.dataset.title;
          if (url) {
            openDoc(url, title);
            // highlight the active reference
            document.querySelectorAll('.ref-link').forEach(l => l.classList.remove('active'));
            firstRef.classList.add('active');
          }
        }


        console.log('✅ History loaded successfully');
      } else {
        messages.innerHTML = '';
        showWelcomeScreen();
      }

    } catch (err) {
      console.error('❌ Failed to load history:', err.message);
      loadingLi.remove();
      messages.innerHTML = '';
      showWelcomeScreen();
    }
  }

  /* ─── COMPOSER AUTO-RESIZE ────────────────────────────────────── */
  function autoGrow(){
    input.style.height='56px';
    if(input.scrollHeight>56) input.style.height=input.scrollHeight+'px';
  }
  // Show the send button only when there's text to send (like Claude).
  function updateSendVisibility(){
    sendBtn.classList.toggle('visible', input.value.trim().length > 0);
  }
  input.addEventListener('input',()=>{ autoGrow(); updateSendVisibility(); });
  input.addEventListener('keydown',e=>{
    if(e.key==='Enter' && !e.shiftKey){
      e.preventDefault();
      if(input.value.trim()) form.dispatchEvent(new Event('submit'));
    }
  });

  /* ─── ASK HANDLER ───────────────────────────────────────────────── */
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;

    hideWelcomeScreen();

    input.value = '';
    input.style.height = '56px';
    updateSendVisibility();
    sendBtn.disabled = true;

    setTitle(q);

    const userLi = document.createElement('li');
    userLi.className = 'msg u';
    userLi.textContent = q;
    messages.append(userLi);

    const botLi = document.createElement('li');
    botLi.className = 'msg ai';
    botLi.innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
    messages.append(botLi);
    messages.scrollTop = messages.scrollHeight;

    try {
      const res = await fetch(`${API_BASE}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: q,
          client_id: CLIENT_ID,
          conversation_id: CONV_ID
        })
      });

      if (!res.ok) {
        const err = await res.json();
        botLi.innerHTML = `<div style="color:red">Error: ${err.detail}</div>`;
        return;
      }

      const data = await res.json();
      botLi.innerHTML = buildAnswer(data);
      messages.scrollTop = messages.scrollHeight;

      // Auto-open first reference, unless the user deliberately collapsed
      // the panel — in that case respect their choice.
      if (!userCollapsedViewer && data.references && data.references.length > 0) {
        setTimeout(() => {
          openDoc(data.references[0].url, data.references[0].title);
        }, 300);
      }

    } catch (err) {
      botLi.innerHTML = `<div style="color:red">Network error: ${err.message}</div>`;
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  });

  /* ─── DOCUMENT VIEWER ───────────────────────────────────────────── */

  function getViewerUrl(url) {
    const lower = url.toLowerCase();
    if (lower.includes(".docx") || lower.includes(".doc") || lower.includes(".pdf")) {
      return "https://docs.google.com/viewer?url=" + encodeURIComponent(url) + "&embedded=true";
    }
    return url;
  }

  // These docs are Word→HTML exports with a fixed ~780px body width and wide
  // tables, so a narrow panel (e.g. the SAILERP popup embed) gets a page-level
  // horizontal scrollbar. Inject a small responsive sheet so the document fits
  // the panel and wide tables scroll inside their own box instead. Same-origin
  // only; the Google Docs Viewer (DOCX/PDF) is cross-origin and will throw —
  // caught and ignored.
  function injectFitCss(frameEl) {
    try {
      const doc = frameEl.contentDocument;
      if (!doc || !doc.head || doc.getElementById('sms-fit-css')) return;
      const style = doc.createElement('style');
      style.id = 'sms-fit-css';
      // Word→HTML exports render list numbers with a negative indent that
      // pokes past the page's left edge (~ -11px), so they get clipped by the
      // narrow panel. Normalise the body with a left gutter (and border-box so
      // padding does not re-introduce a right-side scrollbar), and let wide
      // tables scroll inside their own box.
      style.textContent = `
        html { overflow-x: hidden; }
        body {
          box-sizing: border-box !important;
          max-width: 100% !important;
          margin: 0 !important;
          padding: 20px 20px 24px 40px !important;
        }
        img, svg, video { max-width: 100% !important; height: auto; }
        table { max-width: 100%; display: block; overflow-x: auto; }
        pre { max-width: 100%; overflow-x: auto; }
      `;
      doc.head.appendChild(style);
    } catch (e) {
      /* cross-origin (Google Docs Viewer) — nothing to inject */
    }
  }

  let currentDocUrl = null;

  function openDoc(url, title) {
    if (!url) return;
    
    if (currentDocUrl === url) {
      console.log('Document already loaded');
      return;
    }
    
    currentDocUrl = url;
    lastDocUrl = url;
    lastDocTitle = title;

    docTitle.textContent = title || 'Loading...';
    docTitle.className = 'doc-loading';

    viewer.classList.add('open');
    userCollapsedViewer = false;
    revealViewerToggle();   // header button appears once a doc exists
    updateToggleUi();       // -> "Hide document panel"
    viewer.classList.add('loading');
    
    const fullUrl = url.startsWith('http') ? url : `${DOC_BASE}${url}`;
    
    console.log('Opening:', fullUrl);
    
    frame.src = getViewerUrl(fullUrl);
    
    const handleLoad = () => {
      console.log('Document loaded');
      viewer.classList.remove('loading');
      docTitle.textContent = title || 'Document';
      docTitle.className = 'doc-loaded';
      injectFitCss(frame);

      frame.removeEventListener('load', handleLoad);
      frame.removeEventListener('error', handleError);
    };
    
    const handleError = () => {
      console.error('Load failed');
      viewer.classList.remove('loading');
      docTitle.textContent = 'Error loading document';
      docTitle.className = '';
      
      frame.removeEventListener('load', handleLoad);
      frame.removeEventListener('error', handleError);
    };
    
    frame.addEventListener('load', handleLoad, { once: true });
    frame.addEventListener('error', handleError, { once: true });
    
    setTimeout(() => {
      if (viewer.classList.contains('loading')) {
        console.log('Timeout - assuming loaded');
        handleLoad();
      }
    }, 8000);
  }

  // Low-level: hide the panel and drop the loaded doc. openDoc() early-returns
  // when currentDocUrl === url, so this must clear it or re-opening no-ops.
  function closeViewer() {
    viewer.classList.remove('open');
    frame.src = '';
    currentDocUrl = null;
    docTitle.textContent = 'Document viewer';
    docTitle.className = '';
  }

  /* ─── HEADER PANEL TOGGLE ───────────────────────────────────────── */
  function revealViewerToggle() { if (viewerToggle) viewerToggle.hidden = false; }
  function hideViewerToggle()   { if (viewerToggle) viewerToggle.hidden = true; }

  function updateToggleUi() {
    if (!viewerToggle) return;
    const shown = viewer.classList.contains('open');
    viewerToggle.title = shown ? 'Hide document panel' : 'Show document panel';
    viewerToggle.classList.toggle('active', shown);
  }

  // User hides the panel (via the header toggle or the X). The header toggle
  // stays visible so the panel can be reopened without clicking a reference.
  function hideViewer() {
    closeViewer();
    userCollapsedViewer = true;   // don't auto-open on the next answer
    updateToggleUi();             // -> "Show document panel"
  }

  // User shows the panel again — reopen the last document.
  function showViewer() {
    if (lastDocUrl) openDoc(lastDocUrl, lastDocTitle);
  }

  // Conversation changed (new / switched / deleted): forget the document and
  // remove the header toggle entirely.
  function resetViewer() {
    lastDocUrl = null;
    lastDocTitle = null;
    userCollapsedViewer = false;
    closeViewer();
    hideViewerToggle();
    updateToggleUi();
  }

  vClose.addEventListener('click', hideViewer);

  if (viewerToggle) {
    viewerToggle.addEventListener('click', () => {
      if (viewer.classList.contains('open')) hideViewer();
      else showViewer();
    });
  }

  // Handle reference link clicks
  messages.addEventListener('click', e => {
    const link = e.target.closest('.ref-link');
    if (!link) return;
    
    e.preventDefault();
    const url = link.dataset.url;
    const title = link.dataset.title;
    
    if (url) {
      openDoc(url, title);

      // Visual feedback - highlight active link
      document.querySelectorAll('.ref-link').forEach(l => l.classList.remove('active'));
      link.classList.add('active');
    }
  });

  // Copy an answer to the clipboard.
  messages.addEventListener('click', e => {
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    const content = btn.closest('.msg.ai')?.querySelector('.answer-content');
    const text = content ? content.innerText.trim() : '';
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      btn.classList.add('copied');
      btn.title = 'Copied';
      btn.innerHTML = ICON_CHECK;
      setTimeout(() => {
        btn.classList.remove('copied');
        btn.title = 'Copy';
        btn.innerHTML = ICON_COPY;
      }, 1500);
    }).catch(err => console.warn('Copy failed:', err));
  });

  /* ─── INIT ──────────────────────────────────────────────────────── */
  loadHistory();
  renderThreads();
  updateSendVisibility();
  input.focus();
});

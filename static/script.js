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

  /* ─── UTILITIES ────────────────────────────────────────────────── */
  function newId () {
    if (typeof globalThis.crypto?.randomUUID === 'function') {
      return 'c_' + globalThis.crypto.randomUUID();
    }
    return 'c_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 10);
  }

  // Storage functions
  function saveThread(id){
    const arr = JSON.parse(localStorage.getItem(`${STORAGE_PREFIX}THREADS`)||'[]');
    if(!arr.includes(id)){ 
      arr.push(id); 
      localStorage.setItem(`${STORAGE_PREFIX}THREADS`, JSON.stringify(arr)); 
    }
  }
  
  function setTitle(line){
    const key = `${STORAGE_PREFIX}${CONV_ID}_title`;
    const cur = localStorage.getItem(key)||'';
    if(!cur || cur === 'Untitled'){ 
      localStorage.setItem(key, line.slice(0,30)); 
      saveThread(CONV_ID); 
    }
  }
  
  async function deleteThread(id) {
    const arr = JSON.parse(localStorage.getItem(`${STORAGE_PREFIX}THREADS`)||'[]');
    const filtered = arr.filter(tid => tid !== id);
    localStorage.setItem(`${STORAGE_PREFIX}THREADS`, JSON.stringify(filtered));

    localStorage.removeItem(`${STORAGE_PREFIX}${id}_title`);

    if (id === CONV_ID) {
      CONV_ID = newId();
      sessionStorage.setItem(`${STORAGE_PREFIX}CONV_ID`, CONV_ID);
      messages.innerHTML = '';
      closeViewer();
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
    closeViewer();
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

  /* ─── SIMPLE ANSWER DISPLAY ───────────────────────────────────── */
  function buildAnswer(data) {
    const { answer, references } = data;
    const refsHTML = buildSimpleRefs(references);
    
    return `
      <div class="answer-content">${md.makeHtml(answer)}</div>
      ${refsHTML}
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

    // Merge: build map of id -> title (server takes priority)
    const merged = new Map();

    // Add server conversations first (already sorted by last_activity desc)
    for (const c of serverConvs) {
      merged.set(c.conversation_id, c.title || 'Untitled');
    }

    // Add localStorage conversations that aren't already in the map
    const localList = JSON.parse(localStorage.getItem(`${STORAGE_PREFIX}THREADS`)||'[]');
    for (const id of localList) {
      if (!merged.has(id)) {
        merged.set(id, localStorage.getItem(`${STORAGE_PREFIX}${id}_title`) || 'Untitled');
      }
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

    if (!messages.querySelector('.refs-list .ref-link')) closeViewer();
  });

  newBtn.addEventListener('click', async ()=>{
    CONV_ID = newId();
    sessionStorage.setItem(`${STORAGE_PREFIX}CONV_ID`, CONV_ID);
    messages.innerHTML = '';
    await renderThreads();
    closeViewer();
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
        return;
      }
      
      const contentType = res.headers.get("content-type");
      if (!contentType || !contentType.includes("application/json")) {
        console.log('History endpoint not ready, content-type:', contentType);
        loadingLi.remove();
        return;
      }
      
      const rows = await res.json();
      console.log('📜 Loaded', rows.length, 'messages');
      
      // Remove loading indicator
      loadingLi.remove();
      
      // Only populate if we have data
      if (rows && rows.length > 0) {
        messages.innerHTML = rows.map(r=>{
          if (r.role==='assistant' && r.content.startsWith('[REFS]')){
            return `<li class="msg refs">${r.content.slice(6)}</li>`;
          }
          const cls  = r.role==='user' ? 'u' : 'ai';
          const html = r.role==='assistant'
                       ? md.makeHtml(r.content)
                       : esc(r.content);
          return `<li class="msg ${cls}">${html}</li>`;
        }).join('');

        messages.scrollTop = messages.scrollHeight;
        // ▶️ Auto-open the first reference (if present in history)
        const firstRef = messages.querySelector('.refs-list .ref-link');
        if (firstRef) {
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
        messages.innerHTML = '<li class="msg ai"><em>No messages in this conversation yet.</em></li>';
      }
      
    } catch (err) {
      console.error('❌ Failed to load history:', err.message);
      loadingLi.remove();
      messages.innerHTML = '';
    }
  }

  /* ─── COMPOSER AUTO-RESIZE ────────────────────────────────────── */
  function autoGrow(){
    input.style.height='46px';
    if(input.scrollHeight>46) input.style.height=input.scrollHeight+'px';
  }
  input.addEventListener('input',autoGrow);
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

    input.value = '';
    input.style.height = '46px';
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

      // Auto-open first reference
      if (data.references && data.references.length > 0) {
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
  let currentDocUrl = null;

  function openDoc(url, title) {
    if (!url) return;
    
    if (currentDocUrl === url) {
      console.log('Document already loaded');
      return;
    }
    
    currentDocUrl = url;
    
    docTitle.textContent = title || 'Loading...';
    docTitle.className = 'doc-loading';
    
    viewer.classList.add('visible');
    viewer.classList.add('loading');
    
    const fullUrl = url.startsWith('http') ? url : `${DOC_BASE}${url}`;
    
    console.log('Opening:', fullUrl);
    
    frame.src = fullUrl;
    
    const handleLoad = () => {
      console.log('Document loaded');
      viewer.classList.remove('loading');
      docTitle.textContent = title || 'Document';
      docTitle.className = 'doc-loaded';
      
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

  function closeViewer() {
    viewer.classList.remove('visible');
    frame.src = '';
    currentDocUrl = null;
    docTitle.textContent = 'Document viewer';
    docTitle.className = '';
  }

  vClose.addEventListener('click', closeViewer);

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

  /* ─── INIT ──────────────────────────────────────────────────────── */
  loadHistory();
  renderThreads();
  input.focus();
});

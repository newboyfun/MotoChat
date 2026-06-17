(function() {
  const origFetch = window.fetch;
  window.fetch = function(input, init) {
    init = init || {};
    init.headers = Object.assign({}, init.headers || {});
    const token = localStorage.getItem('kc_token');
    if (token && !init.headers['Authorization'] && !init.headers['authorization']) init.headers['Authorization'] = 'Bearer ' + token;
    return origFetch(input, init);
  };
})();
(function() {
  'use strict';
  const $ = (s, c) => (c||document).querySelector(s);
  const $$ = (s, c) => [...(c||document).querySelectorAll(s)];
  const S = { cid:'default', streaming:false, currentBubble:null, fullReply:'', currentPersona:null };

  const titles = {home:'首页',chat:'对话',role:'我的角色',library:'性格库',notifications:'通知',account:'我的账号'};
  window.goToPage = function(page) {
    S.page = page;
    $$('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.page === page));
    $$('.page').forEach(pEl => {
      const isActive = pEl.id === 'page-' + page;
      pEl.classList.toggle('active', isActive);
      if (isActive) { pEl.classList.remove('fade-in'); void pEl.offsetWidth; pEl.classList.add('fade-in'); }
    });
    const el = document.getElementById('pageTitle');
    if (el) el.textContent = titles[page] || page;
    document.getElementById('sidebar')?.classList.remove('open');
    const overlay = document.getElementById('sidebarOverlay');
    if (overlay) overlay.classList.remove('open');
    if (page === 'home') loadHome();
    if (page === 'chat') { const inp = $('#msgInput'); if (inp) inp.focus(); scrollChat(); }
    if (page === 'role' && S.currentPersona) { updatePersonaHeader(S.currentPersona); syncChatHeaderFromPersona(S.currentPersona); loadUserPersonaDetail(S.currentPersona); loadPersonaPreview(S.currentPersona); }
    if (page === 'library') loadUserLibraryPage();
    if (page === 'notifications') loadNotifications();
    if (page === 'account') refreshAccountPage();
  };

  document.addEventListener('DOMContentLoaded', async () => {
    bindChat(); bindLogout(); bindUserPersona(); bindTheme(); bindSidebar();
    let splashHidden = false;
    function hideSplash() {
      if (splashHidden) return;
      splashHidden = true;
      const sp = document.getElementById('splash');
      if (sp) sp.classList.add('hidden');
    }
    setTimeout(hideSplash, 800);
    try {
      const token = localStorage.getItem('kc_token');
      if (!token) { hideSplash(); location.href='/login'; return; }
      const r = await fetch('/api/auth/me');
      if (!r.ok) { localStorage.removeItem('kc_token'); hideSplash(); location.href='/login'; return; }
      const me = await r.json();
      if (!me || !me.username || me.role !== 'user') { localStorage.removeItem('kc_token'); hideSplash(); location.href='/login'; return; }
      S.me = me;
      if (me.conversation_id) S.cid = me.conversation_id;
      hideSplash();
      const ly = document.getElementById('layout');
      if (ly) ly.style.display = 'flex';
      if (me.username) {
        const hu = document.getElementById('homeUsername'); if (hu) hu.textContent = me.username;
        const an = document.getElementById('accountName'); if (an) an.textContent = me.username;
      }
      if (me.persona) {
        updatePersonaHeader(me.persona); syncChatHeaderFromPersona(me.persona);
        loadUserPersonaDetail(me.persona); loadPersonaPreview(me.persona);
        S.currentPersona = me.persona;
      }
      loadStats(); loadHistory(); refreshTokenUsage(); checkConnection(); loadUserPersonaList(); refreshAccountPage();
      window.goToPage('home');
    } catch(e) { hideSplash(); location.href='/login'; }
  });

  function bindTheme() {
    const btn = document.getElementById('themeToggle');
    if (!btn) return;
    updateThemeIcon();
    btn.addEventListener('click', function() {
      window.mcTheme ? window.mcTheme.toggle() : null;
      updateThemeIcon();
    });
  }
  function bindSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebarOverlay');
    const menuBtn = document.getElementById('menuBtn');
    const closeBtn = document.getElementById('sidebarCloseBtn');
    if (!sidebar) return;
    function open() { sidebar.classList.add('open'); if (overlay) overlay.classList.add('open'); }
    function close() { sidebar.classList.remove('open'); if (overlay) overlay.classList.remove('open'); }
    if (menuBtn) menuBtn.addEventListener('click', open);
    if (closeBtn) closeBtn.addEventListener('click', close);
    if (overlay) overlay.addEventListener('click', close);
    window._sidebarClose = close;
  }
  function updateThemeIcon() {
    const isDark = document.documentElement.hasAttribute('data-theme');
    const sun = document.getElementById('themeIconSun');
    const moon = document.getElementById('themeIconMoon');
    if (sun) sun.style.display = isDark ? 'none' : '';
    if (moon) moon.style.display = isDark ? '' : 'none';
  }

  function bindLogout() {
    const logoutAction = async () => { try { await fetch('/api/auth/logout', {method:'POST'}); } catch(e) {} localStorage.removeItem('kc_token'); location.href='/login'; };
    const topBtn = document.getElementById('logoutBtn');
    if (topBtn) { topBtn.style.display = ''; topBtn.addEventListener('click', logoutAction); }
    const pageBtn = document.getElementById('logoutBtnPage');
    if (pageBtn) pageBtn.addEventListener('click', logoutAction);
  }

  function updatePersonaHeader(name) {
    const avatar = document.getElementById('personaAvatar');
    const displayName = document.getElementById('personaDisplayName');
    const welcome = document.getElementById('personaWelcome');
    if (avatar) avatar.textContent = name ? name.charAt(0).toUpperCase() : 'U';
    if (displayName) displayName.textContent = name || '角色';
    if (welcome) welcome.textContent = name ? ('你好，我是 ' + name + '，来聊天吧。') : '';
  }

  function syncChatHeaderFromPersona(name) {
    const first = name ? name.charAt(0).toUpperCase() : 'U';
    ['chatAvatar','welcomeAvatar','miniAvatar'].forEach(id => { const el = document.getElementById(id); if (el) el.textContent = first; });
    ['chatName','welcomeName','miniName'].forEach(id => { const el = document.getElementById(id); if (el) el.textContent = name || ''; });
  }

  async function loadUserPersonaList() {
    try {
      const r = await fetch('/api/user/personas');
      const d = await r.json();
      const el = document.getElementById('userPersonaList');
      if (!el) return;
      const personas = d.personas || [];
      S.currentPersona = d.current || S.currentPersona;
      el.innerHTML = personas.map(p => '<button class="btn" data-name="' + p.name + '" style="border-color:' + (p.active ? 'var(--accent)' : 'var(--border)') + ';color:' + (p.active ? 'var(--accent)' : 'inherit') + '">' + escapeHtml(p.name) + '</button>').join('');
      el.querySelectorAll('button[data-name]').forEach(btn => btn.addEventListener('click', () => selectUserPersona(btn.dataset.name)));
      if (S.currentPersona) { updatePersonaHeader(S.currentPersona); syncChatHeaderFromPersona(S.currentPersona); loadUserPersonaDetail(S.currentPersona); loadPersonaPreview(S.currentPersona); }
      // Check for pending overrides and show badge
      try {
        const pr = await fetch('/api/user/pending-overrides');
        const pd = await pr.json();
        const pending = pd.pending || {};
        const count = Object.keys(pending).length;
        // Update notification badge in sidebar
        let badge = document.getElementById('notifBadge');
        if (!badge) {
          const navBtn = document.querySelector('[data-page="notifications"]');
          if (navBtn) {
            badge = document.createElement('span');
            badge.id = 'notifBadge';
            badge.style.cssText = 'position:absolute;top:4px;right:4px;width:8px;height:8px;border-radius:50%;background:var(--red);display:none';
            navBtn.style.position = 'relative';
            navBtn.appendChild(badge);
          }
        }
        if (badge) badge.style.display = count > 0 ? 'block' : 'none';
      } catch(e) {}
    } catch(e) { console.error('loadUserPersonaList error', e); }
  }

  async function selectUserPersona(name) {
    try {
      const r = await fetch('/api/user/persona/select', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
      const d = await r.json();
      if (d.success) { toast('角色','已切换到 '+name,'success'); S.currentPersona=name; updatePersonaHeader(name); syncChatHeaderFromPersona(name); loadUserPersonaList(); loadStats(); loadHistory(); loadPersonaPreview(name); } else { toast('错误', d.error || '切换失败','error'); }
    } catch(e) { toast('错误','切换失败','error'); }
  }

  async function loadUserPersonaDetail(name) {
    try {
      const r = await fetch('/api/user/persona/' + encodeURIComponent(name));
      const d = await r.json();
      const title = document.getElementById('userPersonaTitle');
      const el = document.getElementById('userPersona');
      let statusText = '';
      if (d.pending_content) statusText = '（待审批：管理员有新设定）';
      else if (d.user_content) statusText = '（当前生效：用户覆盖）';
      else statusText = '（当前生效：默认）';
      if (title) title.textContent = '- ' + name + statusText;
      if (el) el.value = d.user_content || d.base_content || '';
      // Show pending notice if exists
      let pendingNotice = document.getElementById('personaPendingNotice');
      if (!pendingNotice) {
        const parent = el ? el.parentElement : null;
        if (parent) {
          pendingNotice = document.createElement('div');
          pendingNotice.id = 'personaPendingNotice';
          pendingNotice.style.cssText = 'margin-top:8px;padding:10px;border-radius:8px;font-size:13px;display:none';
          parent.appendChild(pendingNotice);
        }
      }
      if (pendingNotice) {
        if (d.pending_content) {
          pendingNotice.style.display = 'block';
          pendingNotice.style.background = 'rgba(99,102,241,0.08)';
          pendingNotice.style.border = '1px solid var(--accent)';
          pendingNotice.style.color = 'var(--accent)';
          pendingNotice.innerHTML = '管理员为 <strong>' + escapeHtml(name) + '</strong> 提交了新设定，请前往 <a href="javascript:goToPage(%27notifications%27)" style="color:var(--accent);text-decoration:underline">通知页面</a> 审批。';
        } else {
          pendingNotice.style.display = 'none';
        }
      }
    } catch(e) { console.error('loadUserPersonaDetail error', e); }
  }

  async function loadPersonaPreview(name) {
    if (!name) return;
    try {
      const r = await fetch('/api/user/persona/' + encodeURIComponent(name) + '/preview');
      const d = await r.json();
      const el = document.getElementById('personaMemoryPreview');
      const mem = d.memory || [];
      if (!el) return;
      if (!mem.length) { el.innerHTML = '<div style="color:var(--text-muted)">暂无记忆</div>'; return; }
      el.innerHTML = mem.map(m => '<div style="padding:4px 0;border-bottom:1px solid var(--border)"><b>' + escapeHtml(m.role) + '</b>: ' + escapeHtml(m.content) + '</div>').join('');
    } catch(e) { console.error('loadPersonaPreview', e); }
  }

  function bindUserPersona() {
    const btn = document.getElementById('applyPersonaBtn');
    if (btn) btn.addEventListener('click', saveUserPersona);
    const baseBtn = document.getElementById('loadBasePersonaBtn');
    if (baseBtn) baseBtn.addEventListener('click', loadBasePersona);
    const resetBtn = document.getElementById('resetPersonaBtn');
    if (resetBtn) resetBtn.addEventListener('click', resetUserPersona);
  }

  async function saveUserPersona() {
    if (!S.currentPersona) { toast('提示','请先选择角色','info'); return; }
    const content = ((document.getElementById('userPersona')||{}).value||'').trim();
    try {
      const r = await fetch('/api/user/persona/save', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name: S.currentPersona, content})});
      const d = await r.json();
      if (d.success) { toast('成功','已保存为你的专属性格','success'); } else { toast('错误', d.error || '保存失败','error'); }
    } catch(e) { toast('错误','保存失败','error'); }
  }

  async function loadBasePersona() {
    if (!S.currentPersona) { toast('提示','请先选择角色','info'); return; }
    try {
      const r = await fetch('/api/user/persona/' + encodeURIComponent(S.currentPersona));
      const d = await r.json();
      const el = document.getElementById('userPersona');
      if (el) el.value = d.base_content || '';
      toast('提示','已读取默认性格','info');
    } catch(e) { toast('错误','读取失败','error'); }
  }

  async function resetUserPersona() {
    if (!S.currentPersona) { toast('提示','请先选择角色','info'); return; }
    try {
      const r = await fetch('/api/user/persona/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name: S.currentPersona})});
      const d = await r.json();
      if (d.success) { toast('成功','已恢复默认','success'); loadUserPersonaDetail(S.currentPersona); } else { toast('错误', d.error || '恢复失败','error'); }
    } catch(e) { toast('错误','恢复失败','error'); }
  }

  window.sendQuick = function(text) {
    goToPage('chat');
    setTimeout(function() {
      const input = $('#msgInput');
      if (input) { input.value = text; input.dispatchEvent(new Event('input', {bubbles: true})); }
      const btn = $('#sendBtn');
      if (btn && !btn.disabled) btn.click();
    }, 200);
  };

  /* ===== CHAT ===== */
  function bindChat() {
    const input = $('#msgInput');
    const btn = $('#sendBtn');
    if (!input || !btn) return;
    input.addEventListener('input', () => { btn.disabled = !input.value.trim() || S.streaming; input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,120)+'px'; });
    input.addEventListener('keydown', e => { if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); sendMsg(); } });
    btn.addEventListener('click', sendMsg);
    const attachBtn = $('#attachBtn');
    const imageInput = $('#imageInput');
    if (attachBtn && imageInput) { attachBtn.addEventListener('click', () => imageInput.click()); imageInput.addEventListener('change', handleImage); }
  }

  function sendMsg() {
    const input = $('#msgInput');
    const btn = $('#sendBtn');
    if (!input) return;
    const text = input.value.trim();
    if (!text || S.streaming) return;
    input.value=''; input.style.height='auto'; if (btn) btn.disabled=true;
    removeWelcome(); addMsg('user', text); sendStream(text);
  }

  async function sendStream(text) {
    S.streaming=true; S.fullReply=''; S.currentBubble=null;
    const container = $('#chatMessages');
    if (container) {
      const div=document.createElement('div'); div.className='msg assistant';
      div.innerHTML='<div class="msg-avatar">'+getAvatarChar()+'</div><div class="msg-body"><div class="msg-bubble typing-indicator"><span></span><span></span><span></span></div></div>';
      container.appendChild(div); S.currentBubble=div; scrollChat();
    }
    try {
      const response = await fetch('/api/chat/stream', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message:text, conversation_id:S.cid})});
      const reader=response.body.getReader(); const decoder=new TextDecoder(); let buffer='';
      while (true) {
        const {done,value}=await reader.read(); if (done) break;
        buffer+=decoder.decode(value,{stream:true}); const lines=buffer.split('\n'); buffer=lines.pop(); let eventType='';
        for (const line of lines) {
          if (line.startsWith('event: ')) eventType=line.substring(7).trim();
          else if (line.startsWith('data: ')) {
            const rawData=line.substring(6);
            try {
              const data=JSON.parse(rawData);
              if (eventType==='chunk' && data.text) { S.fullReply+=data.text; if (S.currentBubble) { const bubble=S.currentBubble.querySelector('.msg-bubble'); if (bubble) { bubble.classList.remove('typing-indicator'); bubble.innerHTML=renderEmotions(escapeHtml(S.fullReply)); } } scrollChat(); }
              else if (eventType==='end') finishReply();
              else if (eventType==='error') { if (S.currentBubble) { const bubble=S.currentBubble.querySelector('.msg-bubble'); if (bubble) bubble.innerHTML='❌'+escapeHtml(data.error||'处理出错'); } }
            } catch(e) { console.error('parse error', e, rawData); }
          }
        }
      }
    } catch(e) {
      if (S.currentBubble) { const bubble=S.currentBubble.querySelector('.msg-bubble'); if (bubble) bubble.innerHTML='❌ 连接失败: '+escapeHtml(e.message); }
      S.streaming=false;
    }
    const btn=$('#sendBtn'); if (btn) btn.disabled=false;
  }

  function finishReply() {
    if (S.currentBubble && S.fullReply) {
      // 处理 [SPLIT] 标记，拆分成多条消息
      const parts = S.fullReply.split('[SPLIT]').map(p => p.trim()).filter(p => p.length > 0);
      
      if (parts.length > 1) {
        // 移除当前气泡的父元素（整条消息）
        const msgDiv = S.currentBubble.closest('.msg');
        if (msgDiv) msgDiv.remove();
        
        // 添加拆分后的多条消息
        for (const part of parts) {
          const div = document.createElement('div');
          div.className = 'msg assistant';
          const avatar = document.createElement('div');
          avatar.className = 'msg-avatar';
          avatar.textContent = getAvatarChar();
          const body = document.createElement('div');
          body.className = 'msg-body';
          const bubble = document.createElement('div');
          bubble.className = 'msg-bubble';
          bubble.innerHTML = renderEmotions(escapeHtml(part));
          body.appendChild(bubble);
          const time = document.createElement('div');
          time.className = 'msg-time';
          time.textContent = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});
          body.appendChild(time);
          const ttsBtn = document.createElement('button');
          ttsBtn.className = 'msg-tts';
          ttsBtn.textContent = '🔊';
          ttsBtn.title = '朗读';
          ttsBtn.onclick = () => playTTS(part);
          body.appendChild(ttsBtn);
          div.appendChild(avatar);
          div.appendChild(body);
          $('#chatMessages').appendChild(div);
        }
      } else {
        // 没有 [SPLIT] 标记，保持原样
        const bubble = S.currentBubble.querySelector('.msg-bubble');
        if (bubble) {
          bubble.classList.remove('typing-indicator');
          bubble.innerHTML = renderEmotions(escapeHtml(S.fullReply));
        }
        const body = S.currentBubble.querySelector('.msg-body');
        if (body) {
          const time = document.createElement('div');
          time.className = 'msg-time';
          time.textContent = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});
          body.appendChild(time);
          const ttsBtn = document.createElement('button');
          ttsBtn.className = 'msg-tts';
          ttsBtn.textContent = '🔊';
          ttsBtn.title = '朗读';
          ttsBtn.onclick = () => playTTS(S.fullReply);
          body.appendChild(ttsBtn);
        }
      }
    }
    S.currentBubble = null;
    S.streaming = false;
    refreshTokenUsage();
    scrollChat();
    const btn = $('#sendBtn');
    if (btn) btn.disabled = false;
  }

  async function handleImage(e) {
    const file=e.target.files[0]; if (!file) return; e.target.value=''; removeWelcome(); addMsg('user','[图片] '+file.name);
    const fd=new FormData(); fd.append('file',file);
    try { const r=await fetch('/api/images/recognize',{method:'POST',body:fd}); const d=await r.json(); addMsg('assistant',d.recognized||'识别失败'); } catch(err) { addMsg('assistant','图片识别出错: '+err.message); }
  }

  function addMsg(role, content, createdAt) {
    const container=$('#chatMessages'); if (!container) return null;

    // 处理 [SPLIT] 标记，拆分成多条消息
    const parts = content.split('[SPLIT]').map(p => p.trim()).filter(p => p.length > 0);
    const bubbles = [];

    // 格式化时间：优先用数据库时间，否则取当前时间
    const timeStr = createdAt
      ? new Date(createdAt).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'})
      : new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});

    for (const part of parts) {
      const div=document.createElement('div'); div.className='msg '+role;
      const avatar=document.createElement('div'); avatar.className='msg-avatar'; avatar.textContent=role==='user'?'你':getAvatarChar();
      const body=document.createElement('div'); body.className='msg-body';
      const bubble=document.createElement('div'); bubble.className='msg-bubble'; bubble.innerHTML=renderEmotions(escapeHtml(part));
      const time=document.createElement('div'); time.className='msg-time'; time.textContent=timeStr;
      body.appendChild(bubble); body.appendChild(time);
      if (role==='assistant') { const ttsBtn=document.createElement('button'); ttsBtn.className='msg-tts'; ttsBtn.textContent='🔊'; ttsBtn.title='朗读'; ttsBtn.onclick=()=>playTTS(part); body.appendChild(ttsBtn); }
      div.appendChild(avatar); div.appendChild(body); container.appendChild(div);
      bubbles.push(bubble);
    }

    scrollChat();
    return bubbles.length === 1 ? bubbles[0] : bubbles;
  }

  function removeWelcome() { const w=$('#msgWelcome'); if (w) w.remove(); }
  function scrollChat() { const el=$('#chatMessages'); if (el) requestAnimationFrame(()=>el.scrollTop=el.scrollHeight); }
  function getAvatarChar() { const el=$('#chatAvatar'); return (el && el.textContent) || 'M'; }

  async function loadHistory() {
    try {
      const r=await fetch('/api/history/'+encodeURIComponent(S.cid)); const d=await r.json(); const msgs=d.messages||[]; if (!msgs.length) return;
      removeWelcome(); const container=$('#chatMessages'); if (!container) return; container.innerHTML=''; msgs.forEach(m=>addMsg(m.role, m.content, m.created_at));
    } catch(e) { console.error('loadHistory error', e); }
  }

  async function loadStats() {
    try {
      const [userRes] = await Promise.all([
        fetch('/api/user/home-stats')
      ]);
      const userData = userRes.ok ? await userRes.json() : {};
      const persona = S.currentPersona || userData.persona || '';
      ['miniName','personaDisplayName','welcomeName','homePersona','accountPersona'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = persona || '-';
      });
      const first = (persona || 'U').charAt(0).toUpperCase();
      ['miniAvatar','personaAvatar','welcomeAvatar','homeAvatar','accountAvatar'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = first;
      });
      updatePersonaHeader(persona);
      syncChatHeaderFromPersona(persona);
      if (userData.conversation_id) S.cid = userData.conversation_id;
      const messageCount = userData.message_count ?? '-';
      const hm = document.getElementById('homeTotalMessages');
      if (hm) hm.textContent = messageCount;
      const ht = document.getElementById('homeTodayChats');
      if (ht) ht.textContent = '-';
    } catch(e) { console.error('loadStats error', e); }
  }

  async function refreshTokenUsage() {
    try {
      const r = await fetch('/api/token-usage');
      const d = await r.json();
      const u = d.usage || {};
      const chatEl = document.getElementById('chatTokenDisplay');
      if (chatEl) chatEl.textContent = u.total_tokens || '-';
      const homeEl = document.getElementById('homeTokenUsage');
      if (homeEl) homeEl.textContent = u.total_tokens || '-';
    } catch(e) {}
  }

  async function checkConnection() {
    try { const r=await fetch('/api/user/home-stats',{signal:AbortSignal.timeout(3000)}); updateStatus(r.ok); } catch(e) { updateStatus(false); }
    setTimeout(checkConnection,10000);
  }
  function updateStatus(ok) {
    const el=document.getElementById('wsStatus'); if (!el) return; el.textContent=ok?'● 已连接':'● 未连接'; el.style.color=ok?'var(--green)':'var(--red)';
  }

  async function playTTS(text) {
    if (!text) return;
    try { const r=await fetch('/api/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})}); const d=await r.json(); if (d.audio_url) new Audio(d.audio_url).play(); } catch(e) { console.error('TTS error', e); }
  }

  function toast(t,m,tp) {
    const c=document.getElementById('toasts'); if (!c) return;
    const d=document.createElement('div'); d.className='toast toast-'+(tp||'info'); d.innerHTML='<div class="toast-title">'+escapeHtml(t)+'</div><div class="toast-msg">'+escapeHtml(m)+'</div>';
    c.appendChild(d); setTimeout(()=>{d.classList.add('fade-out'); setTimeout(()=>d.remove(),300);},3000);
  }
  window.toast=toast;
  // Page navigation binding
  $$('.nav-btn[data-page]').forEach(btn => btn.addEventListener('click', () => window.goToPage(btn.dataset.page)));

  function escapeHtml(s) { return s ? s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : ''; }
  function renderEmotions(text) { return text ? text.replace(/\n/g,'<br>') : ''; }

  async function loadHome() {
    try {
      const r = await fetch('/api/history/' + encodeURIComponent(S.cid) + '?limit=5');
      const d = await r.json();
      const msgs = d.messages || [];
      const el = document.getElementById('homeRecentMessages');
      if (!el) return;
      if (!msgs.length) { el.innerHTML = '<span style="color:var(--text-muted)">还没有对话，点击上方按钮开始聊天。</span>'; return; }
      el.innerHTML = msgs.slice(-5).reverse().map(m =>
        '<div style="padding:8px 0;border-bottom:1px solid var(--border)"><span style="color:var(--accent);font-weight:600;margin-right:6px">' + escapeHtml(m.role === 'user' ? '我' : (S.currentPersona || '角色')) + '</span>' + escapeHtml((m.content || '').slice(0, 80)) + '</div>'
      ).join('');
    } catch (e) { console.error('loadHome error', e); }
  }

  async function loadUserLibraryPage() {
    const grid = document.getElementById('userLibraryGrid');
    if (!grid) return;
    grid.innerHTML = '<div style="color:var(--text-muted)">加载中...</div>';
    try {
      const r = await fetch('/api/user/persona-library');
      const d = await r.json();
      if (d.access_denied) { grid.innerHTML = '<div style="color:var(--text-muted)">还没有访问性格库的权限，请联系管理员开通。</div>'; return; }
      const items = d.library || [];
      if (!items.length) { grid.innerHTML = '<div style="color:var(--text-muted)">暂无可用模板。</div>'; return; }
      grid.innerHTML = items.map(item =>
        '<div style="background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:14px;cursor:pointer" onclick="applyLibraryItem(\'' + escapeHtml(item.name).replace(/'/g, "\'") + '\')">'
        + '<div style="font-weight:700;margin-bottom:6px">' + escapeHtml(item.name) + (item.persona_name ? ' <span style="color:var(--text-muted);font-size:12px">(' + escapeHtml(item.persona_name) + ')</span>' : '') + '</div>'
        + '<div style="font-size:13px;color:var(--text-muted);line-height:1.7;max-height:72px;overflow:hidden">' + escapeHtml(item.content || '') + '</div>'
        + '</div>'
      ).join('');
    } catch (e) { grid.innerHTML = '<div style="color:var(--red)">加载失败</div>'; }
  }

  async function loadNotifications() {
    const el = document.getElementById('notificationsList');
    if (!el) return;
    el.innerHTML = '<div style="color:var(--text-muted)">\u52a0\u8f7d\u4e2d...</div>';
    try {
      const [notifRes, pendingRes] = await Promise.all([
        fetch('/api/notifications/' + encodeURIComponent(S.cid)),
        fetch('/api/user/pending-overrides')
      ]);
      const nd = await notifRes.json();
      const pd = await pendingRes.json();
      const items = nd.notifications || [];
      const pending = pd.pending || {};
      let html = '';
      for (const [persona, content] of Object.entries(pending)) {
        html += '<div style="padding:16px;background:var(--surface);border:2px solid var(--accent);border-radius:12px;margin-bottom:10px">'
          + '<div style="margin-bottom:8px"><span style="font-weight:700;color:var(--accent)">\ud83d\udd14 \u5f85\u5ba1\u6279\uff1a\u7ba1\u7406\u5458\u66f4\u65b0\u4e86\u89d2\u8272 ' + escapeHtml(persona) + ' \u7684\u8bbe\u5b9a</span></div>'
          + '<div style="font-size:13px;color:var(--text-muted);line-height:1.7;margin-bottom:10px;max-height:120px;overflow:auto;background:var(--bg);border-radius:8px;padding:10px">' + escapeHtml(content || '(\u7a7a\u5185\u5bb9)') + '</div>'
          + '<div style="display:flex;gap:8px">'
          + '<button class="btn btn-primary" onclick="approveOverride(\'' + escapeHtml(persona).replace(/'/g, "\\'") + '\', true)">\u540c\u610f\u5e94\u7528</button>'
          + '<button class="btn" style="color:var(--red)" onclick="approveOverride(\'' + escapeHtml(persona).replace(/'/g, "\\'") + '\', false)">\u62d2\u7edd</button>'
          + '</div></div>';
      }
      for (const n of items) {
        html += '<div style="padding:14px;background:var(--surface);border:1px solid var(--border);border-radius:12px;margin-bottom:10px">'
          + '<div style="display:flex;justify-content:space-between;gap:8px;margin-bottom:6px"><div style="font-weight:600">' + escapeHtml(n.title || '\u901a\u77e5') + '</div><div style="color:var(--text-muted);font-size:12px">' + escapeHtml(n.created_at || '') + '</div></div>'
          + '<div style="font-size:13px;color:var(--text-muted);line-height:1.7">' + escapeHtml(n.content || '') + '</div>'
          + '</div>';
      }
      if (!html) { el.innerHTML = '<div style="padding:16px;background:var(--surface);border:1px solid var(--border);border-radius:12px;color:var(--text-muted)">\u6682\u65f6\u6ca1\u6709\u901a\u77e5\u3002</div>'; return; }
      el.innerHTML = html;
    } catch (e) { el.innerHTML = '<div style="color:var(--red)">\u52a0\u8f7d\u901a\u77e5\u5931\u8d25</div>'; }
  }

  window.approveOverride = async function(persona, approve) {
    try {
      const r = await fetch('/api/user/approve-override', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({persona, approve})});
      const d = await r.json();
      if (r.ok && d.success) {
        toast(approve ? '\u5df2\u540c\u610f' : '\u5df2\u62d2\u7edd', approve ? '\u89d2\u8272\u8bbe\u5b9a\u5df2\u5e94\u7528' : '\u89d2\u8272\u8bbe\u5b9a\u5df2\u62d2\u7edd', 'success');
        loadNotifications();
        if (approve && S.currentPersona === persona) { loadUserPersonaDetail(persona); loadPersonaPreview(persona); }
      } else { toast('\u9519\u8bef', d.error || '\u64cd\u4f5c\u5931\u8d25', 'error'); }
    } catch (e) { toast('\u9519\u8bef', '\u7f51\u7edc\u5f02\u5e38', 'error'); }
  };


  async function refreshAccountPage() {
    const name = document.getElementById('accountName');
    if (name) name.textContent = S.me?.username || '??';
    const cid = document.getElementById('accountId');
    if (cid) cid.textContent = S.cid || '-';
    const persona = document.getElementById('accountPersona');
    if (persona) persona.textContent = S.currentPersona || '-';
    try {
      const r = await fetch('/api/user/home-stats');
      if (r.ok) {
        const d = await r.json();
        if (d.conversation_id) S.cid = d.conversation_id;
        const cidEl = document.getElementById('accountId');
        if (cidEl) cidEl.textContent = S.cid || '-';
        if (d.persona) {
          S.currentPersona = d.persona;
          const personaEl = document.getElementById('accountPersona');
          if (personaEl) personaEl.textContent = d.persona;
        }
        const totalEl = document.getElementById('accountTotalMessages');
        if (totalEl) totalEl.textContent = d.message_count ?? '-';
      }
    } catch (e) { console.error('refreshAccountPage error', e); }
    const todayEl = document.getElementById('accountTodayChats');
    if (todayEl) todayEl.textContent = '-';
  }

  window.doChangePassword = async function() {
    const oldPassword = (document.getElementById('oldPassword')?.value || '').trim();
    const newPassword = (document.getElementById('newPassword')?.value || '').trim();
    if (!oldPassword || !newPassword) { toast('提示','请填写当前密码和新密码','info'); return; }
    if (newPassword.length < 4) { toast('错误','新密码至少 4 位','error'); return; }
    try {
      const r = await fetch('/api/auth/change-password', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({old_password: oldPassword, new_password: newPassword})});
      const d = await r.json();
      if (r.ok && d.success) { toast('成功','密码已更新','success'); document.getElementById('oldPassword').value=''; document.getElementById('newPassword').value=''; } else { toast('错误', d.error || '修改失败','error'); }
    } catch (e) { toast('错误','网络异常','error'); }
  };

    /* ===== PERSONA LIBRARY ===== */
  window.showLibraryPicker = async function() {
    const modal = document.getElementById('libraryPickerModal');
    const list = document.getElementById('libraryPickerList');
    const personaLabel = document.getElementById('libraryPickerPersona');
    if (!modal || !list) return;
    if (personaLabel) personaLabel.textContent = S.currentPersona || '';
    modal.style.display = 'flex';
    list.innerHTML = '<p style="color:var(--text-muted)">加载中...</p>';
    try {
      const r = await fetch('/api/user/persona-library');
      const d = await r.json();
      if (d.access_denied) { list.innerHTML = '<p style="color:var(--text-muted)">你还没有访问性格库的权限，请联系管理员开通。</p>'; return; }
      const items = d.library || [];
      if (!items.length) { list.innerHTML = '<p style="color:var(--text-muted)">暂无可用的管理员模板</p>'; return; }
      list.innerHTML = items.map(item =>
        '<div style="background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:8px;cursor:pointer" onclick="applyLibraryItem(\''+escapeHtml(item.name)+'\')">'
        +'<div style="font-weight:600">'+escapeHtml(item.name)+(item.persona_name?' <span style="color:var(--text-muted);font-size:12px">('+escapeHtml(item.persona_name)+')</span>':'')+'</div>'
        +'<div style="font-size:13px;color:var(--text-muted);margin-top:4px;max-height:60px;overflow:hidden">'+escapeHtml(item.content||'')+'</div>'
        +'</div>'
      ).join('');
    } catch(e) { list.innerHTML = '<p style="color:var(--red)">加载失败</p>'; }
  };
  window.closeLibraryPicker = function() { document.getElementById('libraryPickerModal').style.display = 'none'; };
  window.applyLibraryItem = async function(libName) {
    if (!S.currentPersona) { toast('错误','请先选择角色','error'); return; }
    if (!confirm('将 "'+libName+'" 应用到 '+S.currentPersona+' ？这会覆盖当前的自定义性格。')) return;
    try {
      const r = await fetch('/api/user/persona/apply-library', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({library_name: libName, persona: S.currentPersona})});
      const d = await r.json();
      if (d.success) {
        toast('成功','已应用模板','success');
        closeLibraryPicker();
        loadUserPersonaDetail(S.currentPersona);
      } else { toast('错误', d.error||'应用失败','error'); }
    } catch(e) { toast('错误','应用失败','error'); }
  };

  window.createPersona = async function() {
    const name = prompt('请输入新角色名称:');
    if (!name || !name.trim()) return;
    try {
      const r = await fetch('/api/user/persona/create', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name: name.trim()}) });
      const d = await r.json();
      if (d.success) { toast('角色', '角色 ' + name + ' 已创建', 'success'); loadUserPersonaList(); }
      else toast('错误', d.error || '创建失败', 'error');
    } catch(e) { toast('错误','创建失败','error'); }
  };

  window.renamePersona = async function() {
    if (!S.currentPersona) { toast('提示','请先选择要重命名的角色','info'); return; }
    const newName = prompt('将 "' + S.currentPersona + '" 重命名为:');
    if (!newName || !newName.trim() || newName.trim() === S.currentPersona) return;
    try {
      const r = await fetch('/api/user/persona/rename', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({old_name: S.currentPersona, new_name: newName.trim()}) });
      const d = await r.json();
      if (d.success) {
        S.currentPersona = newName.trim();
        toast('角色', '已重命名为 ' + newName, 'success');
        loadUserPersonaList();
        updatePersonaHeader(newName.trim());
      } else toast('错误', d.error || '重命名失败', 'error');
    } catch(e) { toast('错误','重命名失败','error'); }
  };

  /* ===== AI 生成性格 ===== */
  var _selectedTraits = [];
  window.openAIGenerate = function() {
    var modal = document.getElementById('aiGenModal');
    if (modal) { modal.style.display = 'flex'; }
    _selectedTraits = [];
    document.querySelectorAll('.trait-tag').forEach(function(t) { t.classList.remove('selected'); });
    var r = document.getElementById('aiGenResult'); if (r) { r.style.display = 'none'; r.value = ''; }
    var h = document.getElementById('aiGenHint'); if (h) h.value = '';
    var b = document.getElementById('applyAIBtn'); if (b) b.style.display = 'none';
    var btn = document.getElementById('aiGenBtn'); if (btn) { btn.disabled = false; btn.textContent = '✨ 开始生成'; }
  };
  window.closeAIGenerate = function() {
    var modal = document.getElementById('aiGenModal');
    if (modal) modal.style.display = 'none';
  };
  window.toggleTrait = function(el) {
    var trait = el.dataset.trait;
    var idx = _selectedTraits.indexOf(trait);
    if (idx >= 0) { _selectedTraits.splice(idx, 1); el.classList.remove('selected'); }
    else { _selectedTraits.push(trait); el.classList.add('selected'); }
  };
  window.doGeneratePersonality = async function() {
    var hint = ((document.getElementById('aiGenHint')||{}).value||'').trim();
    if (!_selectedTraits.length && !hint) { toast('提示','请至少选择一个性格特征或填写描述','info'); return; }
    var btn = document.getElementById('aiGenBtn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 生成中...'; }
    try {
      var r = await fetch('/api/generate-personality', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({traits: _selectedTraits, custom_hint: hint})});
      var d = await r.json();
      if (d.content) {
        var resultEl = document.getElementById('aiGenResult');
        if (resultEl) { resultEl.value = d.content; resultEl.style.display = 'block'; }
        var applyBtn = document.getElementById('applyAIBtn');
        if (applyBtn) applyBtn.style.display = 'block';
        if (btn) btn.textContent = '✨ 重新生成';
      } else { toast('错误', d.error||'生成失败','error'); if (btn) btn.textContent = '✨ 开始生成'; }
    } catch(e) { toast('错误','生成失败','error'); if (btn) { btn.textContent = '✨ 开始生成'; btn.disabled = false; } }
  };
  window.applyAIPersonality = function() {
    var result = ((document.getElementById('aiGenResult')||{}).value||'').trim();
    if (!result) { toast('提示','没有可应用的内容','info'); return; }
    var contentArea = document.getElementById('userPersona');
    if (contentArea) { contentArea.value = result; closeAIGenerate(); toast('成功','已填入性格框','success'); return; }
    var textarea = document.getElementById('aiGenResult');
    if (textarea) { textarea.value = result; closeAIGenerate(); toast('成功','已填入性格框','success'); }
  };

})();

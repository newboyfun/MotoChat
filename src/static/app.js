(function() {
  const origFetch = window.fetch;
  window.fetch = function(input, init) {
    init = init || {};
    init.headers = Object.assign({}, init.headers || {});
    const token = localStorage.getItem('kc_token');
    if (token && !init.headers['Authorization'] && !init.headers['authorization']) {
      init.headers['Authorization'] = 'Bearer ' + token;
    }
    return origFetch(input, init);
  };
})();
/* MotoChat - 主应用逻辑 v3 (REST + SSE, no WebSocket) */
(function() {
  'use strict';
  /* dbg v9 */
  const S = {
    page: 'dashboard', cid: 'default',
    streaming: false, start: Date.now(), personas: [],
    currentBubble: null, fullReply: '', role: 'user'
  };
  const $ = (s, c) => (c||document).querySelector(s);
  const $$ = (s, c) => [...(c||document).querySelectorAll(s)];

  /* ===== INIT ===== */
  function _nav() { $$('.nav-btn[data-page]').forEach(btn => { btn.addEventListener('click', () => goToPage(btn.dataset.page)); }); }
  document.addEventListener('DOMContentLoaded', async () => {
    _nav();
    bindChat();
    bindAuth(); bindLogout();
    bindTheme();
    bindSidebar();
    startUptime();
    updateGreeting();

    let splashHidden = false;
    function hideSplash() {
      if (splashHidden) return;
      splashHidden = true;
      const sp = document.getElementById('splash');
      if (sp) sp.classList.add('hidden');
    }
    setTimeout(hideSplash, 800);

    try {
      const me = await apiAuthMe();
      hideSplash();
      if (!me || !me.username || me.role !== 'admin') {
        location.href='/login';
        return;
      }
      applyRole(me.role);
      S.cid = me.conversation_id || "default";
      const ly = document.getElementById('layout');
      if (ly) ly.style.display = 'flex';
      await loadAll();
      checkConnection();
      if (S.role === 'user') goToPage('chat');
    } catch(e) {
      console.error('init error:', e);
      hideSplash();
      location.href='/login';
    }
  });

  function showAuth() {
    const auth = document.getElementById('authView');
    const ly = document.getElementById('layout');
    if (auth) auth.style.display = 'flex';
    if (ly) ly.style.display = 'none';
  }

  async function apiAuthMe() {
    try {
      const token = localStorage.getItem('kc_token');
      if (!token) return null;
      const r = await fetch('/api/auth/me');
      if (!r.ok) return null;
      return await r.json();
    } catch(e) { return null; }
  }

  function bindLogout() {
    const btn = document.getElementById('logoutBtn');
    if (!btn) return;
    btn.style.display = '';
    btn.addEventListener('click', async () => {
      try { await fetch('/api/auth/logout', {method:'POST'}); } catch(e) {}
      localStorage.removeItem('kc_token');
      location.href='/login';
    });
  }
  function applyRole(role) {
    S.role = role || 'user';
    const adminPages = ['personas','users','library','commands','logs','settings'];
    $$('.nav-btn').forEach(btn => {
      if (adminPages.includes(btn.dataset.page)) {
        btn.style.display = S.role === 'admin' ? '' : 'none';
      }
    });
    $$('.admin-only').forEach(el => { el.style.display = S.role === 'admin' ? '' : 'none'; });
  }

  function bindAuth() {}
  /* ===== THEME ===== */
  function bindTheme() {
    const btn = document.getElementById('themeToggle');
    if (!btn) return;
    updateThemeIcon();
    btn.addEventListener('click', function() {
      const next = window.mcTheme ? window.mcTheme.toggle() : 'light';
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
  }
  function updateThemeIcon() {
    const isDark = document.documentElement.hasAttribute('data-theme');
    const sun = document.getElementById('themeIconSun');
    const moon = document.getElementById('themeIconMoon');
    if (sun) sun.style.display = isDark ? 'none' : '';
    if (moon) moon.style.display = isDark ? '' : 'none';
  }
  /* ===== CONNECTION CHECK ===== */
  async function checkConnection() {
    try {
      const r = await fetch('/api/stats', {signal: AbortSignal.timeout(3000)});
      if (r.ok) {
        updateStatus(true);
      } else {
        updateStatus(false);
      }
    } catch(e) {
      updateStatus(false);
    }
    setTimeout(checkConnection, 10000);
  }

  function updateStatus(connected) {
    const el = $('#wsStatus');
    if (!el) return;
    if (connected) {
      el.textContent = '● 已连接';
      el.style.color = 'var(--green)';
    } else {
      el.textContent = '● 未连接';
      el.style.color = 'var(--red)';
    } /* ===== NAV ===== */
  function bindNav() {
    $$('.nav-btn[data-page]').forEach(btn => {
      btn.addEventListener('click', () => goToPage(btn.dataset.page));
    });
  }
  window._initBindNav = bindNav;

  window.goToPage = function(page) {
    S.page = page;
    $$('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.page === page));
    $$('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + page));
    const titles = { dashboard:'控制台', chat:'对话', personas:'角色管理', users:'用户管理', library:'性格库', commands:'指令中心', logs:'系统日志', settings:'设置' };
    const adminPages = ['personas','commands','logs','settings','users','library'];
    if (adminPages.includes(page) && S.role !== 'admin') { toast('权限不足','需要管理员权限','error'); return; }
    $('#pageTitle').textContent = titles[page] || page;
    $('#sidebar').classList.remove('open');
    const overlay = document.getElementById('sidebarOverlay');
    if (overlay) overlay.classList.remove('open');
    if (page === 'chat') setTimeout(() => { const el = $('#msgInput'); if (el) el.focus(); scrollChat(); }, 100);
    if (page === 'logs') refreshLogs();
    if (page === 'personas') loadPersonas();
    if (page === 'users') loadAdminUsers();
    if (page === 'library') loadPersonaLibrary();
    if (page === 'settings') loadSettings();
    if (page === 'dashboard') { loadStats(); refreshTokenUsage(); loadAdminUsers(); }
  };
  window.goToChat = () => goToPage('chat');

  window.sendCommand = async function(cmd) {
    const container = $('#chatMessages');
    if (container) { goToPage("chat"); }
    removeWelcome(); addMsg("user", cmd);
    const status = addMsg('assistant', '执行中...');
    try {
      const r = await fetch('/api/command', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({command:cmd, conversation_id:S.cid})});
      const d = await r.json();
      if (status) status.innerHTML = renderEmotions(escapeHtml(d.reply || '执行完成'));
      refreshTokenUsage();
    } catch(e) { if (status) status.innerHTML = '<span style="color:var(--red)">命令执行失败: ' + escapeHtml(e.message) + '</span>'; }
    scrollChat();
  };

  /* ===== CHAT ===== */
  window.sendQuick = function(text) {
    goToPage('chat');
    setTimeout(function() {
      const input = $('#msgInput');
      if (input) { input.value = text; input.dispatchEvent(new Event('input', {bubbles: true})); }
      const btn = $('#sendBtn');
      if (btn && !btn.disabled) btn.click();
    }, 200);
  };

  function bindChat() {
    const input = $('#msgInput');
    const btn = $('#sendBtn');
    if (input) {
      input.addEventListener('input', autoResize);
      input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMsg(); } });
    }
    if (btn) btn.addEventListener('click', sendMsg);
    const attach = $('#attachBtn');
    const fileInput = $('#imageInput');
    if (attach && fileInput) {
      attach.addEventListener('click', () => fileInput.click());
      fileInput.addEventListener('change', handleImage);
    }
  }

  function autoResize() {
    const el = $('#msgInput');
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  }

  async function sendMsg() {
    const input = $('#msgInput');
    if (!input) return;
    const text = input.value.trim();
    if (!text || S.streaming) return;
    input.value = ''; autoResize();
    removeWelcome();
    addMsg('user', text);
    S.streaming = true;
    const btn = $('#sendBtn'); if (btn) btn.disabled = true;
    const bubble = addMsg('assistant', '');
    S.currentBubble = bubble;
    S.fullReply = '';
    try {
      const resp = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, conversation_id: S.cid })
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        let eventType = '';
        for (const line of lines) {
          if (line.startsWith('event: ')) { eventType = line.substring(7).trim(); continue; }
          if (line.startsWith('data: ')) {
            const rawData = line.slice(6);
            if (rawData === '[DONE]') continue;
            try {
              const parsed = JSON.parse(rawData);
              if (eventType === 'chunk' && parsed.text) {
                S.fullReply += parsed.text;
                if (S.currentBubble) S.currentBubble.innerHTML = renderEmotions(escapeHtml(S.fullReply));
                scrollChat();
              } else if (eventType === 'error') {
                if (S.currentBubble) S.currentBubble.innerHTML = '<span style="color:var(--red)">\u2717 ' + escapeHtml(parsed.error || '\u5904\u7406\u51fa\u9519') + '</span>';
              }
            } catch(e) {}
          }
        }
      }
    } catch(e) {
      if (S.currentBubble) S.currentBubble.innerHTML = '<span style="color:var(--red)">发送失败: ' + escapeHtml(e.message) + '</span>';
    }
    finishReply();
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
            const tts = document.createElement('button');
            tts.className = 'msg-tts';
            tts.textContent = '🔊';
            tts.title = '朗读';
            tts.onclick = () => playTTS(part);
            body.appendChild(tts);
            div.appendChild(avatar);
            div.appendChild(body);
            $('#chatMessages').appendChild(div);
          }
        } else {
          // 没有 [SPLIT] 标记，保持原样
          S.currentBubble.innerHTML = renderEmotions(escapeHtml(S.fullReply));
          const body = S.currentBubble.parentElement;
          if (body) {
            const time = document.createElement('div');
            time.className = 'msg-time';
            time.textContent = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});
            body.appendChild(time);
            const tts = document.createElement('button');
            tts.className = 'msg-tts';
            tts.textContent = '🔊';
            tts.title = '朗读';
            tts.onclick = () => playTTS(S.fullReply);
            body.appendChild(tts);
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

  function addMsg(role, content, createdAt) {
    const container = $('#chatMessages');
    if (!container) return null;

    // 处理 [SPLIT] 标记，拆分成多条消息
    const parts = content.split('[SPLIT]').map(p => p.trim()).filter(p => p.length > 0);

    // 如果没有内容，创建一个空的 bubble
    if (parts.length === 0) {
      parts.push('');
    }

    const bubbles = [];

    // 格式化时间：优先用数据库时间，否则取当前时间
    const timeStr = createdAt
      ? new Date(createdAt).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'})
      : new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});

    for (const part of parts) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      const avatar = document.createElement('div');
      avatar.className = 'msg-avatar';
      avatar.textContent = role === 'user' ? '你' : getAvatarChar();
      const body = document.createElement('div');
      body.className = 'msg-body';
      const bubble = document.createElement('div');
      bubble.className = 'msg-bubble';
      bubble.innerHTML = renderEmotions(escapeHtml(part));
      body.appendChild(bubble);
      if (role === 'user') {
        const time = document.createElement('div');
        time.className = 'msg-time';
        time.textContent = timeStr;
        body.appendChild(time);
      }
      div.appendChild(avatar);
      div.appendChild(body);
      container.appendChild(div);
      bubbles.push(bubble);
    }

    scrollChat();
    return bubbles.length === 1 ? bubbles[0] : bubbles;
  }

  function removeWelcome() {
    const w = $('#msgWelcome');
    if (w) w.remove();
  }

  function scrollChat() {
    const el = $('#chatMessages');
    if (el) requestAnimationFrame(() => el.scrollTop = el.scrollHeight);
  }

  function getAvatarChar() {
    const el = $('#chatAvatar');
    return (el && el.textContent) || 'M';
  }

  async function handleImage(e) {
    const file = e.target.files[0];
    if (!file) return;
    e.target.value = '';
    removeWelcome();
    addMsg('user', '[图片] ' + file.name);
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await fetch('/api/images/recognize', { method: 'POST', body: fd });
      const d = await r.json();
      addMsg('assistant', d.recognized || '识别失败');
    } catch(err) {
      addMsg('assistant', '图片识别出错: ' + err.message);
    }
  }

  async function playTTS(text) {
    if (!text) return;
    try {
      const r = await fetch('/api/tts', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text}) });
      const d = await r.json();
      if (d.audio_url) new Audio(d.audio_url).play();
    } catch(e) { console.error('TTS error', e); }
  }

  /* ===== LOAD ALL ===== */
  async function loadAll() {
    await Promise.allSettled([loadStats(), loadSettings(), loadPersonas(), refreshLogs(), loadHistory(), loadDashboardFeed(), loadAdminUsers(), loadPersonaLibrary()]);
  }

  async function loadHistory() {
    try {
      const r = await fetch('/api/history/' + encodeURIComponent(S.cid));
      const d = await r.json();
      const msgs = d.messages || [];
      if (!msgs.length) return;
      removeWelcome();
      const container = $('#chatMessages');
      if (!container) return;
      container.innerHTML = '';
      msgs.forEach(m => addMsg(m.role, m.content, m.created_at));
    } catch(e) { console.error('loadHistory error', e); }
  }

  async function loadStats() {
    try {
      const r = await fetch('/api/stats');
      const d = await r.json();
      const set = (id, v) => { const el = $('#'+id); if (el) el.textContent = v; };
      set('statMsg', d.total_messages ?? 0);
      set('statPersona', d.persona_count ?? 0);
      set('statKey', d.api_key_configured ? '已配置' : '未配置');
      if (d.active_persona) {
        set('miniName', d.active_persona);
        set('chatName', d.active_persona);
        set('welcomeName', d.active_persona);
        const first = (d.active_persona || 'M').charAt(0).toUpperCase();
        set('miniAvatar', first);
        set('chatAvatar', first);
        set('welcomeAvatar', first);
      }
    } catch(e) { console.error('loadStats error', e); }
  }

  async function loadDashboardFeed() {
    try {
      const r = await fetch('/api/logs?limit=8');
      const d = await r.json();
      const el = $('#dashFeed');
      if (!el) return;
      const logs = d.logs || [];
      if (logs.length === 0) { el.innerHTML = '<div class="loading-text">暂无动态</div>'; return; }
      el.innerHTML = logs.reverse().map(l =>
        '<div class="feed-item"><span class="feed-msg">' + escapeHtml(l.message) + '</span><span class="feed-time">' + l.time + '</span></div>'
      ).join('');
    } catch(e) { console.error('loadDashboardFeed error', e); }
  }

async function loadAdminUsers() {
      try {
        const r = await fetch('/api/admin/users');
        const d = await r.json();
        const users = d.users || [];

        // Update user management page list
        const userList = document.getElementById('adminUsersList');
        if (userList) {
          userList.innerHTML = users.map(u => {
            var first = (u.username || '?').charAt(0).toUpperCase();
            var overrideCount = Object.keys(u.persona_override||{}).length;
            var pendingCount = Object.keys(u.pending_persona_override||{}).length;
            var badges = '<span class="user-badge role-'+u.role+'">'+u.role+'</span>';
            if (overrideCount) badges += ' <span class="user-badge override">'+overrideCount+'个设定</span>';
            if (pendingCount) badges += ' <span class="user-badge pending">'+pendingCount+'待审批</span>';
            return '<div class="user-card" onclick="showUserDetail(\''+escapeHtml(u.username)+'\')">'
              +'<div class="user-card-avatar">'+first+'</div>'
              +'<div class="user-card-body">'
              +'<div class="user-card-name">'+escapeHtml(u.username)+'</div>'
              +'<div class="user-card-meta">角色: '+escapeHtml(u.persona||'-')+'</div>'
              +'</div>'
              +'<div class="user-card-badges">'+badges+'</div>'
              +'<div class="user-card-arrow">→</div>'
              +'</div>';
          }).join('');
        }

        // Update dashboard user overview container
        var dashboardContainer = document.getElementById('adminUsersContainer');
        if (dashboardContainer) {
          var count = users.length;
          if (count === 0) {
            dashboardContainer.innerHTML = '<div class="loading-text">暂无用户</div>';
          } else {
            var html = '<div style="font-size:13px;color:var(--text-muted);margin-bottom:8px">共 '+count+' 个用户</div>';
            html += '<div style="display:flex;flex-direction:column;gap:6px">';
            users.slice(0, 5).forEach(function(u) {
              var first = (u.username || '?').charAt(0).toUpperCase();
              html += '<div style="display:flex;align-items:center;gap:10px;padding:8px;border-radius:var(--r-xs);cursor:pointer;transition:background 0.15s" onclick="showUserDetail(\''+escapeHtml(u.username)+'\')" onmouseover="this.style.background=\'var(--bg-hover)\'" onmouseout="this.style.background=\'\'">';
              html += '<div style="width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,var(--accent),var(--purple));display:flex;align-items:center;justify-content:center;font-weight:700;font-size:12px;color:#fff;flex-shrink:0">'+first+'</div>';
              html += '<div style="flex:1;min-width:0"><div style="font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+escapeHtml(u.username)+'</div><div style="font-size:11px;color:var(--text-muted)">'+escapeHtml(u.persona||'-')+'</div></div>';
              html += '<span class="user-badge role-'+u.role+'" style="font-size:10px">'+u.role+'</span>';
              html += '</div>';
            });
            if (count > 5) html += '<div style="text-align:center;font-size:12px;color:var(--text-muted);padding:4px">还有 '+(count-5)+' 个用户...</div>';
            html += '</div>';
            dashboardContainer.innerHTML = html;
          }
        }

        // Update user count stat
        var statUsers = document.getElementById('statUsers');
        if (statUsers) statUsers.textContent = count;

      } catch(e) { console.error('loadAdminUsers error', e); }
    }
  window.editAdminUser = async function(username) {
    const editDiv = document.getElementById('adminUserEdit');
    const title = document.getElementById('adminEditTitle');
    if (editDiv) editDiv.style.display = '';
    if (title) title.textContent = '编辑: ' + username;
    editDiv.dataset.username = username;
    try {
      const r = await fetch('/api/admin/users/' + encodeURIComponent(username) + '/persona/MONO');
      const d = await r.json();
      const nameInput = document.getElementById('adminEditPersonaName');
      const contentArea = document.getElementById('adminEditPersonaContent');
      if (nameInput) nameInput.value = 'MONO';
      if (contentArea) contentArea.value = d.override_content || '';
    } catch(e) {}
  };
  window.hideAdminUserEdit = function() {
    const el = document.getElementById('adminUserEdit');
    if (el) el.style.display = 'none';
  };
  window.saveAdminUserPersona = async function() {
    const editDiv = document.getElementById('adminUserEdit');
    const username = editDiv ? editDiv.dataset.username : '';
    const personaName = (document.getElementById('adminEditPersonaName')||{}).value || 'MONO';
    const content = (document.getElementById('adminEditPersonaContent')||{}).value || '';
    if (!username) return;
    try {
      const r = await fetch('/api/admin/users/' + encodeURIComponent(username) + '/persona/' + encodeURIComponent(personaName), {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({content})});
      const d = await r.json();
      if (d.success) toast('成功','已保存覆盖性格','success');
      else toast('错误', d.error || '保存失败','error');
    } catch(e) { toast('错误','保存失败','error'); }
  };
window.resetAdminUserPw = async function(username) {
    const newPw = prompt('输入新密码:');
    if (!newPw) return;
    try {
      const r = await fetch('/api/admin/users/reset-password', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username, new_password: newPw})});
      const d = await r.json();
      if (d.success) toast('成功','密码已重置','success');
      else toast('错误', d.error || '重置失败','error');
    } catch(e) { toast('错误','重置失败','error'); }
  };
window.deleteAdminUser = async function(username) {
    if (!confirm('确定删除用户 ' + username + '？')) return;
    try {
      const r = await fetch('/api/admin/users/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username})});
      const d = await r.json();
      if (d.success) { toast('成功','已删除','success'); loadAdminUsers(); }
      else toast('错误', d.error || '删除失败','error');
    } catch(e) { toast('错误','删除失败','error'); }
  };
window.showCreateUser = function() {
    const el = document.getElementById('createUserForm');
    if (el) el.style.display = el.style.display === 'none' ? '' : 'none';
  };
  window.createAdminUser = async function() {
    const username = (document.getElementById('newUserName')||{}).value || '';
    const password = (document.getElementById('newUserPass')||{}).value || '';
    const persona = (document.getElementById('newUserPersona')||{}).value || 'MONO';
    if (!username || !password) { toast('错误','请填写完整','error'); return; }
    try {
      const r = await fetch('/api/admin/users/create', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username, password, persona})});
      const d = await r.json();
      if (d.success) { toast('成功','用户已创建','success'); loadAdminUsers(); }
      else toast('错误', d.error || '创建失败','error');
    } catch(e) { toast('错误','创建失败','error'); }
  };

async function loadSettings() {
    try {
      const r = await fetch('/api/config');
      const d = await r.json();
      const setVal = (id, v) => { const el = $('#'+id); if (el) el.value = v ?? ''; };
      setVal('cfgBaseUrl', d.base_url);
      setVal('cfgModel', d.model);
      setVal('cfgTemp', d.temperature);
      setVal('cfgMaxTokens', d.max_tokens);
      setVal('cfgContextRounds', d.max_context_rounds);
      setVal('cfgVisionKey', d.vision_api_key);
      setVal('cfgVisionBaseUrl', d.vision_base_url);
      setVal('cfgVisionModel', d.vision_model);
      setVal('cfgTtsKey', d.tts_api_key);
      setVal('cfgTtsBaseUrl', d.tts_base_url);
      setVal('cfgTtsModelId', d.tts_model_id);
      const maskedEl = $('#apiKeyMasked');
      if (maskedEl) maskedEl.textContent = d.api_key_masked || '未配置';
      const keyInputRow = $('#keyInputRow');
      if (keyInputRow) keyInputRow.style.display = 'none';
      const apiKeyEdit = $('#apiKeyEdit');
      if (apiKeyEdit) apiKeyEdit.style.display = 'none';
    } catch(e) { console.error('loadSettings error', e); }
  }

  /* ===== PERSONAS ===== */
  async function loadPersonas() {
    try {
      const r = await fetch('/api/personas');
      const d = await r.json();
      S.personas = d.personas || [];
      const grid = $('#personasGrid');
      if (!grid) return;
      if (S.personas.length === 0) {
        grid.innerHTML = '<div class="loading-text">暂无角色，点击上方按钮创建</div>';
        return;
      }
      grid.innerHTML = S.personas.map(p =>
        '<div class="persona-card' + (p.active ? ' active' : '') + '">' +
        '<div class="persona-card-avatar">' + p.name.charAt(0).toUpperCase() + '</div>' +
        '<div class="persona-card-name">' + escapeHtml(p.name) + '</div>' +
        (p.active ? '<span class="badge">当前</span>' : '') +
        '<div class="persona-card-actions">' +
        (p.active ? '' : '<button class="btn-icon" onclick="switchPersona(\'' + p.name + '\')" title="切换"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg></button>') +
        '<button class="btn-icon" onclick="editPersona(\'' + p.name + '\')" title="编辑"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>' +
        (p.active ? '' : '<button class="btn-icon danger" onclick="deletePersona(\'' + p.name + '\')" title="删除"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg></button>') +
        '</div></div>'
      ).join('');
    } catch(e) { console.error('loadPersonas error', e); }
  }

  window.switchPersona = async function(name) {
    try {
      const r = await fetch('/api/persona/switch', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name}) });
      const d = await r.json();
      if (d.success) { toast('角色', '已切换到 ' + name, 'success'); loadPersonas(); loadStats(); }
      else toast('错误', d.error || '切换失败', 'error');
    } catch(e) { toast('错误', '切换失败', 'error'); }
  };

  let _editPersonaName = '';
  window.editPersona = async function(name) {
    _editPersonaName = name;
    const modal = $('#personaModal');
    const title = $('#modalTitle');
    const nameInput = $('#editPersonaName');
    const contentInput = $('#editPersonaContent');
    if (title) title.textContent = '编辑角色: ' + name + ' (管理员独立设定)';
    if (nameInput) nameInput.value = name;
    if (contentInput) contentInput.value = '加载中…';
    if (modal) modal.style.display = 'flex';
    try {
      const r = await fetch('/api/personas/' + encodeURIComponent(name) + '/prompt');
      const d = await r.json();
      const sr = await fetch('/api/config');
      const sd = await sr.json();
      const adminOver = (sd.admin_persona_override || {})[name] || '';
      if (contentInput) contentInput.value = adminOver || d.content || '';
    } catch(e) { if (contentInput) contentInput.value = '加载失败'; }
  };

  window.hidePersonaModal = function() {
    const modal = $('#personaModal');
    if (modal) modal.style.display = 'none';
  };

  window.savePersona = async function() {
    const content = (($('#editPersonaContent')||{}).value||'');
    try {
      const r = await fetch('/api/admin/persona-override', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({persona: _editPersonaName, content})
      });
      const d = await r.json();
      if (d.success) { toast('角色', '管理员性格设定已保存', 'success'); hidePersonaModal(); loadPersonas(); }
      else toast('错误', d.error || '保存失败', 'error');
    } catch(e) { toast('错误', '保存失败', 'error'); }
  };

  window.createPersona = async function() {
    const name = prompt('请输入新角色名称:');
    if (!name || !name.trim()) return;
    try {
      const r = await fetch('/api/personas/create', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name: name.trim()}) });
      const d = await r.json();
      if (d.success) { toast('角色', '角色 ' + name + ' 已创建', 'success'); loadPersonas(); loadStats(); }
      else toast('错误', d.error || '创建失败', 'error');
    } catch(e) { toast('错误', '创建失败', 'error'); }
  };

  window.deletePersona = async function(name) {
    if (!confirm('确定要删除角色 ' + name + ' 吗？')) return;
    try {
      const r = await fetch('/api/personas/' + encodeURIComponent(name) + '/delete', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})
      });
      const d = await r.json();
      if (d.success) { toast('角色', name + ' 已删除', 'success'); loadPersonas(); loadStats(); }
      else toast('错误', d.error || '删除失败', 'error');
    } catch(e) { toast('错误', '删除失败', 'error'); }
  };

  /* ===== LOGS ===== */
  window.refreshLogs = async function() {
    try {
      const level = (($('#logLevelFilter')||{}).value||'all');
      const r = await fetch('/api/logs?level=' + level + '&limit=200');
      const d = await r.json();
      const container = $('#logsContainer');
      if (!container) return;
      const logs = d.logs || [];
      if (logs.length === 0) { container.innerHTML = '<div class="loading-text">暂无日志</div>'; return; }
      container.innerHTML = logs.reverse().map(l => {
        const cls = 'log-line log-' + l.level;
        return '<div class="' + cls + '"><span class="log-time">' + l.time + '</span><span class="log-level">' + l.level.toUpperCase() + '</span><span class="log-msg">' + escapeHtml(l.message) + '</span></div>';
      }).join('');
    } catch(e) { console.error('refreshLogs error', e); }
  };
  window.clearLogs = function() {
    const container = $('#logsContainer');
    if (container) container.innerHTML = '<div class="loading-text">暂无日志</div>';
    toast('日志', '前端日志视图已清除', 'info');
  };

  /* ===== CONFIG ===== */
  window.saveConfig = async function() {
    const status = $('#cfgStatus');
    try {
      const data = {};
      const getVal = id => (($('#'+id)||{}).value||'').trim();
      const apiKeyVal = getVal('cfgApiKey');
      if (apiKeyVal) data['llm.api_key'] = apiKeyVal;
      data['llm.base_url'] = getVal('cfgBaseUrl');
      data['llm.model'] = getVal('cfgModel');
      const temp = parseFloat(getVal('cfgTemp'));
      const maxTokens = parseInt(getVal('cfgMaxTokens'), 10);
      const ctxRounds = parseInt(getVal('cfgContextRounds'), 10);
      if (!isNaN(temp)) data['llm.temperature'] = temp;
      if (!isNaN(maxTokens)) data['llm.max_tokens'] = maxTokens;
      if (!isNaN(ctxRounds)) data['llm.max_context_rounds'] = ctxRounds;
      const r = await fetch('/api/config/save', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data) });
      const d = await r.json();
      if (d.success) {
        if (status) { status.textContent = '已保存'; status.className = 'cfg-status success'; }
        toast('配置', 'API 配置已保存', 'success');
        loadStats(); loadSettings();
      } else {
        if (status) { status.textContent = '保存失败'; status.className = 'cfg-status error'; }
      }
    } catch(e) {
      if (status) { status.textContent = '保存失败: ' + e.message; status.className = 'cfg-status error'; }
    }
    setTimeout(() => { if (status) status.textContent = ''; }, 3000);
  };

  window.toggleKeyVis = function() { const el = $('#cfgApiKey'); if (el) el.type = el.type === 'password' ? 'text' : 'password'; };

  window.revealApiKey = function() {
    const edit = $('#apiKeyEdit'); if (edit) edit.style.display = 'block';
    const status = $('#keyVerifyStatus'); if (status) { status.textContent = ''; status.className = 'cfg-status'; }
    setTimeout(() => { const pv = $('#keyVerifyPw'); if (pv) pv.focus(); }, 100);
  };
  window.hideKeyEdit = function() {
    const edit = $('#apiKeyEdit'); if (edit) edit.style.display = 'none';
    const el = $('#cfgApiKey'); if (el) el.value = '';
  };
  window.verifyKeyAccess = async function() {
    const pw = (($('#keyVerifyPw')||{}).value||'');
    const status = $('#keyVerifyStatus');
    if (!pw) { if (status) { status.textContent = '请输入密码'; status.className = 'cfg-status error'; } return; }
    try {
      const r = await fetch('/api/auth/verify-password', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({password:pw}) });
      const d = await r.json();
      if (d.authenticated) {
        if (status) { status.textContent = '验证成功'; status.className = 'cfg-status success'; }
        const kr = await fetch('/api/auth/reveal-key', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({password:pw, key_type:'llm'}) });
        const kd = await kr.json();
        const el = $('#cfgApiKey'); if (el) el.value = kd.key || '';
        const inputRow = $('#keyInputRow'); if (inputRow) inputRow.style.display = 'block';
      } else {
        if (status) { status.textContent = d.error || '密码错误'; status.className = 'cfg-status error'; }
      }
    } catch(e) { if (status) { status.textContent = '验证失败'; status.className = 'cfg-status error'; } }
  };

  window.saveVisionConfig = async function() {
    const getVal = id => (($('#'+id)||{}).value||'').trim();
    const data = { 'vision.api_key': getVal('cfgVisionKey'), 'vision.base_url': getVal('cfgVisionBaseUrl'), 'vision.model': getVal('cfgVisionModel') };
    const status = $('#cfgVisionStatus');
    try {
      const r = await fetch('/api/config/save', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data) });
      const d = await r.json();
      if (d.success) { toast('成功','视觉配置已保存','success'); if (status) { status.textContent = '已保存'; status.className = 'cfg-status success'; } }
      else { toast('错误', d.error||'保存失败','error'); if (status) { status.textContent = '保存失败'; status.className = 'cfg-status error'; } }
    } catch(e) { toast('错误','保存失败','error'); if (status) { status.textContent = '保存失败'; status.className = 'cfg-status error'; } }
  };

  window.saveTtsConfig = async function() {
    const getVal = id => (($('#'+id)||{}).value||'').trim();
    const data = { 'tts.api_key': getVal('cfgTtsKey'), 'tts.base_url': getVal('cfgTtsBaseUrl'), 'tts.model_id': getVal('cfgTtsModelId') };
    const status = $('#cfgTtsStatus');
    try {
      const r = await fetch('/api/config/save', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data) });
      const d = await r.json();
      if (d.success) { toast('成功','语音配置已保存','success'); if (status) { status.textContent = '已保存'; status.className = 'cfg-status success'; } }
      else { toast('错误', d.error||'保存失败','error'); if (status) { status.textContent = '保存失败'; status.className = 'cfg-status error'; } }
    } catch(e) { toast('错误','保存失败','error'); if (status) { status.textContent = '保存失败'; status.className = 'cfg-status error'; } }
  };

  window.changeAdminPw = async function() {
    const oldPw = (($('#oldPw')||{}).value||'');
    const newPw = (($('#newPw')||{}).value||'');
    if (!oldPw || !newPw) { toast('错误','请填写完整','error'); return; }
    if (newPw.length < 4) { toast('错误','新密码至少4位','error'); return; }
    try {
      const r = await fetch('/api/auth/change-password', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({old_password:oldPw, new_password:newPw}) });
      const d = await r.json();
      if (d.success) { toast('成功','管理密码已修改','success'); const e1=$('#oldPw'); if(e1) e1.value=''; const e2=$('#newPw'); if(e2) e2.value=''; }
      else toast('错误', d.error||'修改失败', 'error');
    } catch(e) { toast('错误','修改失败','error'); }
  };

  /* ===== TOKEN ===== */
  async function refreshTokenUsage() {
    try {
      const r = await fetch('/api/token-usage');
      const d = await r.json();
      const u = d.usage || {};
      const set = (id, v) => { const el = $('#'+id); if (el) el.textContent = v; };
      set('dashTokenPrompt', u.prompt_tokens || 0);
      set('dashTokenCompletion', u.completion_tokens || 0);
      set('dashTokenTotal', u.total_tokens || 0);
      set('chatTokenDisplay', u.total_tokens || '-');
    } catch(e) {}
  }

  /* ===== UPTIME ===== */
  function startUptime() {
    setInterval(() => {
      const diff = Math.floor((Date.now() - S.start) / 1000);
      const h = String(Math.floor(diff / 3600)).padStart(2, '0');
      const m = String(Math.floor((diff % 3600) / 60)).padStart(2, '0');
      const s = String(diff % 60).padStart(2, '0');
      const el = $('#statUptime');
      if (el) el.textContent = h + ':' + m + ':' + s;
    }, 1000);
  }

  function updateGreeting() {
    const h = new Date().getHours();
    let g = '晚上好！';
    if (h < 6) g = '凌晨好！';
    else if (h < 12) g = '上午好！';
    else if (h < 14) g = '中午好！';
    else if (h < 18) g = '下午好！';
    const el = $('#greeting');
    if (el) el.textContent = g;
  }
  }


  /* ===== AI GENERATE PERSONALITY ===== */
  var _selectedTraits = [];
  window.openAIGenerate = function() {
    var modal = document.getElementById('aiGenModal');
    if (modal) { modal.style.display = 'flex'; }
    _selectedTraits = [];
    document.querySelectorAll('.trait-tag').forEach(function(t) { t.classList.remove('selected'); });
    var r = document.getElementById('aiGenResult'); if (r) { r.style.display = 'none'; r.value = ''; }
    var h = document.getElementById('aiGenHint'); if (h) h.value = '';
    var b = document.getElementById('applyAIBtn'); if (b) b.style.display = 'none';
    var btn = document.getElementById('aiGenBtn'); if (btn) { btn.disabled = false; btn.textContent = '\u2728 \u5f00\u59cb\u751f\u6210'; }
  };
  window.openAdminAIGen = function() { openAIGenerate(); };
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
    if (!_selectedTraits.length && !hint) { toast('\u9519\u8bef','\u8bf7\u81f3\u5c11\u9009\u62e9\u4e00\u4e2a\u7279\u5f81','error'); return; }
    var btn = document.getElementById('aiGenBtn');
    if (btn) { btn.disabled = true; btn.textContent = '\u751f\u6210\u4e2d...'; }
    try {
      var r = await fetch('/api/generate-personality', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({traits: _selectedTraits, custom_hint: hint})});
      var d = await r.json();
      if (d.success && d.personality) {
        var resultEl = document.getElementById('aiGenResult');
        if (resultEl) { resultEl.style.display = ''; resultEl.value = d.personality; }
        var applyBtn = document.getElementById('applyAIBtn');
        if (applyBtn) applyBtn.style.display = '';
      } else {
        toast('\u9519\u8bef', d.error || '\u751f\u6210\u5931\u8d25','error');
      }
    } catch(e) { toast('\u9519\u8bef','\u751f\u6210\u5931\u8d25','error'); }
    if (btn) { btn.disabled = false; btn.textContent = '\u2728 \u5f00\u59cb\u751f\u6210'; }
  };
  window.applyAIPersonality = function() {
    var result = ((document.getElementById('aiGenResult')||{}).value||'').trim();
    if (!result) return;
    var contentArea = document.getElementById('editPersonaContent');
    if (!contentArea) contentArea = document.getElementById('adminEditPersonaContent');
    if (contentArea) { contentArea.value = result; closeAIGenerate(); toast('\u6210\u529f','\u5df2\u586b\u5165\u6027\u683c\u6846','success'); return; }
    var textarea = document.getElementById('aiGenResult');
    if (textarea) { textarea.value = result; closeAIGenerate(); toast('\u6210\u529f','\u5df2\u586b\u5165\u6027\u683c\u6846','success'); }
  };

  function toast(title, msg, type) {
    const container = document.getElementById('toasts');
    if (!container) return;
    const div = document.createElement('div');
    div.className = 'toast toast-' + (type||'info');
    div.innerHTML = '<div class="toast-title">' + escapeHtml(title) + '</div><div class="toast-msg">' + escapeHtml(msg) + '</div>';
    container.appendChild(div);
    setTimeout(() => { div.classList.add('fade-out'); setTimeout(() => div.remove(), 300); }, 3000);
  }
  window.toast = toast;

  function escapeHtml(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function renderEmotions(text) {
    if (!text) return '';
    return text.replace(/\n/g, '<br>');
  }

  /* ===== ADMIN: USER MANAGEMENT ===== */
  window.showCreateUser = function() {
    const modal = document.getElementById('createUserModal');
    if (modal) modal.style.display = 'flex';
    const sel = document.getElementById('newUserPersona');
    if (sel) sel.innerHTML = S.personas.map(p => '<option value="'+p.name+'">'+p.name+'</option>').join('');
  };
  window.closeCreateUser = function() { document.getElementById('createUserModal').style.display = 'none'; };
  window.doCreateUser = async function() {
    const username = (document.getElementById('newUsername')||{}).value||'';
    const password = (document.getElementById('newPassword')||{}).value||'';
    const persona = (document.getElementById('newUserPersona')||{}).value||'MONO';
    if (!username || !password) { toast('错误','请填写用户名和密码','error'); return; }
    try {
      const r = await fetch('/api/admin/users/create', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username, password, persona})});
      const d = await r.json();
      if (d.success) { toast('成功','用户已创建','success'); closeCreateUser(); loadAdminUsers(); }
      else { toast('错误', d.error || '创建失败','error'); }
    } catch(e) { toast('错误','创建失败','error'); }
  };
  window.closeUserDetail = function() { document.getElementById('userDetailModal').style.display = 'none'; };
  window.showUserDetail = async function(username) {
    const modal = document.getElementById('userDetailModal');
    const title = document.getElementById('userDetailTitle');
    const content = document.getElementById('userDetailContent');
    if (!modal || !content) return;
    title.textContent = '用户详情: ' + username;
    modal.style.display = 'flex';
    content.innerHTML = '<p style="color:var(--text-muted);padding:20px;text-align:center">加载中...</p>';
    try {
      const r = await fetch('/api/admin/users');
      const d = await r.json();
      const user = (d.users||[]).find(u => u.username === username);
      if (!user) { content.innerHTML = '<p>用户不存在</p>'; return; }
      var first = (username||'?').charAt(0).toUpperCase();
      var overrideCount = Object.keys(user.persona_override||{}).length;
      var pendingCount = Object.keys(user.pending_persona_override||{}).length;

      var html = '';

      // User info card
      html += '<div style="display:flex;align-items:center;gap:14px;margin-bottom:20px;padding:16px;background:var(--accent-soft);border:1px solid var(--border);border-radius:var(--r)">';
      html += '<div style="width:48px;height:48px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--purple));display:flex;align-items:center;justify-content:center;font-weight:700;font-size:20px;color:#fff;flex-shrink:0">'+first+'</div>';
      html += '<div style="flex:1"><div style="font-weight:700;font-size:15px">'+escapeHtml(username)+'</div>';
      html += '<div style="font-size:12px;color:var(--text-secondary);margin-top:2px">角色: '+escapeHtml(user.persona||'-')+' · 类型: <span class="user-badge role-'+user.role+'">'+user.role+'</span>'+(overrideCount?' · <span class="user-badge override">'+overrideCount+'个性设定</span>':'')+(pendingCount?' · <span class="user-badge pending">'+pendingCount+'待审批</span>':'')+'</div></div>';
      html += '<div style="display:flex;gap:6px;flex-shrink:0"><button class="btn" onclick="viewUserChatHistory(\''+escapeHtml(username)+'\')" style="font-size:12px;padding:5px 12px">查看聊天记录</button></div>';
      html += '</div>';

      // Tab buttons
      html += '<div style="display:flex;gap:4px;margin-bottom:16px;border-bottom:1px solid var(--border);padding-bottom:4px">';
      html += '<button class="user-detail-tab active" data-tab="persona" onclick="switchUserDetailTab(this,\'persona\')" style="font-size:13px;padding:8px 16px;border:none;background:none;cursor:pointer;border-radius:var(--r-xs) var(--r-xs) 0 0;font-family:var(--font);font-weight:600;color:var(--accent);border-bottom:2px solid var(--accent)">性格设定</button>';
      html += '<button class="user-detail-tab" data-tab="account" onclick="switchUserDetailTab(this,\'account\')" style="font-size:13px;padding:8px 16px;border:none;background:none;cursor:pointer;border-radius:var(--r-xs) var(--r-xs) 0 0;font-family:var(--font);font-weight:500;color:var(--text-secondary);border-bottom:2px solid transparent">账号安全</button>';
      html += '</div>';

      // Tab: Persona settings
      html += '<div id="userTabPersona">';
      html += '<div style="margin-bottom:16px"><div style="font-weight:600;margin-bottom:8px">全局性格</div>';
      html += '<textarea id="adminEditUserPrompt" class="form-input" rows="3" placeholder="全局性格设定">'+escapeHtml(user.persona_prompt||'')+'</textarea></div>';
      html += '<div style="margin-bottom:16px"><div style="font-weight:600;margin-bottom:8px">各角色独立性格</div>';
      var overrides = user.persona_override || {};
      S.personas.forEach(function(p) {
        var val = overrides[p.name] || '';
        html += '<div style="margin-bottom:8px"><div style="font-size:13px;font-weight:600">'+p.name+(val?' <span style="color:var(--green)">已设置</span>':' <span style="color:var(--text-muted)">未设置</span>')+'</div>';
        html += '<textarea class="form-input admin-override-textarea" data-persona="'+p.name+'" rows="2">'+escapeHtml(val)+'</textarea></div>';
      });
      html += '</div>';
      var pendings = user.pending_persona_override || {};
      if (Object.keys(pendings).length > 0) {
        html += '<div style="margin-bottom:16px;padding:12px;background:rgba(99,102,241,0.08);border:1px solid var(--accent);border-radius:10px"><div style="font-weight:600;margin-bottom:8px;color:var(--accent)">待用户审批的设定</div>';
        for (var pn in pendings) {
          html += '<div style="margin-bottom:6px;font-size:13px"><strong>' + escapeHtml(pn) + ':</strong> <span style="color:var(--text-muted)">' + escapeHtml((pendings[pn]||'').substring(0,100)) + '</span></div>';
        }
        html += '<div style="font-size:12px;color:var(--text-muted);margin-top:6px">用户需要在通知页面同意后才会生效</div></div>';
      }
      html += '<div style="margin-bottom:16px"><div style="font-weight:600;margin-bottom:8px">允许使用的角色</div>';
      html += '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px">';
      var allowed = user.allowed_personas;
      var isAllAllowed = allowed === null || allowed === undefined;
      S.personas.forEach(function(p) {
        var checked = isAllAllowed || (allowed && allowed.includes(p.name));
        html += '<label style="display:flex;align-items:center;gap:4px;cursor:pointer;border:1px solid var(--border);border-radius:8px;padding:4px 10px;font-size:13px">';
        html += '<input type="checkbox" class="admin-allowed-persona" data-name="'+p.name+'" '+(checked?'checked':'')+'> '+escapeHtml(p.name)+'</label>';
      });
      html += '</div>';
      html += '<label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:13px;color:var(--text-muted)">';
      html += '<input type="checkbox" id="adminAllowAll" '+(isAllAllowed?'checked':'')+' onchange="toggleAllowAll(this)"> 全部允许</label></div>';
      html += '<div style="margin-bottom:16px"><label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:600">';
      html += '<input type="checkbox" id="adminEditLibraryAccess" '+(user.library_access?'checked':'')+'> 允许访问性格库</label>';
      html += '<div style="font-size:12px;color:var(--text-muted);margin-top:4px;margin-left:24px">开启后该用户可以使用管理员创建的公开性格模板</div></div>';
      html += '</div>';

      // Tab: Account security
      html += '<div id="userTabAccount" style="display:none">';
      html += '<div style="margin-bottom:20px;padding:14px;background:var(--amber-soft);border:1px solid var(--amber);border-radius:var(--r-sm)"><div style="font-weight:600;color:var(--amber);margin-bottom:4px">重置密码</div><div style="font-size:12px;color:var(--text-secondary)">修改后用户需使用新密码登录</div><div style="display:flex;gap:8px;margin-top:10px"><input id="adminResetPwInput" class="form-input" type="text" placeholder="输入新密码" style="flex:1"><button class="btn btn-primary" onclick="doResetUserPw(\''+escapeHtml(username)+'\')">重置密码</button></div></div>';
      html += '<div style="margin-bottom:16px"><div style="font-weight:600;margin-bottom:6px">会话ID</div><div style="font-size:13px;font-family:var(--font-mono);background:var(--bg-input);padding:8px 12px;border-radius:var(--r-xs)">'+escapeHtml(user.conversation_id||'default')+'</div></div>';
      html += '<div style="margin-bottom:16px"><div style="font-weight:600;margin-bottom:6px">允许的角色</div><div style="font-size:13px;color:var(--text-secondary)">'+escapeHtml(isAllAllowed?'全部':(allowed||[]).join(', ')||'无')+'</div></div>';
      html += '</div>';

      // Save button
      html += '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">';
      html += '<button class="btn btn-primary" onclick="saveUserDetail(\''+escapeHtml(username)+'\')">保存修改</button></div>';
      content.innerHTML = html;
    } catch(e) { content.innerHTML = '<p style="color:var(--red)">加载失败: '+escapeHtml(e.message)+'</p>'; }
  };

  window.switchUserDetailTab = function(btn, tab) {
    document.querySelectorAll('.user-detail-tab').forEach(function(b) {
      b.style.color = 'var(--text-secondary)'; b.style.fontWeight = '500'; b.style.borderBottom = '2px solid transparent';
    });
    btn.style.color = 'var(--accent)'; btn.style.fontWeight = '600'; btn.style.borderBottom = '2px solid var(--accent)';
    document.getElementById('userTabPersona').style.display = tab === 'persona' ? '' : 'none';
    document.getElementById('userTabAccount').style.display = tab === 'account' ? '' : 'none';
  };

  window.viewUserChatHistory = async function(username) {
    var content = document.getElementById('userDetailContent');
    if (!content) return;
    content.innerHTML = '<p style="color:var(--text-muted);padding:20px;text-align:center">加载聊天记录...</p>';
    try {
      var r = await fetch('/api/admin/users/'+encodeURIComponent(username)+'/messages');
      var d = await r.json();
      var msgs = d.messages || [];
      var html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px"><strong>聊天记录 ('+msgs.length+'条)</strong><button class="btn" onclick="showUserDetail(\''+escapeHtml(username)+'\')" style="font-size:12px;padding:5px 12px">返回详情</button></div>';
      if (msgs.length === 0) {
        html += '<p style="color:var(--text-muted);text-align:center;padding:30px">暂无聊天记录</p>';
      } else {
        html += '<div style="max-height:55vh;overflow-y:auto;display:flex;flex-direction:column;gap:8px">';
        msgs.forEach(function(m) {
          var time = m.created_at ? new Date(m.created_at).toLocaleString('zh-CN', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
          html += '<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--r-sm);padding:10px 14px">';
          html += '<div style="display:flex;justify-content:space-between;margin-bottom:4px"><span class="user-badge '+(m.role==='user'?'role-user':'role-admin')+'" style="font-size:11px">'+m.role+'</span><span style="font-size:11px;color:var(--text-muted)">'+time+'</span></div>';
          html += '<div style="font-size:13px;line-height:1.6;white-space:pre-wrap;word-break:break-word">'+escapeHtml(m.content||'')+'</div>';
          html += '</div>';
        });
        html += '</div>';
      }
      content.innerHTML = html;
    } catch(e) { content.innerHTML = '<p style="color:var(--red)">加载失败: '+escapeHtml(e.message)+' <button class="btn" onclick="showUserDetail(\''+escapeHtml(username)+'\')" style="font-size:12px">返回</button></p>'; }
  };

  window.doResetUserPw = async function(username) {
    var input = document.getElementById('adminResetPwInput');
    var newPw = input ? input.value.trim() : '';
    if (!newPw) { toast('错误','请输入新密码','error'); return; }
    try {
      var r = await fetch('/api/admin/users/reset-password', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username: username, new_password: newPw})});
      var d = await r.json();
      if (d.success) { toast('成功','密码已重置为: '+newPw,'success'); if (input) input.value = ''; }
      else toast('错误', d.error || '重置失败','error');
    } catch(e) { toast('错误','重置失败','error'); }
  };

  window.toggleAllowAll = function(cb) {
    document.querySelectorAll('.admin-allowed-persona').forEach(function(el) { el.checked = cb.checked; });
  };

  window.saveUserDetail = async function(username) {
    const promptEl = document.getElementById('adminEditUserPrompt');
    const persona_prompt = promptEl ? promptEl.value : '';
    const persona_override = {};
    document.querySelectorAll('.admin-override-textarea').forEach(el => { const v = el.value.trim(); if (v) persona_override[el.dataset.persona] = v; });
    const libraryAccessEl = document.getElementById('adminEditLibraryAccess');
    const library_access = libraryAccessEl ? libraryAccessEl.checked : false;
    const allowAllEl = document.getElementById('adminAllowAll');
    let allowed_personas = null;
    if (allowAllEl && !allowAllEl.checked) {
      allowed_personas = [];
      document.querySelectorAll('.admin-allowed-persona:checked').forEach(el => { allowed_personas.push(el.dataset.name); });
    }
    try {
      const r = await fetch('/api/admin/users/update', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username, persona_prompt, persona_override, library_access, allowed_personas})});
      const d = await r.json();
      if (d.success) { toast('成功','已保存','success'); closeUserDetail(); loadAdminUsers(); }
      else { toast('错误', d.error||'保存失败','error'); }
    } catch(e) { toast('错误','保存失败','error'); }
  };

  /* ===== ADMIN: PERSONA LIBRARY ===== */
  window.loadPersonaLibrary = async function() {
    try {
      const r = await fetch('/api/admin/persona-library');
      const d = await r.json();
      const el = document.getElementById('personaLibraryList');
      if (!el) return;
      const items = d.library || [];
      if (!items.length) { el.innerHTML = '<p style="color:var(--text-muted)">暂无性格模板</p>'; return; }
      el.innerHTML = items.map(item =>
        '<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:flex-start">'
        +'<div style="flex:1"><div style="font-weight:600">'+escapeHtml(item.name)+' '+(item.public?'<span style="color:var(--green);font-size:12px">公开</span>':'<span style="color:var(--text-muted);font-size:12px">私有</span>')+'</div>'
        +(item.persona_name?'<div style="font-size:12px;color:var(--text-muted)">角色: '+escapeHtml(item.persona_name)+'</div>':'')
        +'<div style="font-size:13px;color:var(--text-muted);margin-top:4px;max-height:40px;overflow:hidden">'+escapeHtml((item.content||'').substring(0,120))+'</div></div>'
        +'<div style="display:flex;gap:6px;margin-left:12px"><button class="btn" onclick="editLibraryItem(\''+escapeHtml(item.name)+'\')">编辑</button>'
        +'<button class="btn" style="color:var(--red)" onclick="deleteLibraryItem(\''+escapeHtml(item.name)+'\')">删除</button></div></div>'
      ).join('');
    } catch(e) { console.error('loadPersonaLibrary', e); }
  };
  window.showAddLibraryItem = function() {
    document.getElementById('libraryEditTitle').textContent = '新建性格模板';
    document.getElementById('libEditName').value = ''; document.getElementById('libEditContent').value = '';
    document.getElementById('libEditPublic').checked = true;
    const sel = document.getElementById('libEditPersona');
    sel.innerHTML = '<option value="">不关联</option>' + S.personas.map(p=>'<option value="'+p.name+'">'+p.name+'</option>').join('');
    document.getElementById('libraryEditModal').style.display = 'flex';
  };
  window.editLibraryItem = async function(name) {
    try {
      const r = await fetch('/api/admin/persona-library'); const d = await r.json();
      const item = (d.library||[]).find(x => x.name === name);
      if (!item) { toast('错误','不存在','error'); return; }
      document.getElementById('libraryEditTitle').textContent = '编辑: '+name;
      document.getElementById('libEditName').value = item.name;
      document.getElementById('libEditContent').value = item.content||'';
      document.getElementById('libEditPublic').checked = item.public !== false;
      const sel = document.getElementById('libEditPersona');
      sel.innerHTML = '<option value="">不关联</option>' + S.personas.map(p=>'<option value="'+p.name+'">'+p.name+'</option>').join('');
      sel.value = item.persona_name||'';
      document.getElementById('libraryEditModal').style.display = 'flex';
    } catch(e) { toast('错误','加载失败','error'); }
  };
  window.closeLibraryEdit = function() { document.getElementById('libraryEditModal').style.display = 'none'; };
  window.doSaveLibraryItem = async function() {
    const name = (document.getElementById('libEditName')||{}).value||'';
    const content = (document.getElementById('libEditContent')||{}).value||'';
    const persona_name = (document.getElementById('libEditPersona')||{}).value||'';
    const isPublic = (document.getElementById('libEditPublic')||{}).checked;
    if (!name) { toast('错误','请填写名称','error'); return; }
    try {
      const r = await fetch('/api/admin/persona-library/save', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name, content, persona_name, public: isPublic})});
      const d = await r.json();
      if (d.success) { toast('成功','已保存','success'); closeLibraryEdit(); loadPersonaLibrary(); }
      else { toast('错误', d.error||'保存失败','error'); }
    } catch(e) { toast('错误','保存失败','error'); }
  };
  window.deleteLibraryItem = async function(name) {
    if (!confirm('确定删除 "'+name+'" ？')) return;
    try {
      const r = await fetch('/api/admin/persona-library/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
      const d = await r.json();
      if (d.success) { toast('成功','已删除','success'); loadPersonaLibrary(); }
      else { toast('错误', d.error||'删除失败','error'); }
    } catch(e) { toast('错误','删除失败','error'); }
  };


  window.toggleAllowAll = function(el) { document.querySelectorAll(".admin-allowed-persona").forEach(cb => { cb.checked = el.checked; }); };
})();










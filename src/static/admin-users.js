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
  let _editUser = null;
  document.addEventListener('DOMContentLoaded', async () => {
    bindAuth(); bindLogout(); bindActions();
    const btn = document.getElementById('menuBtn');
    if (btn) btn.addEventListener('click', () => document.getElementById('sidebar').classList.toggle('open'));
    try {
      const r = await fetch('/api/auth/me');
      if (!r.ok) { location.href='/login'; return; }
      const me = await r.json();
      if (!me || me.role !== 'admin') { location.href='/login'; return; }
      document.getElementById('layout').style.display = 'flex';
      loadUsers();
    } catch(e) { location.href='/login'; }
  });
  function showAuth() { document.getElementById('authView').style.display='flex'; document.getElementById('layout').style.display='none'; }
  function bindAuth() {
    const btn = document.getElementById('authLoginBtn');
    if (btn) btn.addEventListener('click', loginFromForm);
    ['authUser','authPass'].forEach(id => { const el = document.getElementById(id); if (el) el.addEventListener('keydown', e => { if (e.key === 'Enter') loginFromForm(); }); });
  }
  async function loginFromForm() {
    const err = document.getElementById('authError');
    const username = ((document.getElementById('authUser')||{}).value||'').trim();
    const password = ((document.getElementById('authPass')||{}).value||'');
    if (!username || !password) { if (err) err.textContent = '请输入账号和密码'; return; }
    try {
      const r = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username, password})});
      const d = await r.json();
      if (!r.ok || !d.success || d.role !== 'admin') { if (err) err.textContent = d.error || '请使用管理员账号'; return; }
      localStorage.setItem('kc_token', d.token);
      location.reload();
    } catch(e) { if (err) err.textContent = '登录失败'; }
  }
  function bindLogout() {
    const btn = document.getElementById('logoutBtn');
    if (!btn) return;
    btn.style.display = '';
    btn.addEventListener('click', async () => { try { await fetch('/api/auth/logout', {method:'POST'}); } catch(e) {} localStorage.removeItem('kc_token'); location.reload(); });
  }
  function bindActions() {
    document.getElementById('saveUserBtn').addEventListener('click', saveUser);
    document.getElementById('resetPwBtn').addEventListener('click', resetPassword);
    document.getElementById('setPersonaBtn').addEventListener('click', setDefaultPersona);
    document.getElementById('viewMessagesBtn').addEventListener('click', viewMessages);
    document.getElementById('addUserBtn').addEventListener('click', () => { const el = document.getElementById('addCard'); el.style.display = el.style.display === 'none' ? 'block' : 'none'; });
    document.getElementById('createUserBtn').addEventListener('click', createUser);
    document.getElementById('loadBaseBtn').addEventListener('click', loadBase);
    document.getElementById('savePersonaBtn').addEventListener('click', savePersona);
    document.getElementById('clearPersonaBtn').addEventListener('click', clearPersona);
    document.getElementById('epPersonaName').addEventListener('change', loadPersona);
  }
  async function loadUsers() {
    try {
      const r = await fetch('/api/admin/users');
      const d = await r.json();
      const el = document.getElementById('usersList');
      const users = d.users || [];
      if (!users.length) { el.innerHTML = '<div class="loading-text">暂无普通用户</div>'; return; }
      el.innerHTML = users.map(u => '<div class="dash-card" style="margin:8px 0;padding:16px"><div style="display:flex;justify-content:space-between;align-items:center"><div><div style="font-weight:600;font-size:15px">' + esc(u.username) + '</div><div style="color:var(--text-muted);font-size:12px;margin-top:4px">角色: ' + esc(u.persona) + ' · 覆盖: ' + ((u.persona_overrides||[]).length || 0) + '个 · 自定义性格: ' + (u.has_custom_persona_prompt ? '是' : '否') + '</div></div><div style="display:flex;gap:6px"><button class="btn" onclick="window.editUser(\'' + esc(u.username) + '\')">编辑配置</button><button class="btn" onclick="window.editPersona(\'' + esc(u.username) + '\',\'' + esc(u.persona) + '\')">编辑角色</button></div></div></div>').join('');
    } catch(e) { console.error('loadUsers', e); }
  }
  window.editUser = async function(username) {
    _editUser = username;
    document.getElementById('editCard').style.display = 'block';
    document.getElementById('editTitle').textContent = '编辑用户: ' + username;
    document.getElementById('euUsername').value = username;
    document.getElementById('euPassword').value = '';
    const st = document.getElementById('euStatus'); if (st) st.textContent = '';
    try {
      const r = await fetch('/api/admin/users');
      const d = await r.json();
      const u = (d.users||[]).find(x => x.username === username);
      if (u) { document.getElementById('euPersona').value = u.persona || ''; }
    } catch(e) {}
  };
  window.editPersona = async function(username, persona) {
    _editUser = username;
    document.getElementById('personaCard').style.display = 'block';
    document.getElementById('personaTitle').textContent = '角色覆盖: ' + username;
    document.getElementById('epPersonaName').value = persona || 'MONO';
    await loadPersona();
  };
  async function loadPersona() {
    if (!_editUser) return;
    const name = ((document.getElementById('epPersonaName')||{}).value||'MONO').trim();
    try {
      const r = await fetch('/api/admin/users/' + encodeURIComponent(_editUser) + '/persona/' + encodeURIComponent(name));
      const d = await r.json();
      document.getElementById('epContent').value = d.override_content || d.base_content || '';
    } catch(e) { console.error('loadPersona', e); }
  }
  async function loadBase() {
    if (!_editUser) return;
    const name = ((document.getElementById('epPersonaName')||{}).value||'MONO').trim();
    try {
      const r = await fetch('/api/admin/users/' + encodeURIComponent(_editUser) + '/persona/' + encodeURIComponent(name));
      const d = await r.json();
      document.getElementById('epContent').value = d.base_content || '';
      toast('提示','已读取默认','info');
    } catch(e) { toast('错误','读取失败','error'); }
  }
  async function savePersona() {
    if (!_editUser) return;
    const name = ((document.getElementById('epPersonaName')||{}).value||'MONO').trim();
    const content = ((document.getElementById('epContent')||{}).value||'').trim();
    const st = document.getElementById('epStatus');
    try {
      const r = await fetch('/api/admin/users/' + encodeURIComponent(_editUser) + '/persona/' + encodeURIComponent(name), {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({content})});
      const d = await r.json();
      if (d.success) { if (st) { st.textContent = '已保存'; st.className = 'cfg-status success'; } loadUsers(); } else { if (st) { st.textContent = d.error || '保存失败'; st.className = 'cfg-status error'; } }
    } catch(e) { if (st) { st.textContent = '保存失败'; st.className = 'cfg-status error'; } }
  }
  async function clearPersona() {
    if (!_editUser) return;
    const name = ((document.getElementById('epPersonaName')||{}).value||'MONO').trim();
    try {
      await fetch('/api/admin/users/' + encodeURIComponent(_editUser) + '/persona/' + encodeURIComponent(name), {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({content:''})});
      toast('提示','已清空覆盖','info');
      loadPersona();
    } catch(e) { toast('错误','清空失败','error'); }
  }
  async function saveUser() {
    if (!_editUser) return;
    const body = {};
    const password = ((document.getElementById('euPassword')||{}).value||'').trim();
    const persona = ((document.getElementById('euPersona')||{}).value||'').trim();
    const apiKey = ((document.getElementById('euApiKey')||{}).value||'').trim();
    const baseUrl = ((document.getElementById('euBaseUrl')||{}).value||'').trim();
    const model = ((document.getElementById('euModel')||{}).value||'').trim();
    if (password) body.password = password;
    if (persona) body.persona = persona;
    if (apiKey) body.api_key = apiKey;
    if (baseUrl) body.base_url = baseUrl;
    if (model) body.model = model;
    const st = document.getElementById('euStatus');
    try {
      const r = await fetch('/api/admin/users/update', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(Object.assign({username: _editUser}, body))});
      const d = await r.json();
      if (d.success) { if (st) { st.textContent = '已保存'; st.className = 'cfg-status success'; } loadUsers(); } else { if (st) { st.textContent = d.error || '保存失败'; st.className = 'cfg-status error'; } }
    } catch(e) { if (st) { st.textContent = '保存失败'; st.className = 'cfg-status error'; } }
  }

  async function createUser() {
    const username = ((document.getElementById('nuUsername')||{}).value||'').trim();
    const password = ((document.getElementById('nuPassword')||{}).value||'').trim();
    const persona = ((document.getElementById('nuPersona')||{}).value||'MONO').trim();
    const st = document.getElementById('nuStatus');
    if (!username || !password) { if (st) { st.textContent = '请填写用户名和密码'; st.className = 'cfg-status error'; } return; }
    try {
      const r = await fetch('/api/admin/users/create', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username, password, persona})});
      const d = await r.json();
      if (d.success) { if (st) { st.textContent = '已创建'; st.className = 'cfg-status success'; } document.getElementById('nuUsername').value=''; document.getElementById('nuPassword').value=''; loadUsers(); } else { if (st) { st.textContent = d.error || '创建失败'; st.className = 'cfg-status error'; } }
    } catch(e) { if (st) { st.textContent = '创建失败'; st.className = 'cfg-status error'; } }
  }
  window.deleteUser = async function(username) {
    if (!confirm('确定要删除用户 ' + username + ' 吗？')) return;
    try {
      const r = await fetch('/api/admin/users/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username})});
      const d = await r.json();
      if (d.success) { toast('成功','用户已删除','success'); loadUsers(); } else { toast('错误', d.error || '删除失败','error'); }
    } catch(e) { toast('错误','删除失败','error'); }
  };
  window.viewMemory = async function(username) {
    document.getElementById('memoryCard').style.display='block';
    document.getElementById('memoryTitle').textContent='用户记忆: '+username;
    try {
      const r = await fetch('/api/admin/users/'+encodeURIComponent(username)+'/memory?limit=20');
      const d = await r.json();
      const el = document.getElementById('memoryList');
      const mem = d.memory || [];
      if (!mem.length) { el.innerHTML='<div class="loading-text">暂无记忆</div>'; return; }
      el.innerHTML = mem.map(m => '<div style="padding:6px 0;border-bottom:1px solid var(--border)"><b>' + esc(m.role) + '</b>: ' + esc(m.content) + '</div>').join('');
    } catch(e) { console.error('viewMemory', e); }
  };

  async function resetPassword() {
    if (!_editUser) return;
    const pw = ((document.getElementById('euPassword')||{}).value||'').trim();
    const st = document.getElementById('euStatus');
    if (!pw) { if (st) { st.textContent = '请先填写新密码'; st.className = 'cfg-status error'; } return; }
    try {
      const r = await fetch('/api/admin/users/reset-password', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username: _editUser, new_password: pw})});
      const d = await r.json();
      if (d.success) { if (st) { st.textContent = '密码已重置'; st.className = 'cfg-status success'; } } else { if (st) { st.textContent = d.error || '重置失败'; st.className = 'cfg-status error'; } }
    } catch(e) { if (st) { st.textContent = '重置失败'; st.className = 'cfg-status error'; } }
  }
  async function setDefaultPersona() {
    if (!_editUser) return;
    const persona = ((document.getElementById('euPersona')||{}).value||'').trim();
    const st = document.getElementById('euStatus');
    if (!persona) { if (st) { st.textContent = '请先填写角色名称'; st.className = 'cfg-status error'; } return; }
    try {
      const r = await fetch('/api/admin/users/set-persona', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username: _editUser, persona})});
      const d = await r.json();
      if (d.success) { if (st) { st.textContent = '已设为默认角色'; st.className = 'cfg-status success'; } loadUsers(); } else { if (st) { st.textContent = d.error || '设置失败'; st.className = 'cfg-status error'; } }
    } catch(e) { if (st) { st.textContent = '设置失败'; st.className = 'cfg-status error'; } }
  }
  async function viewMessages() {
    if (!_editUser) return;
    document.getElementById('messagesCard').style.display = 'block';
    document.getElementById('messagesTitle').textContent = '用户消息: ' + _editUser;
    try {
      const r = await fetch('/api/admin/users/' + encodeURIComponent(_editUser) + '/messages?limit=100');
      const d = await r.json();
      const el = document.getElementById('messagesList');
      const msgs = d.messages || [];
      if (!msgs.length) { el.innerHTML = '<div class="loading-text">暂无消息</div>'; return; }
      el.innerHTML = msgs.map(m => '<div style="padding:6px 0;border-bottom:1px solid var(--border)"><div style="color:var(--text-muted);font-size:11px">' + esc(m.created_at) + ' · ' + esc(m.role) + '</div><div>' + esc(m.content) + '</div></div>').join('');
    } catch(e) { console.error('viewMessages', e); }
  }
  function esc(s) { return s ? s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;') : ''; }
  function toast(t,m,tp) {
    const c=document.getElementById('toasts'); if(!c) return;
    const d=document.createElement('div'); d.className='toast toast-'+(tp||'info'); d.innerHTML='<div class="toast-title">'+esc(t)+'</div><div class="toast-msg">'+esc(m)+'</div>';
    c.appendChild(d); setTimeout(()=>{d.classList.add('fade-out'); setTimeout(()=>d.remove(),300);},3000);
  }
  window.toast=toast;
})();

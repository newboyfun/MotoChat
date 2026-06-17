(function() {
  'use strict';
  var _navigating = false;
  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('authLoginBtn');
    if (btn) btn.addEventListener('click', login);
    ['authUser','authPass'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('keydown', e => { if (e.key === 'Enter') login(); });
    });
    checkAlreadyLogin();
  });

  async function checkAlreadyLogin() {
    if (_navigating) return;
    try {
      const token = localStorage.getItem('kc_token');
      if (!token) return;
      const r = await fetch('/api/auth/me');
      if (!r.ok) { localStorage.removeItem('kc_token'); return; }
      const me = await r.json();
      if (!me || !me.username) { localStorage.removeItem('kc_token'); return; }
      _navigating = true;
      if (me.role === 'admin') { location.replace('/admin'); return; }
      if (me.role === 'user') { location.replace('/user'); return; }
      _navigating = false;
    } catch(e) { localStorage.removeItem('kc_token'); }
  }

  async function login() {
    if (_navigating) return;
    const err = document.getElementById('authError');
    const username = ((document.getElementById('authUser')||{}).value||'').trim();
    const password = ((document.getElementById('authPass')||{}).value||'');
    if (!username || !password) { if (err) err.textContent = '请输入账号和密码'; return; }
    if (err) err.textContent = '';
    try {
      _navigating = true;
      const r = await fetch('/api/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username, password}), signal: AbortSignal.timeout(10000) });
      const d = await r.json();
      if (!r.ok || !d.success) { if (err) err.textContent = d.error || '登录失败'; _navigating = false; return; }
      localStorage.setItem('kc_token', d.token);
      if (d.role === 'admin') location.replace('/admin?_=' + Date.now());
      else location.replace('/user?_=' + Date.now());
    } catch(e) { if (err) err.textContent = '登录失败'; _navigating = false; }
  }
})();
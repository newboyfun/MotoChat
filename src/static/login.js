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
      // 认证走 HttpOnly Cookie，直接询问后端即可
      const r = await fetch('/api/auth/me');
      if (!r.ok) return;
      const me = await r.json();
      if (!me || !me.username) return;
      _navigating = true;
      if (me.role === 'admin') { location.replace('/admin'); return; }
      if (me.role === 'user') { location.replace('/user'); return; }
      _navigating = false;
    } catch(e) { /* 未登录，停留在登录页 */ }
  }

  async function login() {
    if (_navigating) return;
    const err = document.getElementById('authError');
    const btn = document.getElementById('authLoginBtn');
    const username = ((document.getElementById('authUser')||{}).value||'').trim();
    const password = ((document.getElementById('authPass')||{}).value||'');
    if (!username || !password) { if (err) err.textContent = '请输入账号和密码'; return; }
    if (err) err.textContent = '';
    _navigating = true;
    if (btn) btn.classList.add('loading');
    try {
      const r = await fetch('/api/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username, password}), signal: AbortSignal.timeout(10000) });
      const d = await r.json();
      if (!r.ok || !d.success) { if (err) err.textContent = d.error || '登录失败'; _navigating = false; if (btn) btn.classList.remove('loading'); return; }
      // 认证依赖 HttpOnly Cookie（后端已 Set-Cookie），不再将 token 存入 localStorage，并清理旧版遗留
      try { localStorage.removeItem('kc_token'); } catch(e) {}
      if (d.role === 'admin') location.replace('/admin?_=' + Date.now());
      else location.replace('/user?_=' + Date.now());
    } catch(e) {
      const msg = (e && e.name === 'TimeoutError') ? '连接超时，请检查网络' : '登录失败';
      if (err) err.textContent = msg;
      _navigating = false;
      if (btn) btn.classList.remove('loading');
    }
  }
})();
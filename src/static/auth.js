/**
 * 认证辅助 — 认证统一走 HttpOnly Cookie (kc_token)
 * 不再从 localStorage 注入 Authorization header：
 * 遗留的过期 token 会覆盖有效 Cookie 导致误判登出，这里只做一次性清理
 */
(function() {
  'use strict';
  try { localStorage.removeItem('kc_token'); } catch(e) {}
})();

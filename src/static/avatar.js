/* MotoChat — Persona Avatar Helper
 * 头像 URL 统一走后端 /api/personas/{name}/avatar：
 *   用户上传的自定义头像优先，其次内置专属头像，最后默认玻璃星球。
 * 用法:
 *   MotoAvatars.url('MONO')           -> 头像 URL
 *   MotoAvatars.apply(el, 'MONO')     -> 把字母头像元素替换为 <img>
 *   MotoAvatars.img('MONO')           -> 返回 innerHTML 字符串
 *   MotoAvatars.bust('MONO')          -> 上传新头像后刷新缓存（追加版本号）
 */
(function () {
  'use strict';
  var _versions = {};  // name -> 版本号（上传新头像后更新，强制浏览器重新拉取）

  window.MotoAvatars = {
    url: function (name) {
      var base = '/api/personas/' + encodeURIComponent(name || '') + '/avatar';
      var v = _versions[(name || '').toUpperCase()];
      return v ? base + '?v=' + v : base;
    },
    /* 把已有的字母头像元素内容替换为图片（保留元素本身的 class/样式） */
    apply: function (el, name) {
      if (!el) return;
      el.innerHTML = '';
      var img = document.createElement('img');
      img.src = this.url(name);
      img.alt = name || '';
      img.loading = 'lazy';
      el.appendChild(img);
      el.classList.add('has-img');
    },
    /* 生成头像 img 的 HTML 字符串（用于字符串拼接场景） */
    img: function (name) {
      return '<img src="' + this.url(name) + '" alt="" loading="lazy">';
    },
    /* 上传新头像后调用：刷新该角色所有头像的缓存 */
    bust: function (name, version) {
      _versions[(name || '').toUpperCase()] = version || Date.now();
    }
  };
})();

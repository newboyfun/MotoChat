/* MotoChat v3 — Theme Manager */
(function() {
  'use strict';
  var KEY = 'motochat_theme';

  function getStored() {
    try { return localStorage.getItem(KEY); } catch(e) { return null; }
  }
  function setStored(v) {
    try { localStorage.setItem(KEY, v); } catch(e) {}
  }

  function getSystem() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function apply(theme) {
    if (theme === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
  }

  var current = getStored() || getSystem();
  apply(current);

  window.mcTheme = {
    get: function() { return document.documentElement.hasAttribute('data-theme') ? 'dark' : 'light'; },
    toggle: function() {
      var next = this.get() === 'dark' ? 'light' : 'dark';
      apply(next);
      setStored(next);
      return next;
    },
    set: function(theme) { apply(theme); setStored(theme); }
  };

  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
    if (!getStored()) apply(e.matches ? 'dark' : 'light');
  });
})();

/* Interface-only localization; never walk or rewrite article/user DOM. */
(() => {
  'use strict';
  const locale = document.documentElement.lang === 'en' ? 'en' : 'zh-CN';
  const catalog = locale === 'en' ? (window.WikitideEnglish || {}) : {};
  const patterns = (window.WikitideEnglishPatterns || []).map(key => ({key,
    pattern: new RegExp('^' + key.split(/(\{\d+\})/).map(part => /^\{\d+\}$/.test(part) ? '(.*?)' : part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('') + '$')}));
  function t(value, parameters = []) {
    if (typeof value !== 'string') return value;
    const key = value.trim();
    const leading = value.slice(0, value.length - value.trimStart().length);
    const trailing = value.slice(value.trimEnd().length);
    let translated = Object.prototype.hasOwnProperty.call(catalog, key) ? catalog[key] : key;
    if (locale === 'en' && translated === key && parameters.length === 0 && key.length <= 2048) {
      for (const entry of patterns) {
        const match=entry.pattern.exec(key);
        if (match && catalog[entry.key]) {translated=catalog[entry.key];parameters=match.slice(1);break;}
      }
    }
    return leading + translated.replace(/\{(\d+)\}/g, (match, index) =>
      Number(index) < parameters.length ? String(parameters[Number(index)]) : match) + trailing;
  }
  const escapeAttribute = value => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll("'", '&#39;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  const escapeText = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  function html(source) {
    if (locale !== 'en') return source;
    // This function accepts only source-code literals, before interpolation.
    return source.split(/(<!--[\s\S]*?-->|<[^>]*>)/g).map(part => {
      if (part.startsWith('<!--')) return part;
      if (part.startsWith('<')) return part.replace(/\b(aria-label|aria-description|title|placeholder|alt|data-confirm)=("[^"]*"|'[^']*')/g,
        (_whole, name, quoted) => name + '=' + quoted[0] + escapeAttribute(t(quoted.slice(1,-1))) + quoted.at(-1));
      return /[\u3400-\u9fff]/.test(part) ? escapeText(t(part)) : part;
    }).join('');
  }
  function fragments(parts, values) {
    return parts.map((part, index) => html(part) + (index < values.length ? values[index] : '')).join('');
  }
  window.WikitideI18n = Object.freeze({locale, t, html, fragments});
  document.addEventListener('DOMContentLoaded', () => {
    // Preserve the current reading fragment as well as path and search filters.
    document.querySelectorAll('[data-language-switch]').forEach(anchor => {
      anchor.addEventListener('click', () => {
        const next = new URL(location.href);
        next.searchParams.delete('lang');
        const target = new URL(anchor.href);
        target.searchParams.set('next', next.pathname + next.search + next.hash);
        anchor.href = target.pathname + target.search;
      });
    });
  });
})();

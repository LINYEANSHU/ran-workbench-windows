/* Shared, offline UI localization. Project data never enters the dictionary. */
(function () {
  'use strict';
  const dictionaries = new Map(), listeners = new Set(), nodes = new WeakMap(), attributes = new WeakMap();
  const rendered = new Map();
  let stored;
  try { stored = window.localStorage?.getItem('ran-language'); } catch (_) { /* restricted storage */ }
  let language = window.RAN_LANGUAGE === 'en' || window.RAN_LANGUAGE === 'zh-CN' ? window.RAN_LANGUAGE : stored === 'en' ? 'en' : 'zh-CN';
  const interpolate = (text, args) => text.replace(/\{(\d+)\}/g, (match, index) => index < args.length ? String(args[index] ?? '') : match);
  function t(source, ...args) {
    if (source == null) return '';
    source = String(source);
    const translated = dictionaries.get(source) || source;
    const original = interpolate(source, args), english = interpolate(translated, args);
    // Used only by explicit UI bindings; never applied to project/user fields.
    if (translated !== source) {
      const record = {source, args}; rendered.set(original, record); rendered.set(english, record);
      while (rendered.size > 20000) rendered.delete(rendered.keys().next().value);
    }
    return language === 'en' ? english : original;
  }
  function ignored(element) {
    return !!element?.closest?.('[data-i18n-ignore],script,style,textarea,pre,code,[contenteditable="true"]');
  }
  function translatePage(root = document) {
    function visit(node) {
      if (node.nodeType === 3) {
        if (ignored(node.parentElement)) return;
        const value = node.nodeValue;
        let record = nodes.get(node);
        if (!record || value !== record.last) record = {source: value, last: value};
        const trimmed = record.source.trim();
        const translated = dictionaries.has(trimmed) ? record.source.replace(trimmed, t(trimmed)) : record.source;
        if (node.nodeValue !== translated) node.nodeValue = translated;
        record.last = translated; nodes.set(node, record); return;
      }
      if (node.nodeType === 1) {
        if (ignored(node)) return;
        let records = attributes.get(node);
        if (!records) { records = new Map(); attributes.set(node, records); }
        for (const name of ['title', 'aria-label', 'placeholder', 'alt']) {
          if (!node.hasAttribute(name)) continue;
          const value = node.getAttribute(name);
          let record = records.get(name);
          if (!record || value !== record.last) record = {source: value};
          const translated = t(record.source);
          if (value !== translated) node.setAttribute(name, translated);
          record.last = translated; records.set(name, record);
        }
      }
      for (const child of node.childNodes || []) visit(child);
    }
    visit(root);
  }
  function refresh() {
    if (document.documentElement) document.documentElement.lang = language;
    translatePage();
    for (const callback of [...listeners]) callback(language);
  }
  const api = {
    register(dictionary) {
      for (const [source, english] of Object.entries(dictionary)) if (typeof english === 'string') dictionaries.set(source, english);
      translatePage();
    },
    t,
    // Re-evaluate a string previously created by t(), for a captured UI caption.
    rendered(value) { const record = rendered.get(String(value)); return record ? t(record.source, ...record.args) : t(value); },
    setLanguage(next) {
      if (next !== 'zh-CN' && next !== 'en') throw new RangeError('Unsupported language: ' + next);
      language = next; window.RAN_LANGUAGE = next;
      try { window.localStorage?.setItem('ran-language', next); } catch (_) { /* restricted storage */ }
      refresh();
    },
    get language() { return language; },
    onChange(callback) { listeners.add(callback); return () => listeners.delete(callback); },
    translatePage
  };
  window.RANI18n = Object.freeze(api);
  if (document.documentElement) document.documentElement.lang = language;
  function start() {
    translatePage();
    if (typeof window.MutationObserver === 'function') {
      new window.MutationObserver(records => {
        for (const record of records) {
          if (record.type === 'childList') for (const node of record.addedNodes) translatePage(node);
          else translatePage(record.target);
        }
      }).observe(document.documentElement, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['title', 'aria-label', 'placeholder', 'alt']});
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();

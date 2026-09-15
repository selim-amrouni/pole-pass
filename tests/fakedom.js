// Minimal DOM stand-in for running web/app.js under node without a browser or a map library.
'use strict';
let active = null;
class El {
  constructor(tag, id) { this.tagName = (tag || 'div').toUpperCase(); this.id = id || ''; this.children = []; this.attrs = {}; this.dataset = {}; this.style = {}; this._html = ''; this.hidden = false; this.value = ''; this.checked = false; this.disabled = false; this.listeners = {}; this.parent = null; this.textContent = ''; this.className = ''; this.open = false;
    this.classList = { add: (...c) => c.forEach(x => this._cls().add(x)), remove: (...c) => c.forEach(x => this._cls().delete(x)), toggle: (c, f) => f === undefined ? (this._cls().has(c) ? this._cls().delete(c) : this._cls().add(c)) : (f ? this._cls().add(c) : this._cls().delete(c)), contains: c => this._cls().has(c) };
  }
  _cls() { if (!this._set) this._set = new Set(); return this._set; }
  get innerHTML() { return this._html; }
  set innerHTML(h) { this._html = h; this.children = parse(h, this); }
  insertAdjacentHTML(where, h) { const kids = parse(h, this.parent || this); if (where === 'afterend' && this.parent) { const i = this.parent.children.indexOf(this); this.parent.children.splice(i + 1, 0, ...kids); } else this.children.push(...kids); }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') this.id = v; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  dispatch(t, ev = {}) { ev.target = ev.target || this; ev.preventDefault = () => {}; let n = this; while (n) { (n.listeners[t] || []).forEach(f => f(ev)); n = n.parent; } }
  click() { this.dispatch('click'); }
  focus() { active = this; }
  scrollIntoView() {}
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); }
  closest(sel) { let n = this; while (n) { if (matches(n, sel)) return n; n = n.parent; } return null; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) { const out = []; const walk = n => { n.children.forEach(c => { if (sel.split(',').some(s => matches(c, s.trim()))) out.push(c); walk(c); }); }; walk(this); return out; }
  showModal() { this.open = true; } close() { this.open = false; }
}
function matches(el, sel) {
  if (!(el instanceof El)) return false;
  // supports: tag, .class, #id, [attr=value], [role=tab], combos like .row[data-id="x"], and descendant "a b" (first part ignored)
  const last = sel.trim().split(/\s+/).pop();
  const m = last.match(/^([a-z]*)((?:[.#][\w-]+)*)((?:\[[^\]]+\])*)$/i); if (!m) return false;
  const [, tag, clsid, attrs] = m;
  if (tag && el.tagName !== tag.toUpperCase()) return false;
  for (const p of (clsid.match(/[.#][\w-]+/g) || [])) { if (p[0] === '.' && !el._cls().has(p.slice(1))) return false; if (p[0] === '#' && el.id !== p.slice(1)) return false; }
  for (const a of (attrs.match(/\[[^\]]+\]/g) || [])) { const [, k, , v] = a.match(/^\[([\w-]+)(=("?)(.*?)\3)?\]$/) || []; const val = v === undefined ? undefined : a.replace(/^\[[\w-]+=/, '').replace(/\]$/, '').replace(/^"|"$/g, ''); if (k === undefined) return false; if (val === undefined ? !(k in el.attrs) && !(k.startsWith('data-') && el.dataset[k.slice(5)] !== undefined) : (el.attrs[k] ?? (k.startsWith('data-') ? el.dataset[k.slice(5)] : undefined)) !== val) return false; }
  return true;
}
function parse(html, parent) {
  // tolerant tag parser: builds element tree from tags, keeps text
  const out = []; const stack = [{ el: parent instanceof El ? parent : { children: out, parent, listeners: {} }, tag: '' }];
  const re = /<\/?([a-z][a-z0-9]*)([^>]*)>|([^<]+)/gi; let m;
  while ((m = re.exec(html))) {
    if (m[3] !== undefined) { const top = stack[stack.length - 1].el; if (top instanceof El) top.textContent += m[3]; continue; }
    const closing = m[0][1] === '/'; const tag = m[1].toLowerCase();
    if (closing) { if (stack.length > 1 && stack[stack.length - 1].tag === tag) stack.pop(); continue; }
    const el = new El(tag); const top = stack[stack.length - 1].el; el.parent = top instanceof El ? top : (parent instanceof El ? parent : null);
    for (const a of (m[2].match(/([\w-]+)(?:="([^"]*)")?/g) || [])) { const [, k, v] = a.match(/^([\w-]+)(?:="([^"]*)")?$/); el.attrs[k] = v ?? ''; if (k === 'id') el.id = v; if (k === 'class') (v || '').split(/\s+/).filter(Boolean).forEach(c => el._cls().add(c)); if (k.startsWith('data-')) el.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = v; if (k === 'hidden') el.hidden = true; if (k === 'disabled') el.disabled = true; if (k === 'value') el.value = v; }
    (top instanceof El && top !== parent ? top.children : out).push(el);
    if (!/^(img|input|br|hr|meta|link)$/.test(tag) && !m[2].endsWith('/')) stack.push({ el, tag });
  }
  return out;
}
function makeDocument(ids) {
  const root = new El('body');
  ids.forEach(([id, tag]) => { const e = new El(tag || 'div', id); root.appendChild(e); });
  const doc = {
    body: root, get activeElement() { return active; },
    getElementById: id => root.querySelectorAll('#' + id)[0] || (root.children.find(c => c.id === id) || null),
    querySelector: s => root.querySelector(s), querySelectorAll: s => root.querySelectorAll(s),
    createElement: t => new El(t), addEventListener: (t, f) => root.addEventListener(t, f),
  };
  return doc;
}
module.exports = { El, makeDocument };

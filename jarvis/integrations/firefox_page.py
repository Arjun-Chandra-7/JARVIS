"""The four things Jarvis does to a page, done through Marionette.

`browser.py` reaches into a Chromium page with CDP: find the element whose visible text matches,
click its centre with a trusted input event, read the text, type into a field. None of that
exists for a Firefox browser, so it is rebuilt here on the one primitive Marionette gives —
running JavaScript in the page — with the same rules the CDP version follows.

Those rules are the point, and they are why this is not four one-liners:

*Visible text, not selectors.* "Click the play button" has to find what a person would call the
play button, which means walking the elements that can be pressed and matching their text, aria
label and title — not guessing a CSS path that breaks on the next redesign.

*Visible, not merely present.* A page is full of hidden menus containing exactly the words being
searched for. An element with no layout box is not on screen, and clicking it does nothing while
reporting success.

*Refuse when uncertain.* Two equally good matches means the wrong one is a coin flip, and a coin
flip that clicks is worse than an apology. The CDP version refuses; so does this.

The scripts are written once, as constants, because a script built by string-formatting a user's
words into JavaScript is an injection waiting to happen. Everything variable arrives through
`arguments`, which Marionette passes as data.
"""

from __future__ import annotations

from typing import Any, Optional

# Elements a person would describe as pressable. Deliberately not "everything with an onclick":
# that matches the page's own scroll handlers and body wrappers.
_PRESSABLE = (
    "a, button, [role=button], [role=link], [role=menuitem], [role=tab], "
    "input[type=submit], input[type=button], summary, [onclick]"
)

# One helper, shared by every script below, so "what counts as visible" and "what counts as this
# element's text" are defined once rather than three times slightly differently.
_HELPERS = """
const seen = (el) => {
  if (!el) return false;
  const box = el.getBoundingClientRect();
  if (box.width < 1 || box.height < 1) return false;
  const style = window.getComputedStyle(el);
  return style.visibility !== 'hidden' && style.display !== 'none' &&
         parseFloat(style.opacity || '1') > 0.05;
};
const label = (el) => (
  (el.innerText || '') + ' ' +
  (el.getAttribute('aria-label') || '') + ' ' +
  (el.getAttribute('title') || '') + ' ' +
  (el.getAttribute('placeholder') || '') + ' ' +
  (el.value && typeof el.value === 'string' ? el.value : '')
).replace(/\\s+/g, ' ').trim();
"""

READ_TEXT = """
return (document.body && document.body.innerText) ? document.body.innerText : '';
"""

CLICK_BY_TEXT = _HELPERS + """
const want = (arguments[0] || '').toLowerCase().trim();
if (!want) return {ok: false, why: 'nothing to look for'};
const all = Array.from(document.querySelectorAll(%(pressable)s)).filter(seen);
const exact = all.filter(e => label(e).toLowerCase() === want);
const partial = all.filter(e => label(e).toLowerCase().includes(want));
const hits = exact.length ? exact : partial;
if (hits.length === 0) return {ok: false, why: 'nothing on the page says that'};
if (hits.length > 1 && exact.length !== 1) {
  return {ok: false, why: 'more than one thing says that',
          choices: hits.slice(0, 5).map(e => label(e).slice(0, 60))};
}
const el = hits[0];
el.scrollIntoView({block: 'center'});
el.click();
return {ok: true, clicked: label(el).slice(0, 80)};
""" % {"pressable": repr(_PRESSABLE)}

TYPE_INTO = _HELPERS + """
const want = (arguments[0] || '').toLowerCase().trim();
const text = arguments[1] || '';
const submit = !!arguments[2];
const fields = Array.from(document.querySelectorAll(
  'input, textarea, [contenteditable=""], [contenteditable=true]')).filter(seen);
let el = null;
if (want) {
  el = fields.find(f => label(f).toLowerCase().includes(want)) || null;
  if (!el) {
    // A field is often labelled by something beside it rather than on it.
    el = fields.find(f => {
      const id = f.getAttribute('id');
      if (!id) return false;
      const lab = document.querySelector('label[for="' + CSS.escape(id) + '"]');
      return lab && (lab.innerText || '').toLowerCase().includes(want);
    }) || null;
  }
}
if (!el) el = fields.find(f => f === document.activeElement) || fields[0] || null;
if (!el) return {ok: false, why: 'no field to type into'};
el.focus();
if (el.isContentEditable) {
  el.textContent = text;
} else {
  const setter = Object.getOwnPropertyDescriptor(
    el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
    'value').set;
  setter.call(el, text);
}
// React and friends listen for these rather than watching the property, so a value set without
// them is a value the page never notices.
el.dispatchEvent(new Event('input', {bubbles: true}));
el.dispatchEvent(new Event('change', {bubbles: true}));
if (submit) {
  const form = el.closest('form');
  if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); }
  else {
    for (const type of ['keydown', 'keypress', 'keyup']) {
      el.dispatchEvent(new KeyboardEvent(type, {key: 'Enter', code: 'Enter',
                                                keyCode: 13, which: 13, bubbles: true}));
    }
  }
}
return {ok: true, typed: text.length};
"""

FIND_CLICKABLE = _HELPERS + """
return Array.from(document.querySelectorAll(%(pressable)s))
  .filter(seen)
  .map(e => label(e).slice(0, 70))
  .filter(Boolean)
  .slice(0, 60);
""" % {"pressable": repr(_PRESSABLE)}


def _as_dict(result: Any) -> dict:
    """Marionette hands back whatever the script returned; callers want a dict."""
    return result if isinstance(result, dict) else {"ok": False, "why": "no answer from the page"}


def read_text(conn, limit: int = 20000) -> str:
    return (conn.script(READ_TEXT) or "")[:limit]


def click_text(conn, phrase: str) -> dict:
    """Press the thing the page calls `phrase`. Refuses when it is not sure which."""
    return _as_dict(conn.script(CLICK_BY_TEXT, [phrase]))


def type_into(conn, field: str, text: str, submit: bool = True) -> dict:
    return _as_dict(conn.script(TYPE_INTO, [field, text, submit]))


def clickable(conn) -> list[str]:
    """What there is to press, for when a click missed and the reply should say what was there."""
    got = conn.script(FIND_CLICKABLE)
    return list(got) if isinstance(got, list) else []

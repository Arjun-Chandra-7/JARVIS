"""«open X» — resolved deterministically, without asking the model.

Measured over five attempts each, with the local 3B brain deciding:

    "open friends"          worked 0/5   (3 of 5 called no tool at all)
    "open f.r.i.e.n.d.s"    worked 3/5

"Open X" is the single most common thing said to Jarvis and it has exactly one meaning, so
routing it through a small model's judgment buys nothing and loses most of the attempts. The
resolution order below is unambiguous, needs no inference, and answers in milliseconds instead of
seconds.

Order, first match wins:

1. An installed application — "open Opera GX", "open VS Code".
2. A known website or a URL — "open Netflix", "open github.com".
3. Something to find on the site already open — "open Friends" while on Netflix searches Netflix.
4. Otherwise a web search.

Anything that is not shaped like "open X" is handed back to the model untouched.
"""

from __future__ import annotations

import re
from typing import Optional

# "put on" and "watch" are how people ask for something to play; "show me" is deliberately absent
# because it is far more often a question ("show me my calendar") than a launch.
_OPEN_RE = re.compile(
    r"""^(?:please\s+)?
        (?:open|launch|start|run|play|watch|put\s+on)
        \s+(?:up\s+)?
        (?P<target>.+?)
        (?:\s+(?:please|now|for\s+me))?$""",
    re.IGNORECASE | re.VERBOSE,
)

# "open Friends on Netflix" — the site is where to look, the rest is what to look for.
_ON_SITE_RE = re.compile(r"^(?P<what>.+?)\s+(?:on|in|using)\s+(?P<where>[\w .-]{2,30})$", re.I)

_LEADING_ARTICLE = re.compile(r"^(?:the|my|a|an)\s+", re.IGNORECASE)

# Phrases that look like "open X" but are not a launch request.
# The noun is often qualified — "a bank account", "a new project", "the front door" — so a few
# words are allowed in front of it. "open a bank account" was being sent to a web search.
_NOT_A_LAUNCH = re.compile(
    r"""(?ix)^(?:
        (?:the\s+|a\s+|an\s+|my\s+)?
        (?:[\w'-]+\s+){0,2}
        (?: door | window | (?:new\s+)?tab | file | folder | eyes | mouth | conversation |
            account | issue | ticket | pull\s+request | pr | box | curtains |
            project | advocate | argument | case | discussion | debate | business |
            bottle | can | jar | packet | parcel | present | gift )
        s?
      | (?:a\s+|an\s+)?new\s+[\w'-]+          # "a new project", "new business"
      | up (?:\s+to\s+.*)?
    )$""",
)


# Whisper runs the verb into the next word when they are spoken quickly: "open Netflix" came back
# as "OpenNet Flix". Splitting a glued verb costs nothing and recovers the command.
# The verb may be capitalised or not, but the word stuck to it must genuinely start with a capital
# — otherwise "OpenAI" and "openbox" get taken apart too.
_GLUED_VERB = re.compile(r"\b(?i:open|play|watch|start|launch)(?=[A-Z][a-z])")


def unglue(text: str) -> str:
    return _GLUED_VERB.sub(lambda m: m.group(0) + " ", text or "")


def parse(text: str) -> Optional[str]:
    """The thing to open, or None when this is not an open request."""
    cleaned = unglue((text or "").strip()).strip().rstrip(".!?")
    if not cleaned:
        return None
    match = _OPEN_RE.match(cleaned)
    if not match:
        return None
    target = match.group("target").strip().strip("\"'")
    # Checked before the article is removed, so "open the door" is still recognised as not a launch.
    if not target or _NOT_A_LAUNCH.match(target):
        return None
    target = _LEADING_ARTICLE.sub("", target).strip() or target
    # "open the second one" and similar need conversational context; leave those to the model.
    if re.fullmatch(r"(?:it|that|this|one|them|those)", target, re.IGNORECASE):
        return None
    return target


async def run(target: str, config) -> str:
    """Carry out «open target» and report what actually happened."""
    from .integrations import browser, desktop_apps

    # "open Friends on Netflix" — go to the site first, then look for the title there. Only when
    # the trailing part really names a site, so "play music on shuffle" is untouched.
    pair = _ON_SITE_RE.match(target)
    if pair:
        where = pair.group("where").strip()
        what = pair.group("what").strip()

        # "open YouTube on Chrome" names the browser to use. It has the same shape as "open
        # Friends on Netflix", which names a site to search inside, so it was read the second way
        # and the page opened in whichever browser Jarvis normally drives.
        from .integrations import apps

        if apps.browser_named(where):
            url = browser.resolve_site(what)
            if apps.open_url_in(where, url):
                return f"Opened {what} in {where}."
            return f"I couldn't start {where}."
        if browser._is_known_destination(where) or browser._closest_site(where.lower()):
            if config.browser_control and browser.ensure(browser.resolve_site(where))["ok"]:
                await browser.open_site(where)
                import asyncio

                await asyncio.sleep(1.5)
                found = await browser.search_here(what)
                if found.get("ok"):
                    return f"Searched {where} for {what}. {found.get('found', '')}".strip()
            target = what        # could not search there; fall through with the title alone

    # 1) An installed application wins — "open Spotify" means the app, not the website.
    app = desktop_apps.resolve(target)
    if app is not None:
        if desktop_apps.launch(app):
            from . import context
            context.note_opened(app=app.name, target=app.name)
            return f"Opened {app.name}."
        return f"I found {app.name} but couldn't start it."

    if not config.browser_control:
        from .integrations import apps

        opened = apps.open_url(browser.resolve_site(target))
        return f"Opened {target}." if opened else f"I couldn't open {target}."

    state = browser.ensure(browser.resolve_site(target))
    if not state["ok"]:
        if state["state"] == "needs_restart":
            from .integrations import apps

            opened = apps.open_url(browser.resolve_site(target))
            return (f"Opened {target}. {state['message']}" if opened
                    else f"I couldn't open {target}. {state['message']}")
        return state["message"]

    # 2/3/4) A destination is navigated to; anything else is searched for on the site already
    # open, and failing that on the web. open_site already encodes exactly this.
    result = await browser.open_site(target)
    if result.get("ok"):
        from . import context
        context.note_opened(site=target, target=target)
        return result.get("message") or f"Opened {target}."
    return result.get("error") or f"I couldn't open {target}."


# Speech puts things in front of the verb — a mis-heard wake word, a false start, a filler word.
# Allowing a short run-up before "open" recovers those, but only when the thing named afterwards
# actually resolves, so a garbled sentence can never launch something at random.
_LOOSE_OPEN_RE = re.compile(
    r"\b(?:open|launch|start|play|watch|put\s+on)\s+(?P<target>[\w .+-]{2,40})$",
    re.IGNORECASE,
)


def parse_loose(text: str) -> Optional[str]:
    """A last-resort target from an imperfect transcription, or None."""
    cleaned = unglue((text or "").strip()).strip().rstrip(".!?")
    match = _LOOSE_OPEN_RE.search(cleaned)
    if not match:
        return None
    raw = match.group("target").strip()
    # The same guard as the strict path: "open the window" fuzzy-matched the app Bottles, and
    # "open a bottle" is a drink far more often than it is a launch.
    if _NOT_A_LAUNCH.match(raw):
        return None
    target = _LEADING_ARTICLE.sub("", raw).strip()
    return None if _NOT_A_LAUNCH.match(target) else (target or None)


def _resolves(target: str) -> bool:
    """True when this names something real — an installed app or a known site."""
    try:
        from .integrations import browser, desktop_apps

        if desktop_apps.resolve(target) is not None:
            return True
        return (browser._is_known_destination(target)
                or browser._closest_site(target.lower()) is not None)
    except Exception:  # noqa: BLE001
        return False


async def handle(text: str, config) -> Optional[str]:
    """Entry point for the deterministic command layer. None means 'not mine'."""
    target = parse(text)
    if target is None:
        loose = parse_loose(text)
        # Only act on a loose match that names something real; otherwise let the model decide.
        target = loose if (loose and _resolves(loose)) else None
    if target is None:
        return None
    try:
        return await run(target, config)
    except Exception as exc:  # noqa: BLE001 - never let this swallow a turn; fall back to the model
        return None if isinstance(exc, (ImportError, AttributeError)) else \
            f"I couldn't open {target} — {exc}"

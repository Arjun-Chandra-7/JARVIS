"""Multi-step tasks: a plan of small verified steps, not one large hopeful instruction.

"Play the latest Iman Ghadzi video on YouTube" is four actions — get the browser up, go to
YouTube, search the name, open the first video — and the reason it did not work before is that
each one was left to a 3B model to think of, in order, while remembering what it had already
done. Measured earlier in this project, that model called no tool at all on three of five
attempts at «open friends»; a four-step chain compounds that until it almost never finishes.

So a task is planned first and executed second. The plan is a list of steps drawn from a fixed
vocabulary, every one of which maps to a primitive that already reports honestly whether it
worked. Between steps the runner looks at where it actually is rather than assuming the last
step did what it said. When a step fails the task stops there and says which one and why —
a half-finished task described accurately is worth more than a finished-sounding sentence.

Plans come from a recipe when the request has a familiar shape, and from the model otherwise.
Either way the steps are validated against the same vocabulary before anything runs, so a model
that invents an action cannot execute one.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Optional

MAX_STEPS = 8


@dataclass
class Step:
    action: str
    argument: str = ""
    # What this step is for, in the user's terms, so a failure report reads like an explanation.
    purpose: str = ""


@dataclass
class StepResult:
    step: Step
    ok: bool
    detail: str = ""


@dataclass
class TaskReport:
    request: str
    results: list[StepResult] = field(default_factory=list)
    finished: bool = False

    def spoken(self) -> str:
        """What actually happened, short enough to say out loud."""
        done = [r for r in self.results if r.ok]
        if self.finished:
            last = done[-1].detail if done else ""
            return last or f"Done — {len(done)} steps."
        failed = next((r for r in self.results if not r.ok), None)
        if failed is None:
            return "I didn't get anywhere with that."
        got = f"I got as far as {done[-1].step.purpose or done[-1].step.action}" if done \
            else "I couldn't make a start"
        return f"{got}, then {failed.detail or 'the next step failed'}."


# --------------------------------------------------------------------------- the step vocabulary
# Every action here is a primitive that already verifies itself and reports honestly.
ACTIONS = ("open_app", "open_site", "search_here", "search_newest", "open_first_result",
           "click_link", "click_control", "type_text", "press_key", "scroll", "wait")


async def _run_step(step: Step, config) -> StepResult:
    from .integrations import browser, desktop_apps, desktop_control

    action, arg = step.action, step.argument

    if action == "wait":
        await asyncio.sleep(min(5.0, float(arg or 1)))
        return StepResult(step, True, "waited")

    if action == "open_app":
        app = desktop_apps.resolve(arg)
        if app is None:
            return StepResult(step, False, f"there is no installed app called {arg}")
        if not desktop_apps.launch(app):
            return StepResult(step, False, f"{app.name} would not start")
        await asyncio.sleep(2.0)
        return StepResult(step, True, f"opened {app.name}")

    if action == "open_site":
        state = browser.ensure(browser.resolve_site(arg))
        if not state["ok"]:
            return StepResult(step, False, state["message"])
        result = await browser.open_site(arg)
        if not result.get("ok"):
            return StepResult(step, False, result.get("error") or f"could not open {arg}")
        return StepResult(step, True, result.get("message") or f"opened {arg}")

    if action in ("search_here", "search_newest"):
        newest = action == "search_newest"
        found = await browser.search_here(arg, newest=newest)
        if not found.get("ok"):
            return StepResult(step, False, f"there was nowhere to search for {arg} on this page")
        said = found.get("found") or ""
        # Ordered by date, the first result is not meant to be named after the search term — it
        # is the newest thing by them — so "I don't see iman ghadzi" was true and beside the point.
        if newest or said.startswith("I don't see"):
            said = f"searched for {arg}" + (", newest first" if newest else "")
        return StepResult(step, True, said)

    if action == "open_first_result":
        # Clicking text that matches the search term opens whatever is named that — on YouTube
        # that is the channel, not a video. The first item the results list actually offers is
        # the thing the search was ordered to put first.
        # A heavy results page takes longer to render than the default poll allows, and reading
        # it too early reported "nothing to open" for a search that had worked perfectly.
        items = [i for i in await browser.results_here(wait_s=12.0) if _is_a_title(i)]
        if not items:
            return StepResult(step, False, "the search returned nothing to open")
        # A page still settling can offer a stray label before the real ones; trying the next
        # candidate costs a second and turns a flaky failure into a result.
        tried = []
        for candidate in items[:3]:
            clicked = await browser.click_text(candidate)
            if clicked.get("ok") and clicked.get("changed") is not False:
                await asyncio.sleep(1.5)
                return StepResult(step, True, f"opened “{candidate}”")
            tried.append(candidate)
        return StepResult(step, False, f"I could not open “{tried[0]}”")

    if action == "click_link":
        result = await browser.click_text(arg)
        if not result.get("ok"):
            return StepResult(step, False, result.get("error") or f"nothing on the page said {arg}")
        if result.get("changed") is False:
            return StepResult(step, False, f"clicking {arg} changed nothing on the page")
        await asyncio.sleep(1.2)
        return StepResult(step, True, result.get("message") or f"clicked {arg}")

    if action == "click_control":
        said = await asyncio.to_thread(desktop_control.click_target, arg, "left", False, config)
        ok = said.startswith(("Used the", "Clicked"))
        return StepResult(step, ok, said if ok else said.rstrip("."))

    if action == "type_text":
        return StepResult(step, bool(await asyncio.to_thread(desktop_control.type_text, arg)),
                          f"typed {arg[:40]}")

    if action == "press_key":
        return StepResult(step, bool(await asyncio.to_thread(desktop_control.press_keys, arg)),
                          f"pressed {arg}")

    if action == "scroll":
        await browser.scroll(arg or "down")
        return StepResult(step, True, f"scrolled {arg or 'down'}")

    return StepResult(step, False, f"I have no way to {action}")


# Labels that are plainly not the name of anything: attribute values that leaked out of the
# markup, and fragments too short to identify a result.
_NOT_A_TITLE = re.compile(r"^(?:true|false|null|undefined|none|nan|\d+)$", re.IGNORECASE)


def _is_a_title(text: str) -> bool:
    text = (text or "").strip()
    return len(text) >= 4 and not _NOT_A_TITLE.match(text)


async def run(steps: list[Step], request: str, config) -> TaskReport:
    """Carry out the plan, stopping at the first step that does not do what it claims."""
    report = TaskReport(request=request)
    for step in steps[:MAX_STEPS]:
        try:
            result = await _run_step(step, config)
        except Exception as exc:  # noqa: BLE001 - one bad step must not lose the whole report
            result = StepResult(step, False, f"{step.action} raised {type(exc).__name__}")
        report.results.append(result)
        if not result.ok:
            return report
    report.finished = bool(report.results)
    return report


# --------------------------------------------------------------------------- planning
# "play the latest Iman Ghadzi video on YouTube", "open friends on Netflix", "search lofi on
# Spotify" — the same shape with different nouns, and the commonest thing anyone asks for.
_ON_SITE = re.compile(
    r"""^(?:please\s+)?
        (?:play|watch|open|put\s+on|find|search(?:\s+for)?|look\s+up|show\s+me)\s+
        (?P<what>.+?)
        \s+(?:on|in)\s+(?P<where>[\w .+-]{2,30})$""",
    re.IGNORECASE | re.VERBOSE,
)

# Words that describe which result to take rather than what to search for. Searching YouTube for
# "the latest Iman Ghadzi video" finds videos named that; searching for "Iman Ghadzi" finds him.
_QUALIFIER = re.compile(
    r"\b(?:the\s+)?(?:latest|newest|most\s+recent|last|new|first|top)\b|"
    r"\b(?:video|videos|song|songs|episode|track|movie|film)\b",
    re.IGNORECASE,
)


def _search_terms(what: str) -> str:
    """The words worth typing into a search box."""
    trimmed = _QUALIFIER.sub(" ", what)
    trimmed = re.sub(r"\b(?:a|an|the|of|by|from)\b", " ", trimmed, flags=re.IGNORECASE)
    trimmed = re.sub(r"['’]s\b", "", trimmed)
    return re.sub(r"\s{2,}", " ", trimmed).strip(" ,.") or what.strip()


def wants_newest(text: str) -> bool:
    return bool(re.search(r"\b(?:latest|newest|most\s+recent|last)\b", text or "", re.IGNORECASE))


def recipe(request: str) -> Optional[list[Step]]:
    """A plan for a request whose shape is already understood, or None to ask the model."""
    match = _ON_SITE.match((request or "").strip().rstrip(".!?"))
    if not match:
        return None
    where = match.group("where").strip()
    what = match.group("what").strip()
    terms = _search_terms(what)

    newest = wants_newest(request)
    steps = [
        Step("open_site", where, f"get to {where}"),
        Step("search_newest" if newest else "search_here", terms,
             f"search {where} for {terms}{' , newest first' if newest else ''}"),
    ]
    # Opening the first result is only right when the request named a thing to play or watch.
    # "search lofi on YouTube" asked to search, and stops at the results.
    if re.match(r"^(?:please\s+)?(?:play|watch|put\s+on|open|show\s+me)\b", request, re.IGNORECASE):
        steps.append(Step("open_first_result", "",
                          f"open the {'newest' if newest else 'first'} result"))
    return steps


_PLAN_INSTRUCTIONS = """Break the request into at most {max} steps.
Reply with JSON only: {{"steps": [{{"action": "...", "argument": "...", "purpose": "..."}}]}}

The only actions that exist:
  open_app <name>        launch an installed application
  open_site <name>       go to a website in the browser
  search_here <query>    use the search box of the site already open
  click_link <text>      click something on the web page by its visible text
  click_control <text>   click a button in a native app window by its label
  type_text <text>       type into whatever has focus
  press_key <key>        press a key, e.g. Return
  scroll <up|down>
  wait <seconds>

Use the fewest steps that do the job. Do not invent actions."""


def validate(raw: object, limit: int = MAX_STEPS) -> list[Step]:
    """Turn whatever the model replied with into steps, discarding anything it made up."""
    if isinstance(raw, str):
        text = raw.strip()
        fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        try:
            raw = json.loads(text)
        except ValueError:
            return []
    if isinstance(raw, dict):
        raw = raw.get("steps", [])
    if not isinstance(raw, list):
        return []
    steps: list[Step] = []
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip()
        if action not in ACTIONS:
            continue            # a model that invents an action does not get to run one
        steps.append(Step(action, str(item.get("argument", "")).strip(),
                          str(item.get("purpose", "")).strip()))
    return steps


async def plan(request: str, config) -> list[Step]:
    """Steps for this request: a recipe when the shape is known, else ask the model."""
    known = recipe(request)
    if known:
        return known

    from .agent.factory import make_agent

    agent = make_agent(config, mode="text")
    try:
        reply = await agent.send(
            f"{_PLAN_INSTRUCTIONS.format(max=MAX_STEPS)}\n\nRequest: {request}")
    except Exception:  # noqa: BLE001 - no plan is a clearer outcome than a broken one
        return []
    return validate(reply)


async def handle(text: str, config) -> Optional[str]:
    """Entry point for the deterministic command layer. None means 'not a multi-step task'."""
    steps = recipe((text or "").strip())
    if not steps or len(steps) < 2:
        return None
    report = await run(steps, text, config)
    return report.spoken()

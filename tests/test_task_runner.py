"""A multi-step request is planned into verifiable steps before anything runs."""
import pytest

from jarvis import task_runner as tr


def _plan(request):
    steps = tr.recipe(request)
    return [(s.action, s.argument) for s in steps] if steps else None


def test_the_example_that_prompted_this():
    assert _plan("play the latest iman ghadzi video on youtube") == [
        ("open_site", "youtube"),
        ("search_newest", "iman ghadzi"),
        ("open_first_result", ""),
    ]


@pytest.mark.parametrize("request_text,terms", [
    ("play the latest mrbeast video on youtube", "mrbeast"),
    ("watch the newest veritasium video on youtube", "veritasium"),
    ("play bohemian rhapsody on youtube", "bohemian rhapsody"),
    ("open friends on netflix", "friends"),
])
def test_what_gets_typed_into_the_search_box(request_text, terms):
    """"The latest X video" describes which result to take, not what to search for — searching
    for it finds videos titled that instead of the person who made them."""
    assert _plan(request_text)[1][1] == terms


def test_newest_is_asked_for_only_when_it_was_asked_for():
    assert _plan("play the latest x video on youtube")[1][0] == "search_newest"
    assert _plan("play bohemian rhapsody on youtube")[1][0] == "search_here"


def test_a_search_request_stops_at_the_results():
    """"search lofi on spotify" asked to search. Opening the first result would be an extra
    action nobody requested."""
    plan = _plan("search lofi on spotify")
    assert plan == [("open_site", "spotify"), ("search_here", "lofi")]


def test_a_single_action_is_not_a_task():
    for text in ("open netflix", "what is the volume", "turn the volume up", "hello"):
        assert tr.recipe(text) is None


# ------------------------------------------------------------------ plans from the model
def test_only_actions_that_exist_survive():
    """A model that invents an action does not get to run one."""
    steps = tr.validate({"steps": [
        {"action": "open_site", "argument": "youtube"},
        {"action": "hack_the_mainframe", "argument": "please"},
        {"action": "click_link", "argument": "Sign in"},
    ]})
    assert [s.action for s in steps] == ["open_site", "click_link"]


def test_a_fenced_json_reply_is_read():
    steps = tr.validate('```json\n{"steps": [{"action": "wait", "argument": "2"}]}\n```')
    assert [s.action for s in steps] == ["wait"]


def test_nonsense_yields_no_plan_rather_than_a_bad_one():
    assert tr.validate("I'm afraid I can't do that") == []
    assert tr.validate(None) == []


def test_a_plan_is_bounded():
    many = {"steps": [{"action": "wait", "argument": "1"} for _ in range(40)]}
    assert len(tr.validate(many)) <= tr.MAX_STEPS


# ------------------------------------------------------------------ reporting
def test_a_half_finished_task_says_where_it_stopped():
    report = tr.TaskReport(request="x")
    report.results = [
        tr.StepResult(tr.Step("open_site", "youtube", "get to youtube"), True, "Opened YouTube."),
        tr.StepResult(tr.Step("open_first_result", "", "open it"), False, "nothing to open"),
    ]
    said = report.spoken()
    assert "get to youtube" in said and "nothing to open" in said


def test_a_finished_task_reports_the_last_thing_it_did():
    report = tr.TaskReport(request="x", finished=True)
    report.results = [tr.StepResult(tr.Step("open_first_result", "", "open it"), True,
                                    "opened “Some Video”")]
    assert report.spoken() == "opened “Some Video”"


@pytest.mark.parametrize("label,ok", [
    ("true", False), ("42", False), ("ok", False), ("", False),
    ("Just A Breakup | Iman Gadzhi", True),
])
def test_stray_markup_values_are_not_treated_as_titles(label, ok):
    assert tr._is_a_title(label) is ok

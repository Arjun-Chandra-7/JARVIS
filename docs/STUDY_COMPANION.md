# JARVIS Study Companion

A source-grounded, personalised Class 10 learning system: it explains, answers doubts, checks work,
writes marks-based answers, runs quizzes, plans revision, and teaches from PDFs, the screen, videos
and the teacher. Code: `jarvis/study/`. Tests: `tests/test_study_{intent,sources,engines,learning,contracts}.py` (164, all synthetic, no network).

Branch `feat/jarvis-study-companion`, based on `73e01d8` (the tip of `live-failure-repair` before
the 3D Studio and Daily Brain branches were cut). **Wired in** on `feat/jarvis-integrated-v1`: the
gateway is `jarvis/brain/study_gateway.py` (the Daily Brain's capability router), the live glue is
`jarvis/study_live.py`, and the overlay has a Study tab (Alt+6) — see `docs/INTEGRATION.md`.

---

## Architecture

```
text / voice / capture / screen
        │
        ▼
commands.StudyCompanion.ask ──► session & privacy commands ("pause studying", "delete my study history")
        │                  └──► waiting quiz question? → quiz.QuizEngine.answer
        ▼
intent.extract  (rules only — no model)  →  types.StudyRequest
        │
        ├─ screen / video / "teacher just said" → context.decide → snapshot ingested as a cited source
        │                                        (personal memory OFF for this request)
        ▼
dispatch, in order of trust:
   1. the student's source ── retrieval (BM25 + floor) → grounded answer with citations
   2. deterministic engines ── math_engine (SymPy behind a whitelist), science_engine (units)
   3. verified offline cards ── knowledge.CARDS (NCERT-style, own words)
   4. the brain ────────────── gateway.StudyBrainGateway (typed request, labelled output)
   5. honest refusal ───────── "I can't answer this reliably offline…"
        │
        ▼
verify.check (citations, quotes, labels, misconceptions, scoring points, language)
scrub secrets from output → mastery / session / telemetry (safe fields only)
```

| Module | Responsibility |
|---|---|
| `types.py` | `StudyRequest`, `StudyResponse`, `SourceChunk`, `Citation`, `Claim`, `MarkScheme`, `Evaluation`, enums |
| `intent.py` | Deterministic extraction: task, mode, marks (English/Hindi/Hinglish numerals), language plan, source/screen/past references, privacy, missing info, confidence |
| `languages.py` | English/Hindi/Hinglish detection, split-language requests, glossary, script checks |
| `curriculum.py` | Boards → grades → chapters → sections/topics; objectives, prerequisites, formulae, misconceptions, question types, marking expectations; coverage labels |
| `sources.py` / `documents.py` | Ingestion with full provenance; PDF text layer (pypdf); OCR results with per-word confidence (engine is a protocol) |
| `retrieval.py` | Local lexical retrieval with a relevance floor; `supports`, `best_sentence` |
| `knowledge.py` | Verified topic cards for the tested slices |
| `answer_modes.py` | Understand / exam / revision / stepwise / hint ladder / line-by-line / compare / short |
| `rubrics.py` | Mark schemes, answer checking, mark bands |
| `math_engine.py` / `science_engine.py` | Deterministic, verified computation |
| `quiz.py` / `mastery.py` / `revision.py` / `session.py` | Learning loop |
| `visuals.py` | `TeachingVisualRequest` contract, builders, fake renderer, overlay adapter |
| `context.py` | Screen/selection/browser/video/PDF contract and decisions |
| `gateway.py` | The only door to a model |
| `privacy.py` / `telemetry.py` / `exports.py` / `verify.py` | Protections, safe metrics, exports, anti-hallucination gate |

## Daily Brain integration contract

The study package contains **no provider client** (a test parses every module's imports and fails on
`openai`, `anthropic`, `groq`, `google`, `ollama`, `httpx`, `requests`, `socket`, `subprocess`, …).
All model work is a `gateway.BrainRequest`:

| `BrainTask` | Used for | Capability hint |
|---|---|---|
| `explanation` | topics without a card, screen explanations | `reasoning` |
| `grounded_answer` | "according to this chapter" | `reasoning` |
| `answer_evaluation` | checking answers with no card | `reasoning` |
| `stepwise_solution` | non-deterministic solutions | `reasoning` |
| `quiz_generation` | extending a bank | `fast` |
| `hint_generation` | hints beyond the built-in ladders | `fast` |
| `summarization` | notes from supplied material | `long_context` |
| `multilingual_transformation` | Hindi/Hinglish exam answers | `multilingual` |

A request carries `language`, `privacy` (`public`/`personal`/`sensitive`), `must_include` (scoring
points), `sources` (rendered **fenced** as untrusted), `student_answer` (fenced), `marks`,
`max_words`, `recent` (to avoid repeated questions). `BrainRequest.prompt()` renders it.
`BrainReply` is `ok`, `text`, `route` (`local`/`cloud`), `reason`.

`GuardedGateway` wraps every gateway and (a) refuses personal/sensitive material on a cloud route
unless `PrivacyPolicy` allows it, (b) scrubs secrets, e-mails, phone numbers and home paths,
(c) turns exceptions into "no brain". The companion then says it can't answer reliably rather than
inventing.

**Adapter to write after Daily Brain lands** (≈30 lines, in the Daily Brain tree, not here):

```python
class DailyBrainStudyGateway:
    cloud = True                       # or read from the router: can this request leave the machine?
    def __init__(self, router): self.router = router
    def submit(self, req):
        route = self.router.pick(capability=req.capability, privacy=str(req.privacy))  # router honours privacy
        text = self.router.complete(route, prompt=req.prompt(), max_words=req.max_words)
        return BrainReply(True, text, route="local" if route.local else "cloud")
```

Routing: the Daily Brain's basic study detection should call `commands.looks_like_study(text)` and,
for a hit, hand the turn to a long-lived `StudyCompanion(gateway=DailyBrainStudyGateway(router),
data_dir=default_data_dir(), context_provider=<screen adapter>)`. Current-affairs questions in Social
Science belong to the Daily Brain research route, not here.

## Curriculum schema

`Board(id) → Grade(grade, academic_year) → Chapter(id, subject, title, number, book, area, aliases,
sections, topics, coverage, depth, numbering_verified, source_doc) → Topic(id, name, aliases,
objectives, prerequisites, formulae, misconceptions, question_types, marking)`. Misconceptions live in
one table (`curriculum.MISCONCEPTIONS`) with a label, a one-line correction and detection regexes.

What is actually installed (`REGISTRY.describe()`):

* **Deep (tested vertical slices):** Electricity; Light – Reflection and Refraction; The Human Eye;
  Acids, Bases and Salts; Metals and Non-metals; Life Processes; Control and Coordination; Triangles;
  Statistics; Quadratic Equations; Arithmetic Progressions; Coordinate Geometry.
* **Verified topic cards** (offline explanations + exam answers): current direction, Ohm's law,
  series/parallel, heating & power, refraction, ionic compounds, Pythagoras, cumulative frequency.
* **Outline only (title, coverage GENERAL):** 29 more chapters across Maths, Science, History,
  Geography, Political Science, Economics, English and Hindi.
* **Not installed:** the complete current CBSE syllabus. Chapter numbers follow the 2023–24
  rationalised NCERT order as recalled and are flagged `numbering_verified=False`; plans that use
  them say so.

Coverage labels: `official_source` (student supplied an official document), `general`
("NCERT-style"), `user_imported`, `unverified` (anything not in the registry).

## Sources and provenance

Each `SourceChunk` keeps: document id, safe title (never a path), page, section, chapter,
timestamp, source type, extraction method, language, OCR confidence and uncertain words, ingest time,
access class, content hash, char offsets, and a `suspicious` flag. Chunking keeps paragraphs whole
and starts a new chunk at each numbered heading so sections cite correctly.

Rules: no downloading or scraping books; nothing ingested is written into the repository;
quotations are capped at `MAX_EXCERPT` (240 chars); `SourceStore.save` writes 0600 files only where
the host says; `delete` removes a document and all its chunks.

## Grounded-answer policy

* "According to this page / use only this chapter / what does NCERT say / exact textbook answer"
  set `source_required`. With no source attached the companion **asks for one** and does not answer
  from memory (test: `test_exact_source_question_without_any_source_never_uses_memory`).
* Retrieval must cover ≥60% of the question's content words, and the answer sentence must match
  too; otherwise: "*<title>* doesn't say this — I searched every page I have of it."
* A model answer's sentences are split into claims: tagged `[chunk]` → `source` with the chunk's own
  locator; untagged → `general` and shown as "[general explanation]".
* `verify.check` rejects unknown chunks, locators that differ from the chunk's (no fake page
  numbers), quotes that are not verbatim, unsupported source claims, and "NCERT answer" labels
  without an official source.
* Without a source, card answers are labelled "NCERT-style … general Class 10 knowledge, not quoted
  from NCERT"; model answers are labelled "AI-written, not source-checked".

## Answer modes

Understand (intuition → example → exact definition → one check question; diagram if asked), Exam
(one numbered point per scoring idea, formula, no filler), Revision (essentials, formulae, mistakes,
likely questions, recall; hard line cap), Stepwise (given, required, formula, substitution,
calculation, units, final, check), Hint (ladder; next rung only on request; answer only on "show
the answer"), Line-by-line (short excerpt, meaning, key term), Compare (table, similarities,
conclusion), Short ("only the final answer"). A doubt stays a doubt: exam mode needs marks or an
explicit exam/board/formal request.

## Marks-based evaluation

`MarkScheme` = total, scoring points (keyword alternatives, marks, kind), optional points, formulae,
diagram, terminology, step marks, deductions, max length, `official` + citation. From a card the
scheme is general, and estimates read "likely 3/3 against an NCERT-style rubric, not an official
marking scheme". With no scheme: "cannot grade reliably without a marking scheme". Marks are whole
or half; uncertainty widens to a band ("approximately 2–3 marks"). A known misconception on a line
makes that line inaccurate and is recorded. OCR-uncertain words are never counted against the
student; a clarifying question is asked only when an uncertain word decides a point.

## Maths and science verification

Maths: a whitelist tokenizer (numbers, single-letter variables, operators, √, π, ², ³; powers ≤ 50;
160 chars) in front of SymPy with an empty namespace — `__import__`, attributes, `exp`, `eval`,
lambdas, indexing are refused. Exact surds and fractions (√800 = 20√2), Class 10 methods
(factorisation, discriminant, formula), every root substituted back, domain filters (positive
lengths) with typo warnings, `check_steps` to find the first line that changes the solution set and
name the misconception. Helpers: AP, distance/section formula, probability, grouped mean,
cumulative → class frequencies with a re-sum check, exact trig table, unit conversion.

Science: formula registry with symbol meanings and SI units; dimensional analysis on inputs and
result; prefix conversion shown (mm² → m², cm → m, min → s); refusal on unit mismatch; warnings for
missing units; significant figures from the data; substitution back into the equation.

## Language behaviour

The response follows the student's language: Hinglish in, Hinglish out (Latin script, English
science terms kept); Devanagari in, simple Hindi out; explicit requests win; "exam answer in English,
explain in Hinglish" gives two sections. The glossary gives Hindi term, Hinglish form, spoken
variants, pronunciation and ambiguity notes. The verifier flags Devanagari in a Hinglish answer and
heavily Sanskritised words. Hindi exam answers need the brain (translation); offline the companion
says the verified answer is in English only rather than machine-translating.

## Quiz and mastery

One question at a time; the prompt never contains the answer; MCQ / very short / short / numerical
(unit-aware) / assertion-reason / formula recall. Difficulty rises after two independent right
answers and falls after a miss; recently asked questions are skipped and repeats use alternative
wording. Wrong options map to misconceptions. Mastery per `chapter/topic`: seen, explained,
practised, correct alone, correct with hint, incorrect, repeated mistakes, last reviewed,
confidence, evidence, streak; estimate = (right + ½·hinted + 1)/(attempts + 2), labelled "a practice
indicator, not a formal assessment". Misconceptions are separate records. The student can view,
correct, reset a topic, delete everything, or turn personalisation off. No traits are inferred.

## Revision scheduling

Plans fit the stated time, put repeated mistakes and weak topics first, mix recall and practice,
use 5–15 minute blocks, add a break past 50 minutes, and end a "test tomorrow" plan with a formula
run and a stop. Spaced review intervals: 1, 2, 4, 7, 15, 30 days by streak. **No calendar events**:
`RevisionPlan.reminder_request` is a description for the existing approval manager to offer.

## Teaching-overlay contract

`TeachingVisualRequest(diagram, title, objects, steps, equations, language, speech_sync, hold_s,
dismiss_on, params)` with `VisualObject(id, kind, points, text, size, ends, role, style, dashed)` and
`VisualStep(id, narration, show, highlight, ms)` on a 1000×600 canvas. `validate` checks structure
and subject rules (ray diagrams: normal ⟂ boundary, rays joined, Snell's law angle, bending
direction, labels; circuits: conventional current leaves +; triangles: hypotenuse label).
Builders: ray diagram, circuit, right triangle, coordinate plane, number line, flow (biology/RAG),
table. `to_overlay_batch` converts to the overlay's own command batch and validates it with
`jarvis.teach.protocol.validate` — the shared overlay code is imported read-only, never edited.

After integration the voice path should send `to_overlay_batch(req, region=<overlay region>)`
through the existing teach bus, one step per spoken narration (`speech_sync`), and clear on
`dismiss_on` events.

## Screen, video and document context

`ContextProvider.snapshots() → [ContextSnapshot]` (selection, screen, browser, video with transcript
and position, teacher, pdf_page, image). `decide` makes the present-vs-past call, prefers
selection > PDF page > video/browser > screen, refuses stale (>120 s) or unreadable (<0.55 OCR)
context by asking for a capture, and disables personal memory for present-screen questions. Video
questions use a ±90 s transcript window and cite timestamps. The adapter to write wraps
`jarvis.screen_context` / `jarvis.screen.youtube`; nothing here duplicates them.

## Privacy

Local by default; `PrivacyPolicy` gates cloud use by classification. Logs and metrics pass through
`safe_event` (allow-listed fields, identifiers only — no sentences). Student answer text is never
stored; mastery and session files are 0600. Exports are Markdown/JSON, 0600, refused inside the
repository. Documents are data: text addressed to the assistant is flagged, fenced in every prompt,
recorded as a content-free audit event, and cannot reach a tool or setting — the package exposes no
such surface. Secrets are scrubbed from prompts **and** from any model output.

## Relationship to the existing "study mode"

The base already has `jarvis/modes/study.py` (focus mode: closes distracting apps/tabs, keeps
lectures open) and `jarvis/modes/study_chat.py` (opens a ChatGPT tab primed with
`exam_tutor_prompt.md`), wired through `jarvis/mode_command.py`, with tests in
`tests/test_study_and_coding_setup.py` and `tests/test_study_watching.py`. The companion does not
replace or touch them: study mode decides what the *machine* is for; the companion teaches.
Checked: none of the companion's session commands ("start a science study session", "pause
studying", "end the session", "continue from where we stopped") trigger `study.asked_to_start`/
`asked_to_stop`. Integration rule: starting a companion session must not switch focus mode on by
itself (closing tabs is an outward action); offer it, and use focus mode's own start path if accepted.
Once the companion is wired in, `study_chat`'s external ChatGPT tab becomes an optional fallback.

## Known limitations

* Connected (integrated build): voice/text through the Daily Brain, the real screen reader and YouTube
  captions (`study_live.LiveContextProvider`), the teach bus (`study_live.OverlayRenderer`, refuses lock/
  password/OTP screens), approvals for reminders. Live microphone use has not been soak-tested.
* Verified cards cover 8 topics; other topics need the brain (labelled) or the student's source.
* Retrieval is lexical; paraphrased questions against sources can miss (then it says so).
* Hindi exam answers depend on the brain; offline they are English only.
* Quiz bank is 16 synthetic Electricity questions; generation via the brain is typed but not built.
* Compare mode has no offline content.
* Chapter numbering is unverified; no official syllabus or marking scheme is bundled.
* The Study tab shows the session, the quiz question and practice estimates; there is no editor for
  mastery records beyond the spoken "reset"/"delete" commands.
* Live microphone/screen behaviour untested.

## Integration plan (done — kept for the record; see docs/INTEGRATION.md)

1. Finish and verify `feat/jarvis-daily-brain`; merge into a clean integration branch.
2. Rebase/merge `feat/jarvis-study-companion` onto it. Expected conflicts: `requirements.txt`
   (one appended line), `HANDOFF.md` (top section). The study code touches no shared module.
3. Implement `DailyBrainStudyGateway` over the capability router; construct one `StudyCompanion`
   per process with `data_dir=default_data_dir()`.
4. In the Daily Brain's study detection, call `looks_like_study` and route to `StudyCompanion.ask`;
   speak `StudyResponse.text()` sections; attach `follow_up` as the next prompt.
5. Implement a `ContextProvider` over `jarvis.screen_context` / `jarvis.screen.youtube`, and send
   `StudyResponse.visual` through `visuals.to_overlay_batch` to the teach bus.
6. Route `RevisionPlan.reminder_request` through `jarvis.approvals`.
7. Integrate the 3D branch; run the full suite once; live mic/screen tests; restart only from the
   final integrated checkout.

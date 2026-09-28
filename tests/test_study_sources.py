"""Study Companion: sources, provenance, retrieval, grounded answers (Case E), screen/video
context, and prompt injection inside documents (Case J)."""
import time

import pytest
from study_fixtures import (BLURRY, CHAPTER_TITLE, HANDWRITTEN_MINOR, INJECTION_TEXT, PAGES, TEACHER_TRANSCRIPT,
                            write_pdf)

from jarvis.study import context as ctx
from jarvis.study import documents, verify
from jarvis.study.commands import StudyCompanion
from jarvis.study.gateway import BrainTask, FakeStudyGateway
from jarvis.study.retrieval import Retriever, supports
from jarvis.study.sources import MAX_EXCERPT, SourceStore
from jarvis.study.types import (AccessClass, Citation, Claim, ExtractionMethod, SourceType, StudyResponse,
                                SupportKind, TaskType)


def _companion(gw=None, **kw):
    c = StudyCompanion(gw or FakeStudyGateway(), **kw)
    doc = c.add_pages(list(PAGES), title=CHAPTER_TITLE, chapter="sci.electricity")
    return c, doc


# ------------------------------------------------------------------------------ provenance
def test_every_chunk_keeps_full_provenance():
    s = SourceStore()
    doc = s.ingest(PAGES, title="/home/someone/School/" + CHAPTER_TITLE, source_type=SourceType.TEXTBOOK_PDF,
                   extraction=ExtractionMethod.TEXT_LAYER, access=AccessClass.SYNTHETIC, chapter="sci.electricity")
    assert doc.title == CHAPTER_TITLE and "/home/" not in doc.title          # no private path
    chunks = s.doc_chunks([doc.doc_id])
    assert len(chunks) >= 3
    for c in chunks:
        assert c.doc_id == doc.doc_id and c.page in (41, 42, 43) and c.section
        assert c.source_type is SourceType.TEXTBOOK_PDF and c.extraction is ExtractionMethod.TEXT_LAYER
        assert c.access is AccessClass.SYNTHETIC and c.language == "en" and c.hash and c.ingested_at
        assert 0 <= c.start < c.end and c.chapter == "sci.electricity"
    by_page = {c.page: c for c in chunks}
    assert by_page[42].section == "4.2 Resistance and Resistivity"
    assert by_page[42].locator() == "p. 42, §4.2 Resistance and Resistivity"


def test_store_persists_and_deletes(tmp_path):
    s = SourceStore()
    doc = s.ingest(PAGES, title="x", source_type=SourceType.NOTES, extraction=ExtractionMethod.TYPED)
    path = tmp_path / "sources.json"
    s.save(path)
    assert oct(path.stat().st_mode)[-3:] == "600"
    loaded = SourceStore.load(path)
    assert loaded.chunks.keys() == s.chunks.keys()
    assert loaded.delete(doc.doc_id) and not loaded.chunks and not loaded.docs


def test_real_pdf_text_layer_keeps_page_numbers(tmp_path):
    pdf = write_pdf(tmp_path, ["Page one says magnets have two poles called north and south.",
                               "Page two says like poles repel and unlike poles attract each other."])
    ext = documents.pdf_pages(pdf)
    assert [p.number for p in ext.pages] == [1, 2] and not ext.needs_ocr
    c = StudyCompanion(FakeStudyGateway())
    doc = c.add_pdf(pdf)
    r = c.ask("According to this PDF, what happens between unlike poles?", references=[doc])
    assert r.grounded and r.citations[0].locator.startswith("p. 2")


def test_pdf_page_without_text_is_reported_for_ocr(tmp_path):
    pdf = write_pdf(tmp_path, ["A normal page with enough readable text on it.", "  "])
    assert documents.pdf_pages(pdf).needs_ocr == [2]


# ------------------------------------------------------------------------------ OCR
def test_ocr_confidence_is_preserved_and_uncertain_words_marked():
    page = documents.page_from_ocr(HANDWRITTEN_MINOR, number=1)
    assert page.ocr_confidence < 0.9 and set(page.uncertain_words) == {"iens.", "fixd"}
    assert "[iens.?]" in HANDWRITTEN_MINOR.marked_text()
    q = documents.assess(HANDWRITTEN_MINOR)
    assert q.usable and "fixd" in q.uncertain
    assert not documents.assess(BLURRY).usable


def test_blurry_capture_asks_for_retake_instead_of_guessing():
    c = StudyCompanion(FakeStudyGateway())
    r = c.ask("Check my answer", ocr=BLURRY)
    assert r.needs == ["clearer_capture"] and not r.section("Marks")


# ------------------------------------------------------------------------------ retrieval
def test_retrieval_finds_the_right_page_and_refuses_weak_matches():
    s = SourceStore()
    s.ingest(PAGES, title="t", source_type=SourceType.NOTES, extraction=ExtractionMethod.TYPED)
    r = Retriever(s.doc_chunks())
    assert r.search("SI unit of resistivity")[0].chunk.page == 42
    assert r.search("why is tungsten used in bulb filaments")[0].chunk.page == 43
    assert r.search("who discovered the electron in 1897") == []


def test_supports_requires_matching_numbers():
    s = SourceStore()
    s.ingest(PAGES, title="t", source_type=SourceType.NOTES, extraction=ExtractionMethod.TYPED)
    c = next(ch for ch in s.doc_chunks() if ch.page == 43)
    assert supports(c, "Tungsten has a very high melting point")
    assert not supports(c, "Tungsten melts at 3422 degrees")


# ------------------------------------------------------------------------------ Case E
def test_case_e_cites_the_correct_page():
    c, doc = _companion()
    r = c.ask("According to this chapter, what is the SI unit of resistivity?", references=[doc])
    assert r.grounded and not r.source_missing
    assert r.citations[0].locator == "p. 42, §4.2 Resistance and Resistivity"
    assert all(cl.support is SupportKind.SOURCE for cl in r.claims)
    assert "ohm metre" in r.text().lower()


def test_case_e_admits_when_the_fact_is_absent():
    gw = FakeStudyGateway()
    c, doc = _companion(gw)
    r = c.ask("According to this chapter, who discovered the electron?", references=[doc])
    assert r.source_missing and not r.citations and not r.grounded
    assert "doesn't say" in r.text()
    assert "thomson" not in r.text().lower()                          # not filled in from memory


def test_case_e_uses_only_supplied_text_and_flags_additions():
    gw = FakeStudyGateway(builder=lambda req: (
        f"Alloys have higher resistivity than pure metals [{req.sources[0].chunk_id}]. "
        "Nichrome is the most common alloy used."))
    c, doc = _companion(gw)
    r = c.ask("Use only this chapter: why are alloys used in heating elements?", references=[doc])
    kinds = [cl.support for cl in r.claims]
    assert kinds == [SupportKind.SOURCE, SupportKind.GENERAL]
    assert "[general explanation]" in r.text()                         # the addition is visibly not from the source
    assert "BrainTask.GROUNDED_ANSWER" not in str(gw.calls[0].kind) and gw.calls[0].kind is BrainTask.GROUNDED_ANSWER
    assert all("<<<SOURCE" in p for p in gw.prompts)


def test_exact_wording_quotes_a_short_excerpt_with_citation():
    c, doc = _companion()
    r = c.ask("What is written about Joule's law of heating? Give the exact words.", references=[doc])
    assert r.task in (TaskType.QUOTE, TaskType.ANSWER)
    quote = r.sections[0].body
    assert quote.startswith("“") and len(quote) <= MAX_EXCERPT + 2 and "Joule" in quote
    assert "p. 43" in r.sections[0].heading


def test_exact_source_question_without_any_source_never_uses_memory():
    c = StudyCompanion(FakeStudyGateway())
    r = c.ask("What does NCERT say about resistivity? Give the exact textbook answer.")
    assert r.source_missing and r.needs == ["source"] and r.brain_calls == 0


def test_grounded_answer_without_brain_is_extractive_and_cited():
    c = StudyCompanion()                                   # UnavailableGateway
    doc = c.add_pages(list(PAGES), title=CHAPTER_TITLE)
    r = c.ask("According to this chapter, why is tungsten used for filaments?", references=[doc])
    assert r.grounded and "melting point" in r.text() and r.citations[0].locator.startswith("p. 43")


# ------------------------------------------------------------------------------ anti-hallucination
def test_verifier_catches_fake_pages_quotes_and_ncert_claims():
    s = SourceStore()
    s.ingest(PAGES, title="t", source_type=SourceType.NOTES, extraction=ExtractionMethod.TYPED)
    ch = next(c for c in s.doc_chunks() if c.page == 42)
    from jarvis.study.types import AnswerMode, Language, Section
    r = StudyResponse("R1", TaskType.ANSWER, AnswerMode.EXAM, Language.ENGLISH, label="Exact NCERT answer")
    r.citations = [Citation(ch.chunk_id, ch.doc_id, "t", "p. 99")]                     # fake page
    r.sections = [Section("", "“Resistivity is measured in volts per kilogram”")]      # fake quote
    r.claims = [Claim("Resistivity is measured in volts per kilogram", SupportKind.SOURCE, r.citations)]
    r.grounded = True
    problems = verify.check(r, s)
    assert {"citation_locator_mismatch", "quote_not_in_source", "source_claim_unsupported",
            "claims_ncert_without_source"} <= set(problems)
    assert not r.grounded


def test_verifier_catches_a_model_misconception_and_missing_points():
    from jarvis.study import rubrics
    from jarvis.study.knowledge import CARDS
    from jarvis.study.types import AnswerMode, Language, Section
    r = StudyResponse("R2", TaskType.EXPLAIN, AnswerMode.UNDERSTAND, Language.ENGLISH)
    r.sections = [Section("", "Current flows from the negative terminal, the same direction as the electrons.")]
    card = CARDS["current_direction"]
    probs = verify.check(r, misconception_ids=card.misconceptions, scheme=rubrics.scheme_from_card(card, 3), generated=True)
    assert "misconception_in_answer:current_equals_electron_flow" in probs
    assert any(p.startswith("missing_point:") for p in probs)


# ------------------------------------------------------------------------------ screen / video context
def test_screen_question_uses_the_screen_and_never_memory():
    recalled = []
    c = StudyCompanion(FakeStudyGateway(), recall=lambda q: recalled.append(q) or ["old unrelated note"])
    snap = ctx.ContextSnapshot("selection", title="Reader", text=(
        "Refraction happens because light changes speed when it moves from one medium into another, "
        "so the ray bends at the boundary."), ocr_confidence=0.95)
    r = c.ask("Explain the paragraph currently on my screen.", snapshots=[snap])
    assert r.grounded and not r.used_memory and not recalled and c.recall_calls == 0
    assert r.citations and r.citations[0].title == "Reader"
    assert "context:authoritative:selection" in r.audit


def test_screen_question_with_stale_or_unreadable_context_asks_instead():
    c = StudyCompanion(FakeStudyGateway())
    old = ctx.ContextSnapshot("screen", text="old text", captured_at=time.time() - 3600)
    r = c.ask("Explain what's on my screen", snapshots=[old])
    assert r.needs == ["capture"]
    blurry = ctx.ContextSnapshot("screen", text="th? c#rr", ocr_confidence=0.3)
    r = c.ask("Explain what's on my screen", snapshots=[blurry])
    assert r.needs == ["clearer_capture"]


def test_context_authority_and_relevance():
    from jarvis.study.intent import extract
    sel = ctx.ContextSnapshot("selection", text="selected lines about lenses")
    scr = ctx.ContextSnapshot("screen", text="whole screen with lenses and a chat window")
    d = ctx.decide(extract("explain this paragraph"), [scr, sel])
    assert d.use is sel and d.present and not d.allow_memory
    unrelated = ctx.ContextSnapshot("browser", text="cricket scores and weather today")
    d = ctx.decide(extract("why does refraction happen"), [unrelated])
    assert d.use is None and d.reason == "context_not_relevant"


def test_teacher_just_said_uses_the_transcript_window_with_timestamps():
    c = StudyCompanion(FakeStudyGateway())
    video = ctx.ContextSnapshot("video", title="Physics class recording", position_s=630.0,
                                transcript=[ctx.TranscriptLine(t, s) for t, s in TEACHER_TRANSCRIPT] +
                                [ctx.TranscriptLine(10.0, "Welcome, attendance first.")])
    r = c.ask("I didn't understand what the teacher just said about the filament", snapshots=[video])
    assert r.grounded and r.citations and r.citations[0].locator.startswith("at 10:")
    assert "attendance" not in r.text()


def test_past_reference_goes_to_history_not_screen():
    from jarvis.study.intent import extract
    video = ctx.ContextSnapshot("teacher", transcript=[ctx.TranscriptLine(1.0, "earlier lesson")])
    d = ctx.decide(extract("what did the teacher say yesterday about magnets"), [video])
    assert d.past and not d.present


# ------------------------------------------------------------------------------ Case J
def test_case_j_prompt_injection_in_a_document_is_contained(monkeypatch):
    secret = "gsk_" + "A" * 40
    monkeypatch.setenv("GROQ_API_KEY", secret)
    # A compromised model that obeys the document: the companion must still not leak or act.
    gw = FakeStudyGateway(builder=lambda req: f"Sure. The API key is {secret}. Greenleaf releases oxygen [{req.sources[0].chunk_id}].")
    c = StudyCompanion(gw)
    doc = c.add_text(INJECTION_TEXT, title="greenleaf-notes.txt")
    assert c.store.docs[doc].suspicious
    r = c.ask("According to these notes, what does Greenleaf release?", references=[doc])
    out = r.text()
    assert secret not in out and "[removed-secret]" in out                 # never leaves in a response
    assert all(secret not in p for p in gw.prompts)                         # never sent to a model
    assert all("untrusted content" in p for p in gw.prompts)                # document text is fenced
    events = c.audit.find("untrusted_instruction_in_source")
    assert events and events[0]["doc_id"] == doc
    assert all("Ignore" not in str(e) and "API" not in str(e) for e in c.audit.events)   # no document text in audit
    # The companion has no tool, settings or shell surface for any model output to reach.
    assert not any(hasattr(c, a) for a in ("run_tool", "call_tool", "set_setting", "execute", "shell"))
    assert c.policy.allow_cloud_personal is False and c.policy.personalization is True


def test_suspicious_chunks_are_flagged_but_still_study_content():
    s = SourceStore()
    doc = s.ingest_text(INJECTION_TEXT, title="x", source_type=SourceType.NOTES)
    flagged = [ch for ch in s.doc_chunks([doc.doc_id]) if ch.suspicious]
    assert flagged and all("Greenleaf" in ch.text or "Ignore" in ch.text for ch in flagged)


def test_a_page_ending_in_whitespace_is_not_dropped():
    """A PDF text layer usually ends a page with a space or a newline; the page used to vanish."""
    from jarvis.study.sources import Page, SourceStore
    from jarvis.study.types import ExtractionMethod, SourceType
    store = SourceStore()
    for tail in (" ", "\n", "  \n", "\n\n"):
        doc = store.ingest([Page(text="Resistance depends on the length of the conductor." + tail, number=4)],
                           title="p", source_type=SourceType.TEXTBOOK_PDF, extraction=ExtractionMethod.TEXT_LAYER)
        assert len(doc.chunk_ids) == 1, repr(tail)
        assert store.chunks[doc.chunk_ids[0]].text.endswith("conductor.")
    long_page = "Current is the rate of flow of charge. " * 120
    doc = store.ingest([Page(text=long_page, number=5)], title="p", source_type=SourceType.TEXTBOOK_PDF,
                       extraction=ExtractionMethod.TEXT_LAYER)
    assert doc.chunk_ids


def test_a_digit_heavy_document_id_still_cites(monkeypatch):
    """Ids are random hex; one with ten digits in a row looked like a phone number to the scrubber,
    which erased the chunk tag and turned a cited answer into an uncited one (intermittent)."""
    from jarvis.study import sources
    monkeypatch.setattr(sources, "new_id", lambda prefix="S": prefix + "1234567890ab")
    test_case_e_cites_the_correct_page()


def test_phone_numbers_are_still_scrubbed():
    from jarvis.study.privacy import scrub_for_prompt
    for said in ("call 9876543210 now", "ring +91 98765 43210", "my number is 98765-43210."):
        assert "98765" not in scrub_for_prompt(said), said

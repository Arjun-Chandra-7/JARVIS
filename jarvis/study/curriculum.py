"""The curriculum registry: boards, classes, subjects, chapters, topics — and how sure we are.

What is installed is a *representative* Class 10 CBSE/NCERT-compatible structure: chapter titles
for each subject, and deep entries (objectives, prerequisites, formulae, misconceptions, question
types, marking expectations) only for the chapters that have tested vertical slices. It is not
the complete current syllabus and nothing here says it is. Every entry carries a ``Coverage``:

* ``GENERAL``   — general Class 10 knowledge. Answers built on it are labelled "NCERT-style".
* ``OFFICIAL_SOURCE`` — only when the student supplied an official document for that chapter.
* ``USER_IMPORTED`` — a chapter the student added from their own material.
* ``UNVERIFIED`` — anything asked about that is not in the registry at all.

Chapter numbers follow the 2023–24 rationalised NCERT books as recalled, and are marked
``numbering_verified=False``: "revise chapters 1–9" uses them, and the plan says so.

The schema is not Class-10-only: a ``Board`` holds any number of ``Grade``s, and ``register``
adds chapters for any board, grade or subject.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .types import Coverage


@dataclass
class Misconception:
    id: str
    label: str                       # what the student believes / does
    correction: str                  # the one-line fix
    signals: tuple[str, ...] = ()    # regexes that suggest it in an answer


@dataclass
class Topic:
    id: str
    name: str
    aliases: tuple[str, ...] = ()
    objectives: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()        # topic ids
    formulae: tuple[str, ...] = ()             # science_engine / math_engine formula ids
    misconceptions: tuple[str, ...] = ()       # misconception ids
    question_types: tuple[str, ...] = ()
    marking: str = ""                          # what examiners typically expect (general, not official)


@dataclass
class Section:
    id: str
    title: str
    topics: tuple[str, ...] = ()


@dataclass
class Chapter:
    id: str
    subject: str                     # "physics", "mathematics", "history", …
    title: str
    number: Optional[int] = None
    book: str = ""
    area: str = ""                   # "science", "social_science", … (the exam paper it belongs to)
    aliases: tuple[str, ...] = ()
    sections: tuple[Section, ...] = ()
    topics: tuple[Topic, ...] = ()
    coverage: Coverage = Coverage.GENERAL
    depth: str = "outline"           # "outline" (title only) | "deep" (tested vertical slice)
    numbering_verified: bool = False
    source_doc: str = ""             # doc id of an official / imported source, when there is one

    def topic(self, topic_id: str) -> Optional[Topic]:
        return next((t for t in self.topics if t.id == topic_id), None)


@dataclass
class Grade:
    grade: int
    academic_year: str
    chapters: list[Chapter] = field(default_factory=list)


@dataclass
class Board:
    id: str
    name: str
    grades: dict[int, Grade] = field(default_factory=dict)


# Subjects and the exam paper they sit in. Science is taught as one paper with three strands.
SUBJECTS = {
    "mathematics": "mathematics",
    "physics": "science", "chemistry": "science", "biology": "science",
    "history": "social_science", "geography": "social_science",
    "political_science": "social_science", "economics": "social_science",
    "english": "english", "hindi": "hindi",
}
SUBJECT_ALIASES = {
    "mathematics": ("math", "maths", "mathematics", "ganit", "गणित"),
    "physics": ("physics",), "chemistry": ("chemistry",), "biology": ("biology", "bio"),
    "science": ("science", "vigyan", "विज्ञान"),
    "history": ("history", "itihas", "इतिहास"), "geography": ("geography", "bhugol", "भूगोल"),
    "political_science": ("political science", "civics", "polity", "pol sci"),
    "economics": ("economics", "arthshastra"),
    "social_science": ("social science", "sst", "social studies"),
    "english": ("english",), "hindi": ("hindi", "हिंदी", "हिन्दी"),
}

MISCONCEPTIONS: dict[str, Misconception] = {m.id: m for m in (
    Misconception("current_equals_electron_flow",
                  "Thinks conventional current flows the same way electrons move",
                  "Conventional current is taken from + to − outside the cell; electrons drift from − to +.",
                  (r"current\s+(?:flows?|goes)\s+(?:from\s+)?(?:the\s+)?negative", r"same\s+direction\s+as\s+(?:the\s+)?electrons")),
    Misconception("forgot_square_root",
                  "Forgets the square root when recovering a length from its square",
                  "If d² = k then d = √k, not k.",
                  (r"d\s*=\s*800\b",)),
    Misconception("cf_as_frequency",
                  "Uses cumulative frequency directly as the class frequency",
                  "Class frequency = this c.f. − previous c.f.",
                  ()),
    Misconception("omits_units",
                  "Leaves units off a numerical answer",
                  "Every physical quantity needs its SI unit in the final answer.",
                  ()),
    Misconception("series_parallel_swap",
                  "Adds resistances in parallel like series (or vice versa)",
                  "Series: R = R1 + R2; parallel: 1/R = 1/R1 + 1/R2.",
                  ()),
    Misconception("solid_ions_move",
                  "Thinks ions can move in a solid ionic compound",
                  "In the solid the ions are held in fixed positions in the lattice, so they cannot carry charge.",
                  (r"ions?\s+(?:can\s+)?move\s+(?:freely\s+)?in\s+(?:the\s+)?solid",)),
    Misconception("ionic_electrons_conduct",
                  "Credits free electrons (not ions) for conduction in molten ionic compounds",
                  "Molten ionic compounds conduct because their ions move; there are no free electrons as in metals.",
                  (r"(?:free|mobile)\s+electrons",)),
    Misconception("refraction_bends_away_denser",
                  "Says light bends away from the normal on entering a denser medium",
                  "Entering an optically denser medium, light slows and bends towards the normal.",
                  (r"away\s+from\s+(?:the\s+)?normal.*denser",)),
    Misconception("lens_sign_convention",
                  "Mixes up signs in the lens formula",
                  "Use the Cartesian convention: distances measured against incident light are negative.",
                  ()),
)}


def _t(id, name, aliases=(), **kw) -> Topic:
    return Topic(id=id, name=name, aliases=tuple(aliases), **kw)


# Deep chapters: the tested vertical slices.
_ELECTRICITY = Chapter(
    id="sci.electricity", subject="physics", title="Electricity", number=11, book="Science (NCERT-compatible)",
    area="science", aliases=("electricity", "bijli", "current electricity"), depth="deep",
    topics=(
        _t("current_direction", "Electric current and its direction",
           ("conventional current", "direction of current", "electron flow", "current ka direction", "current direction"),
           objectives=("Define electric current and its SI unit", "Distinguish conventional current from electron flow"),
           formulae=("I=Q/t",), misconceptions=("current_equals_electron_flow",),
           question_types=("definition", "reasoning", "numerical"),
           marking="Definition with unit; direction stated relative to electron flow."),
        _t("potential_difference", "Potential difference", ("voltage", "pd", "potential difference"),
           formulae=("V=W/Q",), prerequisites=("current_direction",), question_types=("definition", "numerical")),
        _t("ohms_law", "Ohm's law", ("ohm's law", "ohms law", "v=ir"),
           formulae=("V=IR",), prerequisites=("potential_difference",), misconceptions=("omits_units",),
           question_types=("statement", "graph", "numerical"),
           marking="Statement, V ∝ I at constant temperature, V = IR, V–I graph a straight line through origin."),
        _t("resistivity", "Resistance and resistivity", ("resistance", "resistivity"),
           formulae=("R=rho*l/A",), prerequisites=("ohms_law",), question_types=("reasoning", "numerical")),
        _t("combinations", "Resistors in series and parallel", ("series", "parallel", "combination of resistors"),
           formulae=("Rs=R1+R2", "1/Rp=1/R1+1/R2"), prerequisites=("ohms_law",),
           misconceptions=("series_parallel_swap", "omits_units"), question_types=("numerical", "circuit")),
        _t("heating_power", "Heating effect and electric power", ("heating effect", "joule heating", "electric power"),
           formulae=("H=I^2*R*t", "P=V*I"), prerequisites=("ohms_law",), misconceptions=("omits_units",),
           question_types=("numerical", "application")),
    ))
_LIGHT = Chapter(
    id="sci.light", subject="physics", title="Light – Reflection and Refraction", number=9,
    book="Science (NCERT-compatible)", area="science", depth="deep",
    aliases=("light", "reflection", "refraction", "lens", "mirror"),
    topics=(
        _t("refraction", "Refraction of light", ("refraction", "bending of light", "apvartan"),
           objectives=("Explain why light bends at a boundary", "Draw the refracted ray with normal and angles"),
           misconceptions=("refraction_bends_away_denser",), question_types=("diagram", "reasoning"),
           marking="Ray diagram with normal, i and r marked, arrows on rays; bending towards normal in denser medium."),
        _t("refractive_index", "Refractive index", ("refractive index",), formulae=("n=c/v",),
           prerequisites=("refraction",), question_types=("numerical", "reasoning")),
        _t("lens_formula", "Lens formula and magnification", ("lens formula", "magnification"),
           formulae=("1/v-1/u=1/f", "m=v/u", "P=1/f"), misconceptions=("lens_sign_convention",),
           question_types=("numerical", "diagram")),
    ))
_EYE = Chapter(
    id="sci.eye", subject="physics", title="The Human Eye and the Colourful World", number=10,
    book="Science (NCERT-compatible)", area="science", depth="deep",
    aliases=("human eye", "eye", "myopia", "hypermetropia", "dispersion"),
    topics=(
        _t("defects", "Defects of vision and correction", ("myopia", "hypermetropia", "presbyopia"),
           question_types=("diagram", "reasoning")),
        _t("dispersion", "Dispersion of white light", ("dispersion", "prism", "spectrum", "rainbow"),
           question_types=("reasoning", "diagram")),
    ))
_ACIDS = Chapter(
    id="sci.acids", subject="chemistry", title="Acids, Bases and Salts", number=2, book="Science (NCERT-compatible)",
    area="science", depth="deep", aliases=("acids", "bases", "salts", "ph"),
    topics=(
        _t("ph", "The pH scale", ("ph", "ph scale"), question_types=("reasoning", "application")),
        _t("neutralisation", "Neutralisation", ("neutralisation", "neutralization"), question_types=("equation",)),
    ))
_METALS = Chapter(
    id="sci.metals", subject="chemistry", title="Metals and Non-metals", number=3, book="Science (NCERT-compatible)",
    area="science", depth="deep", aliases=("metals", "non-metals", "ionic compounds"),
    topics=(
        _t("ionic_properties", "Properties of ionic compounds",
           ("ionic compounds", "ionic compound conduct", "molten ionic", "conduct electricity when molten"),
           objectives=("Explain conduction of ionic compounds in molten and aqueous states",),
           misconceptions=("solid_ions_move", "ionic_electrons_conduct"), question_types=("reasoning",),
           marking="Made of ions; ions fixed in solid lattice; ions free to move when molten/in solution."),
        _t("reactivity", "Reactivity series", ("reactivity series",), question_types=("equation", "reasoning")),
    ))
_LIFE = Chapter(
    id="sci.life", subject="biology", title="Life Processes", number=5, book="Science (NCERT-compatible)",
    area="science", depth="deep", aliases=("life processes", "nutrition", "respiration", "photosynthesis"),
    topics=(
        _t("photosynthesis", "Photosynthesis", ("photosynthesis",), question_types=("equation", "diagram")),
        _t("respiration", "Respiration", ("respiration", "aerobic", "anaerobic"), question_types=("flowchart", "compare")),
    ))
_CONTROL = Chapter(
    id="sci.control", subject="biology", title="Control and Coordination", number=6, book="Science (NCERT-compatible)",
    area="science", depth="deep", aliases=("control and coordination", "reflex", "hormones", "nervous system"),
    topics=(
        _t("reflex_arc", "Reflex action and reflex arc", ("reflex arc", "reflex action"),
           question_types=("flowchart", "diagram")),
        _t("hormones", "Hormones in animals", ("hormones", "endocrine"), question_types=("table", "reasoning")),
    ))
_TRIANGLES = Chapter(
    id="math.triangles", subject="mathematics", title="Triangles", number=6, book="Mathematics (NCERT-compatible)",
    area="mathematics", depth="deep", aliases=("triangles", "pythagoras", "similar triangles"),
    topics=(
        _t("pythagoras", "Pythagoras theorem and its uses", ("pythagoras", "diagonal", "hypotenuse"),
           formulae=("a^2+b^2=c^2",), misconceptions=("forgot_square_root",),
           question_types=("numerical", "proof"), marking="Theorem stated, substitution, square root, units."),
    ))
_STATS = Chapter(
    id="math.statistics", subject="mathematics", title="Statistics", number=13, book="Mathematics (NCERT-compatible)",
    area="mathematics", depth="deep", aliases=("statistics", "mean", "median", "mode", "ogive"),
    topics=(
        _t("cumulative_frequency", "Cumulative frequency", ("cumulative frequency", "c.f.", "cf table", "less than type"),
           misconceptions=("cf_as_frequency",), question_types=("table", "median"),
           marking="Recover class frequencies by subtraction before using a formula."),
        _t("median_grouped", "Median of grouped data", ("median",), prerequisites=("cumulative_frequency",),
           formulae=("l+((n/2-cf)/f)*h",), question_types=("numerical",)),
    ))
_QUAD = Chapter(
    id="math.quadratic", subject="mathematics", title="Quadratic Equations", number=4,
    book="Mathematics (NCERT-compatible)", area="mathematics", depth="deep",
    aliases=("quadratic", "quadratic equations", "discriminant"),
    topics=(_t("roots", "Roots and the discriminant", ("roots", "discriminant", "quadratic formula"),
               question_types=("numerical", "nature of roots")),))
_AP = Chapter(
    id="math.ap", subject="mathematics", title="Arithmetic Progressions", number=5,
    book="Mathematics (NCERT-compatible)", area="mathematics", depth="deep",
    aliases=("arithmetic progression", "ap", "a.p."),
    topics=(_t("nth_term", "nth term and sum", ("nth term", "sum of n terms"), question_types=("numerical",)),))
_COORD = Chapter(
    id="math.coordinate", subject="mathematics", title="Coordinate Geometry", number=7,
    book="Mathematics (NCERT-compatible)", area="mathematics", depth="deep",
    aliases=("coordinate geometry", "distance formula", "section formula"),
    topics=(_t("distance", "Distance and section formulae", ("distance formula", "section formula", "midpoint"),
               question_types=("numerical",)),))

# Outline chapters: title and coverage only. Honest about being thin.
_OUTLINE = [
    ("sci.reactions", "chemistry", "Chemical Reactions and Equations", 1, "science"),
    ("sci.carbon", "chemistry", "Carbon and its Compounds", 4, "science"),
    ("sci.reproduce", "biology", "How do Organisms Reproduce?", 7, "science"),
    ("sci.heredity", "biology", "Heredity", 8, "science"),
    ("sci.magnetic", "physics", "Magnetic Effects of Electric Current", 12, "science"),
    ("sci.environment", "biology", "Our Environment", 13, "science"),
    ("math.real", "mathematics", "Real Numbers", 1, "mathematics"),
    ("math.polynomials", "mathematics", "Polynomials", 2, "mathematics"),
    ("math.linear", "mathematics", "Pair of Linear Equations in Two Variables", 3, "mathematics"),
    ("math.trig", "mathematics", "Introduction to Trigonometry", 8, "mathematics"),
    ("math.trig_apps", "mathematics", "Some Applications of Trigonometry", 9, "mathematics"),
    ("math.circles", "mathematics", "Circles", 10, "mathematics"),
    ("math.areas", "mathematics", "Areas Related to Circles", 11, "mathematics"),
    ("math.surface", "mathematics", "Surface Areas and Volumes", 12, "mathematics"),
    ("math.probability", "mathematics", "Probability", 14, "mathematics"),
    ("hist.europe", "history", "The Rise of Nationalism in Europe", None, "social_science"),
    ("hist.india", "history", "Nationalism in India", None, "social_science"),
    ("geo.resources", "geography", "Resources and Development", None, "social_science"),
    ("geo.water", "geography", "Water Resources", None, "social_science"),
    ("pol.power_sharing", "political_science", "Power-sharing", None, "social_science"),
    ("pol.federalism", "political_science", "Federalism", None, "social_science"),
    ("eco.development", "economics", "Development", None, "social_science"),
    ("eco.sectors", "economics", "Sectors of the Indian Economy", None, "social_science"),
    ("eng.letter_to_god", "english", "A Letter to God", None, "english"),
    ("eng.mandela", "english", "Nelson Mandela: Long Walk to Freedom", None, "english"),
    ("eng.dust_of_snow", "english", "Dust of Snow", None, "english"),
    ("eng.writing", "english", "Writing formats (letters, analytical paragraph)", None, "english"),
    ("hin.vakya_bhed", "hindi", "रचना के आधार पर वाक्य भेद", None, "hindi"),
    ("hin.anuchhed", "hindi", "अनुच्छेद लेखन", None, "hindi"),
]


class Registry:
    def __init__(self) -> None:
        self.boards: dict[str, Board] = {}

    # ----------------------------------------------------------------------- building
    def register(self, chapter: Chapter, board: str = "CBSE", grade: int = 10, year: str = "2025-26") -> Chapter:
        b = self.boards.setdefault(board, Board(board, board))
        g = b.grades.setdefault(grade, Grade(grade, year))
        g.chapters = [c for c in g.chapters if c.id != chapter.id] + [chapter]
        return chapter

    def import_chapter(self, title: str, subject: str, doc_id: str, board: str = "CBSE", grade: int = 10,
                       official: bool = False) -> Chapter:
        """A chapter the student brought. Coverage says so; it is never mistaken for installed."""
        cid = "user." + re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40]
        existing = self.chapter(cid, board, grade)
        topics = existing.topics if existing else ()
        ch = Chapter(id=cid, subject=subject, title=title, area=SUBJECTS.get(subject, subject),
                     coverage=Coverage.OFFICIAL_SOURCE if official else Coverage.USER_IMPORTED,
                     source_doc=doc_id, topics=topics, aliases=(title.lower(),))
        return self.register(ch, board, grade)

    # ----------------------------------------------------------------------- lookup
    def chapters(self, board: str = "CBSE", grade: int = 10, subject: str = "") -> list[Chapter]:
        g = self.boards.get(board, Board(board, board)).grades.get(grade)
        if not g:
            return []
        out = g.chapters
        if subject:
            want = {subject} | ({s for s, a in SUBJECTS.items() if a == subject})
            out = [c for c in out if c.subject in want or c.area == subject]
        return out

    def chapter(self, chapter_id: str, board: str = "CBSE", grade: int = 10) -> Optional[Chapter]:
        return next((c for c in self.chapters(board, grade) if c.id == chapter_id), None)

    def by_number(self, numbers: Iterable[int], subject: str, board: str = "CBSE", grade: int = 10) -> list[Chapter]:
        ns = set(numbers)
        return sorted((c for c in self.chapters(board, grade, subject) if c.number in ns), key=lambda c: c.number)

    def find(self, text: str, board: str = "CBSE", grade: int = 10) -> tuple[Optional[Chapter], Optional[Topic]]:
        """The chapter and topic a request is about, by alias. Topic aliases outrank chapter
        aliases; longer matches outrank shorter ones ("conventional current" over "current")."""
        low = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
        best: tuple[int, Optional[Chapter], Optional[Topic]] = (0, None, None)
        for ch in self.chapters(board, grade):
            for tp in ch.topics:
                for a in (tp.name.lower(),) + tp.aliases:
                    if _has(low, a) and len(a) + 100 > best[0]:
                        best = (len(a) + 100, ch, tp)
            for a in (ch.title.lower(),) + ch.aliases:
                if _has(low, a) and len(a) > best[0]:
                    best = (len(a), ch, None)
        return best[1], best[2]

    def coverage_of(self, text: str, board: str = "CBSE", grade: int = 10) -> Coverage:
        ch, _ = self.find(text, board, grade)
        return ch.coverage if ch else Coverage.UNVERIFIED

    def describe(self, board: str = "CBSE", grade: int = 10) -> dict:
        """What is actually installed — for "what can you teach me?" and the docs."""
        out: dict[str, dict] = {}
        for c in self.chapters(board, grade):
            s = out.setdefault(c.subject, {"deep": [], "outline": [], "imported": []})
            key = "imported" if c.coverage in (Coverage.USER_IMPORTED, Coverage.OFFICIAL_SOURCE) else c.depth
            s[key].append(c.title)
        return out


def _has(haystack: str, needle: str) -> bool:
    return bool(needle) and re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack) is not None


def subject_of(text: str) -> str:
    low = " " + (text or "").lower() + " "
    for subj, names in SUBJECT_ALIASES.items():
        if any(_has(low, n) for n in names):
            return subj
    return ""


def default_registry() -> Registry:
    r = Registry()
    for ch in (_ELECTRICITY, _LIGHT, _EYE, _ACIDS, _METALS, _LIFE, _CONTROL,
               _TRIANGLES, _STATS, _QUAD, _AP, _COORD):
        r.register(ch)
    for cid, subj, title, num, area in _OUTLINE:
        r.register(Chapter(id=cid, subject=subj, title=title, number=num, area=area,
                           aliases=(title.lower(),)))
    return r


REGISTRY = default_registry()

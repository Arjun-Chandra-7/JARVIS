"""Deterministic Class 10 mathematics, behind a restricted interface.

Nothing the student types is ever executed. An expression is first tokenised against a small
whitelist — numbers, single-letter variables, + − × ÷ ^ ( ) =, √/sqrt, π, ² and ³ — and only a
string made entirely of those tokens reaches SymPy's parser, with a global namespace that holds
nothing but the few constructors the parser needs. Anything else is a ``MathError``, reported to
the student as "I can't read that expression", never guessed at.

Answers keep exact forms (20√2, 3/4) alongside decimals; solutions are substituted back; domain
restrictions (a length is positive, a probability is in [0, 1]) filter roots and flag questions
that are probably mistyped. Methods are the Class 10 ones: factorisation or the quadratic
formula, not calculus.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional, Sequence

import sympy as sp
from sympy.parsing.sympy_parser import (convert_xor, implicit_multiplication_application, parse_expr,
                                        standard_transformations)


class MathError(ValueError):
    pass


MAX_LEN = 160
MAX_POWER = 50
_TOKEN = re.compile(r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<fn>sqrt|√)|(?P<pi>pi|π)|(?P<var>[a-z])(?![a-z])|"
                    r"(?P<op>\*\*|[-+*/^()=×÷·])|(?P<sup>[²³]))", re.I)
_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)
_GLOBALS = {"Integer": sp.Integer, "Float": sp.Float, "Rational": sp.Rational, "Symbol": sp.Symbol,
            "sqrt": sp.sqrt, "pi": sp.pi, "__builtins__": {}}


def _normalise(text: str) -> str:
    """Tokenise against the whitelist and rebuild a SymPy-safe string, or refuse."""
    text = (text or "").strip().rstrip(".")
    if not text or len(text) > MAX_LEN:
        raise MathError("empty_or_too_long")
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip() == "":
                break
            raise MathError(f"unsupported:{text[pos:pos + 12]!r}")
        pos = m.end()
        if m.group("num"):
            out.append(m.group("num"))
        elif m.group("fn"):
            out.append("sqrt")
        elif m.group("pi"):
            out.append("pi")
        elif m.group("var"):
            out.append(m.group("var").lower())
        elif m.group("sup"):
            out.append("**2" if m.group("sup") == "²" else "**3")
        else:
            out.append({"×": "*", "·": "*", "÷": "/", "^": "**"}.get(m.group("op"), m.group("op")))
    s = " ".join(out)
    # "√ 800" → "sqrt(800)" when the root has no bracket of its own.
    s = re.sub(r"sqrt\s+(\d+(?:\.\d+)?|[a-z])", r"sqrt(\1)", s)
    for p in re.findall(r"\*\*\s*\(?\s*(\d+)", s):
        if int(p) > MAX_POWER:
            raise MathError("power_too_large")
    return s


def parse(text: str) -> sp.Expr:
    s = _normalise(text)
    if "=" in s:
        raise MathError("expected_expression_not_equation")
    try:
        return parse_expr(s, local_dict={}, global_dict=dict(_GLOBALS), transformations=_TRANSFORMS, evaluate=True)
    except Exception as e:  # noqa: BLE001 — any parser failure is simply "can't read that"
        raise MathError("parse_failed") from e


def parse_equation(text: str) -> sp.Eq:
    s = _normalise(text)
    if s.count("=") != 1:
        raise MathError("expected_one_equals_sign")
    lhs, rhs = s.split("=")
    try:
        L = parse_expr(lhs, local_dict={}, global_dict=dict(_GLOBALS), transformations=_TRANSFORMS)
        R = parse_expr(rhs, local_dict={}, global_dict=dict(_GLOBALS), transformations=_TRANSFORMS)
    except Exception as e:  # noqa: BLE001
        raise MathError("parse_failed") from e
    return sp.Eq(L, R, evaluate=False)


# ------------------------------------------------------------------------------------ values
@dataclass
class Value:
    exact: sp.Expr

    @property
    def is_exact_rational(self) -> bool:
        return bool(self.exact.is_Rational)

    def pretty(self) -> str:
        """Class 10 notation: 20√2, 3/4, 5 + 2√3."""
        s = sp.sstr(sp.nsimplify(self.exact) if self.exact.is_number else self.exact)
        s = re.sub(r"sqrt\((\d+)\)", r"√\1", s)
        s = re.sub(r"(\d)\*√", r"\1√", s)
        return s.replace("**", "^").replace("*", "×")

    def decimal(self, places: int = 2) -> str:
        v = float(sp.N(self.exact, 30))
        return f"{v:.{places}f}".rstrip("0").rstrip(".") if places else str(round(v))

    def __str__(self) -> str:
        p = self.pretty()
        d = self.decimal(2) if self.exact.is_number else ""
        return p if (not d or self.is_exact_rational and "/" not in p) else f"{p} ≈ {d}"


def evaluate(text: str) -> Value:
    e = parse(text)
    if e.free_symbols:
        raise MathError("has_variables")
    return Value(sp.nsimplify(sp.simplify(e)))


def simplify_root(n: int) -> Value:
    """√n in simplest surd form: √800 = 20√2."""
    if n < 0:
        raise MathError("negative_under_root")
    return Value(sp.sqrt(sp.Integer(n)))


# ------------------------------------------------------------------------------------ solving
@dataclass
class Solution:
    question: str
    given: list[str] = field(default_factory=list)
    required: str = ""
    method: str = ""
    formula: str = ""
    steps: list[str] = field(default_factory=list)
    answers: list[Value] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    unit: str = ""
    verification: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def final(self) -> str:
        if not self.answers:
            return "No valid answer"
        vals = ", ".join(str(a) for a in self.answers)
        return f"{vals}{(' ' + self.unit) if self.unit else ''}"


def solve(equation: str, var: str = "", *, positive: bool = False, integer: bool = False, unit: str = "") -> Solution:
    """Solve a linear or quadratic equation the Class 10 way, check each root, apply the domain."""
    eq = parse_equation(equation)
    expr = sp.expand(eq.lhs - eq.rhs)
    syms = sorted(expr.free_symbols, key=str)
    if not syms:
        raise MathError("no_variable")
    x = sp.Symbol(var) if var else syms[0]
    if len(syms) > 1:
        raise MathError("more_than_one_variable")
    sol = Solution(question=equation, given=[f"{sp.sstr(eq.lhs)} = {sp.sstr(eq.rhs)}"], required=f"{x}", unit=unit)
    poly = sp.Poly(expr, x)
    deg = poly.degree()
    if deg == 1:
        a, b = poly.all_coeffs()
        sol.method = "linear"
        sol.steps = [f"Bring terms together: {sp.sstr(a)}{x} + ({sp.sstr(b)}) = 0",
                     f"{x} = {sp.sstr(-b)}/{sp.sstr(a)}"]
        roots = [sp.nsimplify(-b / a)]
    elif deg == 2:
        a, b, c = poly.all_coeffs()
        D = sp.simplify(b ** 2 - 4 * a * c)
        sol.method = "quadratic"
        sol.formula = f"{x} = (−b ± √D)/2a, D = b² − 4ac"
        sol.steps.append(f"Standard form: {sp.sstr(a)}{x}² + ({sp.sstr(b)}){x} + ({sp.sstr(c)}) = 0")
        sol.steps.append(f"D = ({sp.sstr(b)})² − 4({sp.sstr(a)})({sp.sstr(c)}) = {sp.sstr(D)}")
        if D < 0:
            sol.steps.append("D < 0, so there are no real roots.")
            roots = []
        else:
            factored = sp.factor(expr)
            if factored != expr and D.is_Rational and sp.sqrt(D).is_Rational:
                sol.steps.append(f"Factorise: {sp.sstr(factored)} = 0")
            roots = sorted(set(sp.nsimplify(r) for r in sp.solve(expr, x)), key=lambda r: float(r))
            nature = "two equal real roots" if D == 0 else "two distinct real roots"
            sol.steps.append(f"D {'= 0' if D == 0 else '> 0'}, so {nature}.")
    else:
        raise MathError("degree_not_supported")
    for r in roots:
        ok = sp.simplify(expr.subs(x, r)) == 0
        sol.verification.append(f"{x} = {Value(r).pretty()}: substituting gives {'0 ✓' if ok else 'not 0 ✗'}")
        if not ok:
            continue
        if positive and not (r > 0):
            sol.rejected.append(f"{Value(r).pretty()} (must be positive)")
            continue
        if integer and not r.is_integer:
            sol.rejected.append(f"{Value(r).pretty()} (must be a whole number)")
            continue
        sol.answers.append(Value(r))
    if roots and not sol.answers:
        sol.warnings.append("possible_typo: no root satisfies the conditions of the question")
    if deg == 2 and not roots and positive:
        sol.warnings.append("possible_typo: a length/count equation with no real roots")
    return sol


def length_from_square(square: str, name: str = "d", unit: str = "") -> Solution:
    """"d² = 800" → d = √800 = 20√2 — the step students skip."""
    v = evaluate(square)
    if v.exact < 0:
        raise MathError("negative_square")
    root = Value(sp.sqrt(v.exact))
    sol = Solution(question=f"{name}² = {v.pretty()}", given=[f"{name}² = {v.pretty()}"], required=name,
                   method="square_root", formula=f"{name} = √({name}²), taking the positive root for a length", unit=unit)
    sol.steps = [f"{name} = √{v.pretty()}"]
    if not root.is_exact_rational:
        k = sp.sqrt(v.exact)
        coeff, rest = k.as_coeff_Mul()
        if coeff != 1:
            sol.steps.append(f"√{v.pretty()} = √({sp.sstr(coeff ** 2)} × {sp.sstr(rest ** 2)}) = {root.pretty()}")
    sol.answers = [root]
    sol.verification.append(f"({root.pretty()})² = {Value(sp.expand(root.exact ** 2)).pretty()} ✓")
    return sol


# ------------------------------------------------------------------------------------ checking work
@dataclass
class StepCheck:
    ok: bool
    wrong_line: Optional[int] = None
    expected: str = ""
    got: str = ""
    misconception: str = ""
    explanation: str = ""


_LINE_EQ = re.compile(r"^\s*(?:=>|⇒|∴|so,?|therefore,?|hence,?)?\s*(?P<eq>[^=]+=[^=]+)$", re.I)


def check_steps(lines: Sequence[str]) -> StepCheck:
    """Find the first line whose equation does not follow from the one before it.

    Each line is an equation in one variable. A line "follows" if it has the same admissible
    solution set (positive roots, when the variable is a length — the caller's problem decides,
    we take positive whenever both sides are non-negative numbers, the usual Class 10 case)."""
    prev_set, prev_eq = None, None
    for i, raw in enumerate(lines, start=1):
        m = _LINE_EQ.match(raw.strip())
        if not m:
            continue
        try:
            eq = parse_equation(m.group("eq"))
        except MathError:
            continue
        expr = sp.expand(eq.lhs - eq.rhs)
        syms = list(expr.free_symbols)
        if len(syms) != 1:
            continue
        x = syms[0]
        roots = {sp.nsimplify(r) for r in sp.solve(expr, x) if r.is_real and r > 0}
        if prev_set is not None and roots != prev_set:
            chk = StepCheck(False, i, _fmt_set(x, prev_set), _fmt_set(x, roots))
            # The signature mistake: x² = k written as x = k.
            pe = sp.expand(prev_eq.lhs - prev_eq.rhs)
            if sp.Poly(pe, x).degree() == 2 and len(roots) == 1:
                (r,) = roots
                if any(sp.simplify(r - rr ** 2) == 0 for rr in prev_set):
                    chk.misconception = "forgot_square_root"
                    k = next(iter(prev_set))
                    chk.explanation = (f"Line {i} drops the square root: from {x}² = {Value(r).pretty()} it should be "
                                       f"{x} = √{Value(r).pretty()} = {Value(k).pretty()}, not {Value(r).pretty()}.")
            if not chk.explanation:
                chk.explanation = f"Line {i} changes the answer: before it {chk.expected}, after it {chk.got}."
            return chk
        prev_set, prev_eq = roots, eq
    return StepCheck(True)


def _fmt_set(x, s) -> str:
    return ", ".join(f"{x} = {Value(r).pretty()}" for r in sorted(s, key=float)) or f"no positive {x}"


# ------------------------------------------------------------------------------------ topics
def ap_nth(a: Fraction, d: Fraction, n: int) -> Fraction:
    if n < 1:
        raise MathError("n_must_be_positive")
    return Fraction(a) + (n - 1) * Fraction(d)


def ap_sum(a: Fraction, d: Fraction, n: int) -> Fraction:
    if n < 1:
        raise MathError("n_must_be_positive")
    return Fraction(n, 2) * (2 * Fraction(a) + (n - 1) * Fraction(d))


def distance(p: tuple, q: tuple) -> Value:
    return Value(sp.sqrt(sp.nsimplify((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2)))


def section_point(p: tuple, q: tuple, m: int, n: int) -> tuple[Fraction, Fraction]:
    if m + n == 0:
        raise MathError("ratio_sum_zero")
    return (Fraction(m * q[0] + n * p[0], m + n), Fraction(m * q[1] + n * p[1], m + n))


def probability(favourable: int, total: int) -> Fraction:
    if total <= 0 or favourable < 0:
        raise MathError("invalid_counts")
    if favourable > total:
        raise MathError("possible_typo: favourable outcomes exceed total")
    return Fraction(favourable, total)


def is_cumulative(values: Sequence[float]) -> bool:
    """A cumulative (less-than) column never decreases, and its last entry is the total."""
    return len(values) >= 2 and all(b >= a for a, b in zip(values, values[1:]))


@dataclass
class FrequencyRecovery:
    frequencies: list[int]
    working: list[str]
    verified: bool


def frequencies_from_cumulative(cf: Sequence[int]) -> FrequencyRecovery:
    if not is_cumulative(cf):
        raise MathError("not_cumulative: the totals decrease somewhere")
    f = [cf[0]] + [b - a for a, b in zip(cf, cf[1:])]
    working = [f"f₁ = {cf[0]}"] + [f"f{_sub(i + 2)} = {b} − {a} = {b - a}" for i, (a, b) in enumerate(zip(cf, cf[1:]))]
    total_ok = sum(f) == cf[-1]
    working.append(f"Check: {' + '.join(map(str, f))} = {sum(f)} = last c.f. {cf[-1]} {'✓' if total_ok else '✗'}")
    return FrequencyRecovery(f, working, total_ok)


def _sub(n: int) -> str:
    return "".join("₀₁₂₃₄₅₆₇₈₉"[int(c)] for c in str(n))


def grouped_mean(mids: Sequence[float], freqs: Sequence[int]) -> Value:
    if len(mids) != len(freqs) or not sum(freqs):
        raise MathError("bad_table")
    return Value(sp.nsimplify(sum(sp.nsimplify(x) * f for x, f in zip(mids, freqs)) / sum(freqs)))


TRIG_EXACT = {  # sin, cos, tan of the standard angles — the Class 10 table
    0: ("0", "1", "0"), 30: ("1/2", "√3/2", "1/√3"), 45: ("1/√2", "1/√2", "1"),
    60: ("√3/2", "1/2", "√3"), 90: ("1", "0", "not defined"),
}

_UNITS = {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0, "s": 1.0, "min": 60.0, "h": 3600.0}


def convert(value: float, frm: str, to: str, power: int = 1) -> float:
    """Length/time conversions, with ``power`` 2 for areas and 3 for volumes (cm² → m²)."""
    if frm not in _UNITS or to not in _UNITS:
        raise MathError("unknown_unit")
    if (frm in ("s", "min", "h")) != (to in ("s", "min", "h")):
        raise MathError("incompatible_units")
    return value * (_UNITS[frm] / _UNITS[to]) ** power


def pythagoras_check(a: float, b: float, c: float) -> bool:
    """Is (a, b, c) a right triangle with hypotenuse c? Flags a hypotenuse shorter than a leg."""
    if c <= max(a, b):
        return False
    return math.isclose(a * a + b * b, c * c, rel_tol=1e-9)

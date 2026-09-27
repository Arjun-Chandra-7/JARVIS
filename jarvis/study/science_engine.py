"""Formulae, SI units and calculation for Class 10 science — deterministic and checked.

A formula is stored once, as an equation between named symbols, each with the quantity it stands
for and its SI unit. Solving for any symbol is SymPy's job; the unit of the answer is derived
from the units of the inputs by dimensional analysis, and compared with the unit the formula says
the answer must have. A mismatch (say, a length given in cm into R = ρl/A without converting) is
reported, not silently "fixed".

Formula strings here are written by us, not the student; the student's values arrive as numbers
with unit strings parsed by ``quantity``.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

import sympy as sp

# Dimensions as exponents of (kg, m, s, A).
Dim = tuple[int, int, int, int]
_BASE: dict[str, tuple[float, Dim]] = {
    "": (1, (0, 0, 0, 0)),
    "kg": (1, (1, 0, 0, 0)), "g": (1e-3, (1, 0, 0, 0)),
    "m": (1, (0, 1, 0, 0)), "cm": (1e-2, (0, 1, 0, 0)), "mm": (1e-3, (0, 1, 0, 0)), "km": (1e3, (0, 1, 0, 0)),
    "m2": (1, (0, 2, 0, 0)), "cm2": (1e-4, (0, 2, 0, 0)), "mm2": (1e-6, (0, 2, 0, 0)),
    "s": (1, (0, 0, 1, 0)), "min": (60, (0, 0, 1, 0)), "h": (3600, (0, 0, 1, 0)),
    "A": (1, (0, 0, 0, 1)), "mA": (1e-3, (0, 0, 0, 1)),
    "C": (1, (0, 0, 1, 1)),
    "J": (1, (1, 2, -2, 0)), "kJ": (1e3, (1, 2, -2, 0)), "kWh": (3.6e6, (1, 2, -2, 0)),
    "W": (1, (1, 2, -3, 0)), "kW": (1e3, (1, 2, -3, 0)),
    "V": (1, (1, 2, -3, -1)), "mV": (1e-3, (1, 2, -3, -1)),
    "ohm": (1, (1, 2, -3, -2)), "kohm": (1e3, (1, 2, -3, -2)),
    "ohm m": (1, (1, 3, -3, -2)),
    "m/s": (1, (0, 1, -1, 0)),
    "D": (1, (0, -1, 0, 0)),                     # dioptre = 1/m
}
_ALIASES = {"Ω": "ohm", "ohms": "ohm", "Ω m": "ohm m", "Ωm": "ohm m", "ohm-m": "ohm m", "kΩ": "kohm",
            "m²": "m2", "cm²": "cm2", "mm²": "mm2", "amp": "A", "amps": "A", "ampere": "A", "volt": "V",
            "volts": "V", "watt": "W", "joule": "J", "coulomb": "C", "second": "s", "seconds": "s", "sec": "s",
            "metre": "m", "meter": "m", "dioptre": "D", "diopter": "D", "unit": "kWh", "units": "kWh",
            "minutes": "min", "hour": "h", "hours": "h", "m s-1": "m/s", "ms-1": "m/s"}
UNIT_NAMES = {(1, 2, -3, -2): "Ω", (0, 0, 0, 1): "A", (1, 2, -3, -1): "V", (1, 2, -3, 0): "W",
              (1, 2, -2, 0): "J", (0, 0, 1, 1): "C", (0, 1, 0, 0): "m", (0, 0, 1, 0): "s",
              (1, 3, -3, -2): "Ω m", (0, 2, 0, 0): "m²", (0, -1, 0, 0): "D", (0, 1, -1, 0): "m/s",
              (0, 0, 0, 0): ""}


class ScienceError(ValueError):
    pass


def unit(u: str) -> tuple[float, Dim]:
    k = (u or "").strip()
    k = _ALIASES.get(k, _ALIASES.get(k.lower(), k))
    if k not in _BASE:
        raise ScienceError(f"unknown_unit:{u}")
    return _BASE[k]


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: str = ""

    def si(self) -> tuple[float, Dim]:
        f, d = unit(self.unit)
        return self.value * f, d


_Q = re.compile(r"^\s*(?P<v>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(?:×\s*10\^?(?P<e>[-+]?\d+))?\s*(?P<u>[^\d\s].*)?$")


def quantity(text: str) -> Quantity:
    """"0.5 A", "2.4 kΩ", "1.6 × 10^-19 C" → Quantity. A bare number has unit ""."""
    m = _Q.match(text or "")
    if not m:
        raise ScienceError("unreadable_quantity")
    v = float(m.group("v")) * (10 ** int(m.group("e")) if m.group("e") else 1)
    u = (m.group("u") or "").strip()
    unit(u)
    return Quantity(v, u)


@dataclass(frozen=True)
class Formula:
    id: str
    name: str
    equation: str                       # SymPy syntax, both sides
    symbols: dict                       # symbol -> (quantity name, SI unit key)
    chapter: str = ""
    note: str = ""

    def eq(self) -> sp.Eq:
        lhs, rhs = self.equation.split("=")
        loc = {s: sp.Symbol(s, positive=True) for s in self.symbols}
        return sp.Eq(sp.sympify(lhs, locals=loc), sp.sympify(rhs, locals=loc))


def _F(id, name, eq, syms, chapter="sci.electricity", note=""):
    return Formula(id, name, eq, syms, chapter, note)


FORMULAE: dict[str, Formula] = {f.id: f for f in (
    _F("I=Q/t", "Electric current", "I = Q/t", {"I": ("current", "A"), "Q": ("charge", "C"), "t": ("time", "s")}),
    _F("V=W/Q", "Potential difference", "V = W/Q", {"V": ("potential difference", "V"), "W": ("work done", "J"), "Q": ("charge", "C")}),
    _F("V=IR", "Ohm's law", "V = I*R", {"V": ("potential difference", "V"), "I": ("current", "A"), "R": ("resistance", "ohm")},
       note="Holds for a conductor at constant temperature."),
    _F("R=rho*l/A", "Resistance from resistivity", "R = rho*l/A",
       {"R": ("resistance", "ohm"), "rho": ("resistivity", "ohm m"), "l": ("length", "m"), "A": ("cross-sectional area", "m2")}),
    _F("Rs=R1+R2", "Resistors in series", "Rs = R1 + R2", {"Rs": ("total resistance", "ohm"), "R1": ("resistance 1", "ohm"), "R2": ("resistance 2", "ohm")}),
    _F("1/Rp=1/R1+1/R2", "Resistors in parallel", "1/Rp = 1/R1 + 1/R2",
       {"Rp": ("total resistance", "ohm"), "R1": ("resistance 1", "ohm"), "R2": ("resistance 2", "ohm")}),
    _F("H=I^2*R*t", "Joule's law of heating", "H = I**2*R*t",
       {"H": ("heat produced", "J"), "I": ("current", "A"), "R": ("resistance", "ohm"), "t": ("time", "s")}),
    _F("P=V*I", "Electric power", "P = V*I", {"P": ("power", "W"), "V": ("potential difference", "V"), "I": ("current", "A")}),
    _F("P=I^2*R", "Power from current", "P = I**2*R", {"P": ("power", "W"), "I": ("current", "A"), "R": ("resistance", "ohm")}),
    _F("n=c/v", "Refractive index", "n = c/v", {"n": ("refractive index", ""), "c": ("speed of light in vacuum", "m/s"),
                                                  "v": ("speed of light in the medium", "m/s")}, "sci.light"),
    _F("1/v-1/u=1/f", "Lens formula", "1/v - 1/u = 1/f", {"v": ("image distance", "m"), "u": ("object distance", "m"),
                                                          "f": ("focal length", "m")}, "sci.light",
       note="Cartesian sign convention: u is negative for a real object."),
    _F("m=v/u", "Magnification (lens)", "m = v/u", {"m": ("magnification", ""), "v": ("image distance", "m"), "u": ("object distance", "m")}, "sci.light"),
    _F("P=1/f", "Power of a lens", "P = 1/f", {"P": ("power of lens", "D"), "f": ("focal length", "m")}, "sci.light",
       note="f must be in metres for P in dioptres."),
)}

_DIMS = {k: v[1] for k, v in _BASE.items()}


def _dim(u: str) -> Dim:
    return unit(u)[1]


def _combine(expr: sp.Expr, dims: dict[str, Dim]) -> Optional[Dim]:
    """Dimension of a SymPy expression given the dimension of each symbol; None if inconsistent."""
    if expr.is_Symbol:
        return dims[str(expr)]
    if expr.is_Number:
        return (0, 0, 0, 0)
    if expr.is_Add:
        ds = {_combine(a, dims) for a in expr.args}
        ds.discard((0, 0, 0, 0)) if len(ds) > 1 and any(a.is_Number for a in expr.args) else None
        return ds.pop() if len(ds) == 1 else None
    if expr.is_Mul:
        out = (0, 0, 0, 0)
        for a in expr.args:
            d = _combine(a, dims)
            if d is None:
                return None
            out = tuple(x + y for x, y in zip(out, d))
        return out
    if expr.is_Pow and expr.exp.is_Integer:
        d = _combine(expr.base, dims)
        return None if d is None else tuple(x * int(expr.exp) for x in d)
    return None


@dataclass
class Calculation:
    formula: Formula
    target: str
    given: list[str] = field(default_factory=list)
    conversions: list[str] = field(default_factory=list)
    rearranged: str = ""
    substitution: str = ""
    value: float = 0.0
    unit: str = ""
    display: str = ""
    verification: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def steps(self) -> list[str]:
        out = [f"Given: {', '.join(self.given)}", f"To find: {self.formula.symbols[self.target][0]} ({self.target})",
               f"Formula: {self.formula.equation}"]
        out += [f"Convert: {c}" for c in self.conversions]
        if self.rearranged:
            out.append(f"Rearranged: {self.rearranged}")
        out.append(f"Substitute: {self.substitution}")
        out.append(f"Answer: {self.target} = {self.display}")
        return out


def sig(x: float, n: int = 3) -> str:
    """Round to n significant figures, the way a Class 10 answer is written."""
    if x == 0 or not math.isfinite(x):
        return str(x)
    d = n - int(math.floor(math.log10(abs(x)))) - 1
    r = round(x, d)
    s = f"{r:.{max(d, 0)}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def sig_figs_of(text: str) -> int:
    digits = re.sub(r"[^\d.]", "", text.split("×")[0].split("e")[0])
    digits = digits.lstrip("0.").replace(".", "") if "." in digits else digits.strip("0") or "0"
    return max(1, len(digits))


def calculate(formula_id: str, target: str, known: dict[str, str]) -> Calculation:
    """Solve ``formula`` for ``target`` from quantities like {"V": "12 V", "I": "0.5 A"}."""
    f = FORMULAE.get(formula_id)
    if not f:
        raise ScienceError("unknown_formula")
    if target not in f.symbols:
        raise ScienceError("unknown_target")
    calc = Calculation(f, target)
    subs: dict[sp.Symbol, float] = {}
    dims: dict[str, Dim] = {}
    figs = []
    for sym, text in known.items():
        if sym not in f.symbols:
            raise ScienceError(f"unknown_symbol:{sym}")
        q = quantity(text)
        want = f.symbols[sym][1]
        if q.unit == "" and want != "":
            calc.warnings.append(f"missing_unit:{sym}")
            q = Quantity(q.value, want)
        v_si, d = q.si()
        if d != _dim(want):
            raise ScienceError(f"unit_mismatch:{sym} given in {q.unit}, needs {UNIT_NAMES.get(_dim(want), want)}")
        if unit(q.unit)[0] != 1:
            calc.conversions.append(f"{sym} = {text} = {sig(v_si, 4)} {UNIT_NAMES.get(d, want)}")
        calc.given.append(f"{sym} = {text}")
        subs[sp.Symbol(sym, positive=True)] = v_si
        dims[sym] = d
        figs.append(sig_figs_of(text))
    missing = [s for s in f.symbols if s != target and s not in known]
    if missing:
        raise ScienceError(f"missing_values:{','.join(missing)}")
    eq = f.eq()
    T = sp.Symbol(target, positive=True)
    sols = sp.solve(eq, T)
    if not sols:
        raise ScienceError("cannot_rearrange")
    expr = sols[0]
    calc.rearranged = f"{target} = {sp.sstr(expr)}" if sp.sstr(expr) != sp.sstr(eq.rhs) else ""
    value = float(expr.subs(subs))
    shown = sp.sstr(expr)
    for k in sorted(subs, key=lambda k: -len(str(k))):
        shown = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(str(k))}(?![A-Za-z0-9_])", "(" + sig(subs[k], 4) + ")", shown)
    calc.substitution = re.sub(r"\((-?[\d.]+)\)", r"\1", shown) if shown.count("(") <= 2 else shown
    calc.substitution += f" = {sig(value, 4)}"
    dims[target] = _combine(expr, dims) or _dim(f.symbols[target][1])
    expected = _dim(f.symbols[target][1])
    if dims[target] != expected:
        calc.warnings.append("unit_inconsistent")
    calc.value = value
    calc.unit = UNIT_NAMES.get(expected, f.symbols[target][1])
    calc.display = f"{sig(value, max(2, min(figs) if figs else 3))}" + (f" {calc.unit}" if calc.unit else "")
    # Verify by substituting the answer back into the original equation.
    back = eq.lhs.subs({**subs, T: value}) - eq.rhs.subs({**subs, T: value})
    ok = abs(float(back)) <= 1e-9 * max(1.0, abs(value))
    calc.verification.append(f"Substituting {target} = {sig(value, 4)} back: {'both sides equal ✓' if ok else 'mismatch ✗'}")
    return calc


def check_numeric_answer(answer_text: str, expected: Calculation, rel_tol: float = 0.02) -> dict:
    """Is the student's numeric answer right, and did they give a unit? Returns findings."""
    out = {"value_ok": False, "unit_given": False, "unit_ok": False}
    m = re.search(r"[-+]?\d+(?:\.\d+)?(?:\s*×\s*10\^?[-+]?\d+)?\s*[A-Za-zΩΩ°²/ ]*", answer_text or "")
    if not m:
        return out
    try:
        q = quantity(m.group(0).strip())
    except ScienceError:
        return out
    out["unit_given"] = q.unit != ""
    if q.unit:
        v, d = q.si()
        out["unit_ok"] = d == _dim(expected.formula.symbols[expected.target][1])
    else:
        v = q.value
    out["value_ok"] = math.isclose(v, expected.value, rel_tol=rel_tol)
    return out


# Cause → effect chains for explanations and "why" questions (general knowledge, not quotations).
CHAINS = {
    "refraction": ["light passes from one medium into another", "its speed changes at the boundary",
                   "the wavefront turns, so the ray changes direction",
                   "slower (denser) medium → bends towards the normal; faster → away from the normal"],
    "ionic_conduction": ["ionic compounds are made of positive and negative ions",
                         "in the solid the ions are held in a fixed lattice and cannot move",
                         "when molten (or dissolved) the ions are free to move",
                         "moving ions carry charge, so the liquid conducts"],
    "current_direction": ["current was defined as the flow of positive charge before electrons were known",
                          "so conventional current goes from + to − in the external circuit",
                          "electrons are negative and drift from − to +",
                          "both describe the same current; only the sign convention differs"],
    "heating": ["current flows through a resistor", "electrons collide with the ions of the conductor",
                "electrical energy turns into heat", "H = I²Rt"],
}

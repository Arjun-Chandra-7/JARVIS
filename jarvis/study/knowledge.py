"""Verified offline topic cards for the tested vertical slices.

Each card is written for this project in its own words — it is *general Class 10 knowledge*,
checked by hand, and every answer built from it is labelled "NCERT-style", never "NCERT".
A card gives the companion something correct to say with no model at all, and gives the
verifier the scoring points any model-written answer on the topic must contain.

A card holds: the scoring points (with keyword alternatives the rubric matches on), an
explanation per language (intuition → example → precise statement), a comprehension check with
what a right answer contains, marks-based answers built from subsets of the points, and the
misconceptions it is designed to catch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .types import Language, ScoringPoint

EN, HG, HI = Language.ENGLISH, Language.HINGLISH, Language.HINDI


@dataclass
class Check:
    question: str
    expects: list[list[str]]         # keyword groups; any group fully present = understood
    answer: str


@dataclass
class Card:
    topic: str                       # curriculum topic id
    chapter: str                     # curriculum chapter id
    title: str
    points: list[ScoringPoint]
    explain: dict                    # Language -> list of paragraphs
    example: dict                    # Language -> str
    check: dict                      # Language -> Check
    exam: dict                       # marks -> list of (point id, sentence)
    definition: dict = field(default_factory=dict)
    misconceptions: tuple[str, ...] = ()
    formulae: tuple[str, ...] = ()
    chain: str = ""                  # science_engine.CHAINS key
    visual: str = ""                 # visuals builder name
    revision: list[str] = field(default_factory=list)
    mistakes: list[str] = field(default_factory=list)
    likely: list[str] = field(default_factory=list)

    def point(self, pid: str) -> Optional[ScoringPoint]:
        return next((p for p in self.points if p.id == pid), None)

    def exam_answer(self, marks: int) -> list[str]:
        best = max((m for m in self.exam if m <= marks), default=min(self.exam))
        return [s for _, s in self.exam[best]]


def P(id, idea, *keywords, marks=1.0, required=True, kind="idea") -> ScoringPoint:
    return ScoringPoint(id=id, idea=idea, keywords=[list(k) for k in keywords], marks=marks, required=required, kind=kind)


CARDS: dict[str, Card] = {}


def _add(card: Card) -> Card:
    CARDS[card.topic] = card
    return card


# ------------------------------------------------------------------ Electricity: current direction
_add(Card(
    topic="current_direction", chapter="sci.electricity", title="Conventional current vs electron flow",
    points=[
        P("history", "Direction of current was fixed as the flow of positive charge before electrons were discovered",
          ("positive", "charge", "before"), ("positive", "charge", "convention"), ("positive", "charge", "earlier")),
        P("conventional", "Conventional current flows from the positive terminal to the negative terminal (outside the cell)",
          ("positive", "negative", "terminal"), ("+", "−"), ("positive", "to", "negative")),
        P("electrons", "Electrons are negatively charged and drift from the negative terminal to the positive terminal",
          ("electron", "negative", "positive"), ("electrons", "opposite")),
        P("same_current", "Both describe the same current; the convention is still used", ("same", "current"),
          ("convention", "still"), required=False),
    ],
    explain={
        EN: ["Think of it as a naming choice made early. Scientists agreed on a direction for current before anyone "
             "knew what actually moves in a wire, and they assumed it was positive charge.",
             "Later we found that in metal wires the moving particles are electrons, which are negative. Negative "
             "charge moving one way has the same effect as positive charge moving the other way.",
             "So conventional current is drawn from the + terminal to the − terminal outside the cell, while electrons "
             "drift from − to +. Same current, opposite arrows."],
        HG: ["Isko ek purana naming decision samjho. Jab tak electron discover nahi hua tha, scientists ne maan liya "
             "ki wire mein positive charge chalta hai — aur current ka direction usi hisaab se fix kar diya.",
             "Baad mein pata chala ki metal wire mein asal mein electrons move karte hain, aur electron negative hota "
             "hai. Negative charge ek taraf jaaye, ya positive charge ulti taraf — circuit pe effect same hota hai.",
             "Isliye conventional current + terminal se − terminal ki taraf dikhaya jaata hai (cell ke bahar), aur "
             "electrons − se + ki taraf drift karte hain. Current wahi hai, bas arrow ulta hai."],
        HI: ["इसे एक पुराना नाम रखने का फ़ैसला समझो। इलेक्ट्रॉन की खोज से पहले वैज्ञानिकों ने मान लिया था कि तार में "
             "धनात्मक आवेश चलता है, और धारा (current) की दिशा उसी हिसाब से तय कर दी।",
             "बाद में पता चला कि धातु के तार में असल में इलेक्ट्रॉन चलते हैं, जो ऋणात्मक होते हैं। ऋणात्मक आवेश का "
             "एक ओर जाना और धनात्मक आवेश का उलटी ओर जाना — दोनों का असर एक जैसा है।",
             "इसलिए परंपरागत धारा सेल के बाहर + सिरे से − सिरे की ओर मानी जाती है, और इलेक्ट्रॉन − से + की ओर "
             "चलते हैं। धारा वही है, बस तीर उलटा है।"],
    },
    example={
        EN: "Like a queue shuffling forward: people step one way while the empty gap moves the other way — describing "
            "either tells you the same thing about the queue.",
        HG: "Jaise cinema ki line mein log aage khisakte hain aur khaali jagah peeche ki taraf jaati hai — dono se "
            "same movement describe hota hai.",
        HI: "जैसे सिनेमा की लाइन में लोग आगे खिसकते हैं और ख़ाली जगह पीछे की ओर जाती है — दोनों से वही हलचल समझ आती है।",
    },
    check={
        EN: Check("In a torch circuit, which way do the electrons move through the bulb compared with the current arrow?",
                  [["opposite"], ["negative", "positive"], ["ulta"]], "Opposite to the current arrow: from − towards +."),
        HG: Check("Ek torch ke circuit mein bulb ke through electrons current ke arrow ke saath chalte hain ya ulte?",
                  [["ulta"], ["ulte"], ["opposite"], ["negative", "positive"]], "Ulte — electrons − se + ki taraf jaate hain."),
        HI: Check("टॉर्च के सर्किट में इलेक्ट्रॉन धारा के तीर की दिशा में चलते हैं या उलटी दिशा में?",
                  [["उलटी"], ["उल्टी"], ["opposite"]], "उलटी दिशा में — − से + की ओर।"),
    },
    exam={
        2: [("conventional", "Conventional current flows from the positive terminal to the negative terminal of the cell through the external circuit."),
            ("electrons", "Electrons, being negatively charged, flow from the negative terminal to the positive terminal, i.e. opposite to conventional current.")],
        3: [("history", "The direction of current was fixed as the direction of flow of positive charge before electrons were discovered."),
            ("conventional", "So conventional current flows from the positive to the negative terminal in the external circuit."),
            ("electrons", "Electrons are negatively charged and move from the negative to the positive terminal, opposite to conventional current.")],
    },
    definition={EN: "Electric current is the rate of flow of electric charge through a cross-section of a conductor; I = Q/t, SI unit ampere (A).",
                HG: "Electric current matlab charge kitni tezi se flow ho raha hai: I = Q/t, unit ampere (A).",
                HI: "विद्युत धारा आवेश के प्रवाह की दर है: I = Q/t, SI मात्रक ऐम्पियर (A)।"},
    misconceptions=("current_equals_electron_flow",), formulae=("I=Q/t",), chain="current_direction", visual="circuit",
    revision=["I = Q/t, unit ampere (A); 1 A = 1 C/s", "Conventional current: + → − outside the cell",
              "Electron flow: − → +, opposite to conventional current"],
    mistakes=["Drawing the current arrow the way electrons move", "Forgetting 'outside the cell' / external circuit"],
    likely=["Why is the direction of current opposite to electron flow?", "Define 1 ampere."],
))

# ------------------------------------------------------------------ Metals: ionic compounds conduct
_add(Card(
    topic="ionic_properties", chapter="sci.metals", title="Why ionic compounds conduct only when molten or dissolved",
    points=[
        P("ions", "Ionic compounds are made of oppositely charged ions", ("ions",), ("ionic", "charged", "particles")),
        P("solid_fixed", "In the solid state the ions are held in fixed positions in a rigid lattice / by strong "
          "electrostatic forces, so they cannot move", ("solid", "fixed"), ("solid", "cannot", "move"),
          ("solid", "not", "free"), ("rigid", "structure")),
        P("molten_move", "When molten (or dissolved in water) the ions become free to move and carry charge, so "
          "electricity is conducted", ("molten", "free", "move"), ("molten", "ions", "move"), ("melt", "move", "charge"),
          ("molten", "mobile")),
        P("solution", "Aqueous solutions conduct for the same reason", ("water", "move"), ("solution", "move"),
          required=False),
    ],
    explain={
        EN: ["Electricity needs moving charges. An ionic compound is full of charged ions, but in the solid they are "
             "locked in place in a crystal, so nothing can carry the charge along.",
             "Melt it and the lattice breaks up: the same ions can now move, and moving ions carry current.",
             "So: charged particles present in both states, but only mobile in the molten (or dissolved) state."],
        HG: ["Current ke liye charges ka move karna zaroori hai. Ionic compound mein ions to hote hain, par solid "
             "mein wo crystal mein fixed jagah pe lock hote hain — koi charge aage nahi le ja sakta.",
             "Melt karte hi lattice toot jaata hai, wahi ions ab move kar sakte hain, aur moving ions current carry karte hain.",
             "Matlab: charged particles dono state mein hain, par move sirf molten (ya paani mein ghule) state mein karte hain."],
        HI: ["धारा के लिए आवेश का चलना ज़रूरी है। आयनिक यौगिक में आयन तो होते हैं, पर ठोस में वे क्रिस्टल में अपनी "
             "जगह पर जकड़े रहते हैं।", "पिघलाने पर जालक टूट जाता है और वही आयन चल सकते हैं, इसलिए धारा बहती है।"],
    },
    example={EN: "A packed stadium with every seat taken: plenty of people, but nobody can move to the exit until the seats are removed.",
             HG: "Bhare hue stadium jaisa — log bahut hain par seats pe fixed hain, koi exit tak nahi ja sakta.",
             HI: "भरे हुए स्टेडियम जैसा — लोग बहुत हैं पर सीटों पर बँधे हैं।"},
    check={EN: Check("Solid salt has ions. So why doesn't solid salt conduct?", [["fixed"], ["cannot", "move"], ["not", "free"]],
                     "Its ions are fixed in the lattice and cannot move."),
           HG: Check("Solid namak mein bhi ions hain — phir wo conduct kyun nahi karta?", [["fixed"], ["move", "nahi"], ["cannot", "move"]],
                     "Kyunki ions fixed hote hain, move nahi kar sakte."),
           HI: Check("ठोस नमक में भी आयन हैं, फिर भी वह चालन क्यों नहीं करता?", [["जकड़े"], ["चल", "नहीं"]], "आयन अपनी जगह जकड़े रहते हैं।")},
    exam={
        1: [("molten_move", "In the molten state the ions are free to move and carry charge; in the solid state they are fixed.")],
        3: [("ions", "Ionic compounds are made up of positively and negatively charged ions."),
            ("solid_fixed", "In the solid state these ions are held in fixed positions by strong electrostatic forces of attraction, so they cannot move and the solid does not conduct electricity."),
            ("molten_move", "On melting, the ions become free to move and carry charge, so molten ionic compounds conduct electricity.")],
    },
    misconceptions=("solid_ions_move", "ionic_electrons_conduct"), chain="ionic_conduction",
    revision=["Ionic compounds: made of ions", "Solid: ions fixed → no conduction", "Molten / aqueous: ions mobile → conduct"],
    mistakes=["Saying free electrons conduct in molten ionic compounds", "Saying there are no ions in the solid"],
    likely=["Why do ionic compounds have high melting points?", "Why does solid NaCl not conduct electricity?"],
))

# ------------------------------------------------------------------ Light: refraction
_add(Card(
    topic="refraction", chapter="sci.light", title="Refraction of light",
    points=[
        P("definition", "Refraction is the change in direction of light when it passes obliquely from one medium to another",
          ("change", "direction", "medium"), ("bending", "medium"), ("bends", "medium")),
        P("speed", "It happens because the speed of light is different in different media", ("speed",), ("velocity",)),
        P("towards", "Going into an optically denser medium, light bends towards the normal", ("towards", "normal", "denser")),
        P("away", "Going into a rarer medium, it bends away from the normal", ("away", "normal", "rarer"), required=False),
        P("diagram", "Ray diagram with incident ray, normal, refracted ray and angles i and r marked",
          ("diagram",), ("normal", "incident", "refracted"), kind="diagram", required=False),
    ],
    explain={
        EN: ["Light travels at different speeds in air, water and glass. When a ray hits the boundary at a slant, one "
             "side of the beam slows down first, so the beam turns.",
             "Entering a slower (optically denser) medium like glass, it turns towards the normal; coming back out into "
             "air it turns away from the normal.",
             "That is refraction: a change in the direction of light at the boundary because its speed changes."],
        HG: ["Light ki speed air, paani aur glass mein alag hoti hai. Jab ray boundary pe tircha padta hai, beam ka ek "
             "side pehle slow hota hai — isliye beam mud jaata hai.",
             "Denser medium (jaise glass) mein jaate waqt ray normal ki taraf mudti hai, aur wapas air mein aate waqt "
             "normal se door.", "Yahi refraction hai: speed badalne ki wajah se boundary pe light ka direction badalna."],
        HI: ["प्रकाश की चाल हवा, पानी और काँच में अलग होती है। तिरछी किरण सीमा पर मुड़ जाती है।",
             "सघन माध्यम में जाते समय किरण अभिलंब (normal) की ओर मुड़ती है, विरल में जाते समय अभिलंब से दूर।"],
    },
    example={EN: "A straw in a glass of water looks bent at the surface.",
             HG: "Paani ke glass mein straw surface pe tedha dikhta hai.",
             HI: "पानी के गिलास में स्ट्रॉ सतह पर मुड़ा हुआ दिखता है।"},
    check={EN: Check("A ray goes from glass into air. Towards or away from the normal?", [["away"]], "Away from the normal."),
           HG: Check("Ray glass se air mein jaa rahi hai — normal ki taraf mudegi ya door?", [["door"], ["away"]], "Normal se door."),
           HI: Check("किरण काँच से हवा में जा रही है — अभिलंब की ओर या दूर?", [["दूर"], ["away"]], "अभिलंब से दूर।")},
    exam={
        2: [("definition", "Refraction is the change in the direction of propagation of light when it passes obliquely from one transparent medium to another."),
            ("speed", "It occurs because the speed of light is different in different media.")],
        3: [("definition", "Refraction is the change in the direction of propagation of light when it passes obliquely from one transparent medium to another."),
            ("speed", "It occurs because the speed of light changes from one medium to the other."),
            ("towards", "Light going from a rarer to a denser medium bends towards the normal (and away from it in the reverse case).")],
    },
    misconceptions=("refraction_bends_away_denser",), chain="refraction", visual="ray_diagram", formulae=("n=c/v",),
    revision=["Refraction: change of direction at a boundary due to change of speed", "Rarer → denser: towards normal",
              "n = c/v", "Laws: incident ray, refracted ray and normal lie in one plane; sin i / sin r = constant"],
    mistakes=["Forgetting to draw the normal", "No arrows on rays", "Bending the wrong way"],
))

# ------------------------------------------------------------------ Maths: Pythagoras / diagonal
_add(Card(
    topic="pythagoras", chapter="math.triangles", title="Pythagoras theorem",
    points=[
        P("statement", "In a right triangle, hypotenuse² = sum of squares of the other two sides", ("hypotenuse", "square"),
          ("a²", "b²", "c²")),
        P("substitute", "Substitute the known sides", ("substitut",), kind="step"),
        P("root", "Take the square root to get the length", ("√",), ("square root",), ("sqrt",), kind="step"),
        P("unit", "Give the unit", ("cm",), ("m",), ("unit",), kind="unit", required=False),
    ],
    explain={
        EN: ["The theorem gives the square of the long side, not the side itself. The last step is always a square root."],
        HG: ["Theorem se hypotenuse ka square milta hai, side nahi. Isliye last step hamesha square root hota hai."],
        HI: ["प्रमेय से कर्ण का वर्ग मिलता है, भुजा नहीं। इसलिए आख़िरी कदम हमेशा वर्गमूल है।"],
    },
    example={EN: "A 20 cm × 20 cm square tile: its diagonal is √(20² + 20²) = √800 = 20√2 ≈ 28.28 cm, not 800 cm.",
             HG: "20 cm × 20 cm tile ka diagonal √800 = 20√2 ≈ 28.28 cm hai, 800 cm nahi.",
             HI: "20 cm × 20 cm टाइल का विकर्ण √800 = 20√2 ≈ 28.28 cm है।"},
    check={EN: Check("If d² = 50, what is d?", [["5√2"], ["√50"], ["7.07"]], "d = √50 = 5√2 ≈ 7.07"),
           HG: Check("Agar d² = 50 hai, to d kya hoga?", [["5√2"], ["√50"], ["7.07"]], "d = √50 = 5√2"),
           HI: Check("अगर d² = 50, तो d क्या होगा?", [["5√2"], ["√50"]], "d = √50 = 5√2")},
    exam={2: [("statement", "By Pythagoras theorem, d² = a² + b²."),
              ("root", "d = √(a² + b²), taking the positive square root.")]},
    misconceptions=("forgot_square_root", "omits_units"), formulae=("a^2+b^2=c^2",), visual="triangle",
    revision=["h² = p² + b²", "Always finish with √", "Diagonal of a square of side a = a√2"],
    mistakes=["Stopping at d² and writing d = 800", "Adding sides instead of squares"],
))

# ------------------------------------------------------------------ Maths: cumulative frequency
_add(Card(
    topic="cumulative_frequency", chapter="math.statistics", title="Recognising a cumulative frequency table",
    points=[
        P("increasing", "Cumulative frequencies never decrease: each entry adds the next class to the running total",
          ("increas",), ("running", "total"), ("never", "decrease"), ("add",)),
        P("last_total", "The last cumulative frequency equals the total number of observations", ("last", "total")),
        P("recover", "Class frequencies are recovered by subtracting consecutive cumulative frequencies",
          ("subtract",), ("minus",), ("difference",)),
    ],
    explain={
        EN: ["Look at the numbers going down the column. If each one is the previous one plus something — so they "
             "only go up and the last one is the total — it is a running total, i.e. cumulative frequency.",
             "Ordinary frequencies can go up and down; cumulative ones cannot decrease.",
             "To get back the frequency of each class, subtract the entry above it."],
        HG: ["Column ke numbers dekho. Agar har number pichhle wale se bada ya barabar hai aur last number total "
             "hai, to ye running total hai — yaani cumulative frequency.",
             "Normal frequency upar-neeche ho sakti hai, cumulative kabhi ghat-ti nahi.",
             "Har class ki frequency nikaalne ke liye upar wali entry ghata do."],
        HI: ["कॉलम की संख्याएँ देखो। अगर हर संख्या पिछली से बड़ी या बराबर है और आख़िरी संख्या कुल है, तो यह "
             "संचयी बारंबारता है।", "हर वर्ग की बारंबारता पाने के लिए ऊपर वाली संख्या घटाओ।"],
    },
    example={EN: "Marks 0–10, 10–20, 20–30, 30–40, 40–50 with column 5, 12, 20, 26, 30: always rising and ending at the total 30.",
             HG: "Column 5, 12, 20, 26, 30 — hamesha badh raha hai aur 30 (total) pe khatam.",
             HI: "कॉलम 5, 12, 20, 26, 30 — हमेशा बढ़ रहा है और 30 (कुल) पर ख़त्म।"},
    check={EN: Check("The c.f. column reads 4, 9, 15. What is the frequency of the second class?", [["5"]], "9 − 4 = 5"),
           HG: Check("c.f. column 4, 9, 15 hai. Doosri class ki frequency?", [["5"]], "9 − 4 = 5"),
           HI: Check("संचयी बारंबारता 4, 9, 15 है। दूसरे वर्ग की बारंबारता?", [["5"]], "9 − 4 = 5")},
    exam={2: [("increasing", "The given column is cumulative because each value is the running total of the frequencies up to that class, so it never decreases."),
              ("recover", "Class frequencies are found by subtracting consecutive cumulative frequencies.")]},
    misconceptions=("cf_as_frequency",), visual="table",
    revision=["c.f. never decreases; last c.f. = n", "f = c.f. − previous c.f.", "Median class: first c.f. ≥ n/2"],
    mistakes=["Using c.f. as f in the mean formula", "Using the median class's own c.f. instead of the previous one"],
))

# ------------------------------------------------------------------ Electricity: Ohm's law & combinations (quiz / revision)
_add(Card(
    topic="ohms_law", chapter="sci.electricity", title="Ohm's law",
    points=[
        P("statement", "At constant temperature the potential difference across a conductor is directly proportional to the current",
          ("proportional", "current"), ("constant", "temperature")),
        P("equation", "V = IR, where R is the resistance", ("v", "ir"), ("v = ir",), kind="formula"),
        P("graph", "The V–I graph is a straight line through the origin", ("straight", "line"), ("graph",), required=False),
    ],
    explain={EN: ["Push harder (more potential difference) and more current flows — in proportion, as long as the wire's temperature stays the same. The constant of proportionality is the resistance: V = IR."],
             HG: ["Zyada push (potential difference) do to zyada current — proportion mein, jab tak temperature same rahe. V = IR, R resistance hai."],
             HI: ["तापमान स्थिर रहने पर विभवांतर धारा के समानुपाती होता है: V = IR।"]},
    example={EN: "A 12 V supply across a 24 Ω resistor drives 0.5 A.", HG: "12 V aur 24 Ω se 0.5 A current.", HI: "12 V और 24 Ω से 0.5 A।"},
    check={EN: Check("If V doubles and R stays the same, what happens to I?", [["double"], ["twice"], ["2"]], "It doubles."),
           HG: Check("V double karo aur R same rakho, to I ka kya hoga?", [["double"], ["do guna"], ["2"]], "Double ho jaayega."),
           HI: Check("V दोगुना और R वही, तो I?", [["दोगुना"], ["double"]], "दोगुना।")},
    exam={3: [("statement", "Ohm's law: at constant temperature, the potential difference across a conductor is directly proportional to the current through it."),
              ("equation", "V ∝ I, so V = IR, where R is the resistance of the conductor."),
              ("graph", "The V–I graph for such a conductor is a straight line passing through the origin.")]},
    misconceptions=("omits_units",), formulae=("V=IR",),
    revision=["V = IR (constant temperature)", "1 Ω = 1 V/A", "V–I graph: straight line through origin"],
    mistakes=["Leaving out 'at constant temperature'", "No unit on R"],
))
_add(Card(
    topic="combinations", chapter="sci.electricity", title="Resistors in series and parallel",
    points=[P("series", "Series: R = R1 + R2 + …", ("series",)), P("parallel", "Parallel: 1/R = 1/R1 + 1/R2 + …", ("parallel",)),
            P("unit", "Answer in ohm", ("ω",), ("ohm",), kind="unit")],
    explain={EN: ["In series the current has one path, so resistances add. In parallel there are more paths, so the total is less than the smallest."],
             HG: ["Series mein current ka ek hi raasta — resistances add. Parallel mein zyada raaste — total sabse chhote se bhi kam."],
             HI: ["श्रेणी में एक ही रास्ता — प्रतिरोध जुड़ते हैं। समांतर में कई रास्ते — कुल प्रतिरोध सबसे छोटे से भी कम।"]},
    example={EN: "4 Ω and 12 Ω: series 16 Ω, parallel 3 Ω.", HG: "4 Ω aur 12 Ω: series 16 Ω, parallel 3 Ω.", HI: "4 Ω और 12 Ω: श्रेणी 16 Ω, समांतर 3 Ω।"},
    check={EN: Check("Is the parallel total bigger or smaller than the smallest resistor?", [["smaller"], ["less"]], "Smaller."),
           HG: Check("Parallel ka total sabse chhote resistor se bada hoga ya chhota?", [["chhota"], ["smaller"], ["kam"]], "Chhota."),
           HI: Check("समांतर का कुल प्रतिरोध सबसे छोटे से बड़ा या छोटा?", [["छोटा"]], "छोटा।")},
    exam={2: [("series", "In series, Rs = R1 + R2."), ("parallel", "In parallel, 1/Rp = 1/R1 + 1/R2.")]},
    misconceptions=("series_parallel_swap", "omits_units"), formulae=("Rs=R1+R2", "1/Rp=1/R1+1/R2"), visual="circuit",
    revision=["Series: add", "Parallel: add reciprocals; total < smallest", "Household wiring is parallel"],
    mistakes=["Forgetting to invert 1/Rp at the end"],
))
_add(Card(
    topic="heating_power", chapter="sci.electricity", title="Heating effect and power",
    points=[P("joule", "H = I²Rt", ("i²rt",), ("i^2rt",), kind="formula"), P("power", "P = VI = I²R = V²/R", ("p", "vi"), kind="formula"),
            P("unit", "1 kWh = 3.6 × 10⁶ J", ("kwh",), required=False)],
    explain={EN: ["Current through a resistor turns electrical energy into heat: H = I²Rt. Power is the rate: P = VI."],
             HG: ["Resistor mein current se electrical energy heat banti hai: H = I²Rt. Power uski rate hai: P = VI."],
             HI: ["प्रतिरोधक में धारा से ऊष्मा बनती है: H = I²Rt। शक्ति: P = VI।"]},
    example={EN: "2 A through 5 Ω for 1 minute: H = 4 × 5 × 60 = 1200 J.", HG: "2 A, 5 Ω, 1 minute: H = 1200 J.", HI: "2 A, 5 Ω, 1 मिनट: H = 1200 J।"},
    check={EN: Check("If the current doubles, how does the heat change?", [["four"], ["4"]], "Four times (I²)."),
           HG: Check("Current double ho to heat kitni guna?", [["4"], ["char"], ["four"]], "4 guna."),
           HI: Check("धारा दोगुनी हो तो ऊष्मा कितनी गुना?", [["4"], ["चार"]], "4 गुना।")},
    exam={2: [("joule", "H = I²Rt (Joule's law of heating)."), ("power", "P = VI = I²R = V²/R.")]},
    misconceptions=("omits_units",), formulae=("H=I^2*R*t", "P=V*I", "P=I^2*R"),
    revision=["H = I²Rt", "P = VI = I²R = V²/R", "1 kWh = 3.6 × 10⁶ J"],
    mistakes=["Using minutes instead of seconds in H = I²Rt"],
))


def card_for(topic: str) -> Optional[Card]:
    return CARDS.get(topic)

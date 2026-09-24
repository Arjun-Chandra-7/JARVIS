"""RAG — retrieval-augmented generation — drawn as a diagram and explained a step at a time.

Two rows: indexing along the top (documents → embedding model → vector database) and the question
along the bottom (query → similarity search → retrieved chunks → language model), meeting at the
embedding model and the database, with the answer coming back up at the end.

Follow-ups change the diagram that is already there — focus a node, show the chunks, mark where
things go wrong, put fine-tuning beside it — rather than drawing a new one.
"""
from __future__ import annotations

from typing import Optional

from .. import layout
from ..plan import LessonPlan, Phrase, SourceContext, Step
from ..scene import add, anim, create, focus, highlight, obj, unfocus, unhighlight, update

TOPIC = "rag"

NODES = ("docs", "embed", "vdb", "query", "search", "retrieved", "llm", "answer")
# (column, row) on a 5 × 2 grid
GRID = {"docs": (0, 0), "embed": (1, 0), "vdb": (2, 0), "answer": (4, 0),
        "query": (0, 1), "search": (2, 1), "retrieved": (3, 1), "llm": (4, 1)}
ICON = {"docs": "chunks", "embed": "vector", "vdb": "db", "query": "user", "search": "search",
        "retrieved": "context", "llm": "model", "answer": "answer"}
EDGES = [  # id, from, to, bend
    ("e-docs-embed", "docs", "embed", 0), ("e-embed-vdb", "embed", "vdb", 0),
    ("e-query-embed", "query", "embed", 0), ("e-embed-search", "embed", "search", 0),
    ("e-vdb-search", "vdb", "search", 0), ("e-search-retrieved", "search", "retrieved", 0),
    ("e-retrieved-llm", "retrieved", "llm", 0), ("e-llm-answer", "llm", "answer", 0),
]

LABELS = {
    "en": {"docs": ("Chunked documents", "split into pieces"), "embed": ("Embedding model", "text → vector"),
           "vdb": ("Vector database", "stores the vectors"), "query": ("User query", "your question"),
           "search": ("Similarity search", "nearest vectors"), "retrieved": ("Retrieved chunks", "top matches"),
           "llm": ("Language model", "reads + writes"), "answer": ("Final answer", "grounded in your docs"),
           "edges": {"e-docs-embed": "chunks", "e-embed-vdb": "vectors", "e-query-embed": "question",
                     "e-embed-search": "query vector", "e-vdb-search": "compare", "e-search-retrieved": "top-k",
                     "e-retrieved-llm": "context", "e-llm-answer": "answer"},
           "title": "RAG · retrieval-augmented generation"},
    "hi-pure": {"docs": ("दस्तावेज़ के टुकड़े", "छोटे भागों में"), "embed": ("एम्बेडिंग मॉडल", "पाठ → सदिश"),
                "vdb": ("वेक्टर डेटाबेस", "सदिश सहेजता है"), "query": ("आपका प्रश्न", "उपयोगकर्ता"),
                "search": ("समानता खोज", "निकटतम सदिश"), "retrieved": ("मिले हुए टुकड़े", "सबसे मेल खाते"),
                "llm": ("भाषा मॉडल", "पढ़ता + लिखता"), "answer": ("अंतिम उत्तर", "दस्तावेज़ों पर आधारित"),
                "edges": {"e-docs-embed": "टुकड़े", "e-embed-vdb": "सदिश", "e-query-embed": "प्रश्न",
                          "e-embed-search": "प्रश्न सदिश", "e-vdb-search": "तुलना", "e-search-retrieved": "शीर्ष",
                          "e-retrieved-llm": "संदर्भ", "e-llm-answer": "उत्तर"},
                "title": "RAG · खोज-आधारित उत्तर"},
}

T = {
    "en": {
        "intro": "RAG means retrieval-augmented generation. Let me draw it.",
        "s1": ["First, your documents are split into small chunks, a paragraph or so each."],
        "s2": ["An embedding model turns each chunk into a vector,", "a list of numbers that captures its meaning."],
        "s3": ["Those vectors are stored in a vector database."],
        "s4": ["When you ask a question,", "the same embedding model turns your question into a vector too."],
        "s5": ["The database finds the chunks whose vectors are closest to your question.", "That's similarity search."],
        "s6": ["Those best-matching chunks are pulled out as context."],
        "s7": ["The language model reads your question together with that context,",
               "and writes an answer grounded in your documents."],
        "close": "So: retrieve first, then generate.",
        "check": "Quick check: why does the question need to become a vector too?",
    },
    "hinglish": {
        "intro": "RAG ka matlab hai retrieval-augmented generation. Chaliye diagram banate hain.",
        "s1": ["Sabse pehle, aapke documents ko chhote chhote chunks mein toda jaata hai."],
        "s2": ["Embedding model har chunk ko ek vector bana deta hai,", "yaani numbers ki list, jo uska meaning pakadti hai."],
        "s3": ["Yeh vectors ek vector database mein store hote hain."],
        "s4": ["Jab aap sawaal poochte ho,", "wahi embedding model aapke sawaal ko bhi vector bana deta hai."],
        "s5": ["Database woh chunks dhoondta hai jinke vectors aapke sawaal ke sabse kareeb hain.",
               "Isse similarity search kehte hain."],
        "s6": ["Sabse matching chunks context ban kar bahar aate hain."],
        "s7": ["Language model aapka sawaal aur woh context saath mein padhta hai,",
               "aur aapke documents pe based answer likhta hai."],
        "close": "Matlab: pehle retrieve, phir generate.",
        "check": "Chhota sa sawaal: sawaal ko bhi vector kyun banana padta hai?",
    },
    "hi": {
        "intro": "RAG का मतलब है retrieval-augmented generation. चलिए diagram बनाते हैं।",
        "s1": ["सबसे पहले, आपके documents को छोटे छोटे chunks में तोड़ा जाता है।"],
        "s2": ["Embedding model हर chunk को एक vector बना देता है,", "यानी numbers की list, जो उसका meaning पकड़ती है।"],
        "s3": ["ये vectors एक vector database में store होते हैं।"],
        "s4": ["जब आप सवाल पूछते हो,", "वही embedding model आपके सवाल को भी vector बना देता है।"],
        "s5": ["Database वो chunks ढूँढता है जिनके vectors आपके सवाल के सबसे क़रीब हैं।", "इसे similarity search कहते हैं।"],
        "s6": ["सबसे matching chunks context बनकर बाहर आते हैं।"],
        "s7": ["Language model आपका सवाल और वो context साथ में पढ़ता है,", "और आपके documents पर based answer लिखता है।"],
        "close": "मतलब: पहले retrieve, फिर generate।",
        "check": "छोटा सा सवाल: सवाल को भी vector क्यों बनाना पड़ता है?",
    },
    "hi-pure": {
        "intro": "RAG का अर्थ है, खोज की सहायता से उत्तर बनाना। आइए इसका चित्र बनाते हैं।",
        "s1": ["सबसे पहले, आपके दस्तावेज़ों को छोटे-छोटे टुकड़ों में बाँटा जाता है।"],
        "s2": ["एम्बेडिंग मॉडल हर टुकड़े को एक सदिश में बदलता है,", "यानी संख्याओं की सूची, जो उसका अर्थ पकड़ती है।"],
        "s3": ["ये सदिश एक वेक्टर डेटाबेस में सहेजे जाते हैं।"],
        "s4": ["जब आप प्रश्न पूछते हैं,", "वही मॉडल आपके प्रश्न को भी सदिश में बदल देता है।"],
        "s5": ["डेटाबेस वे टुकड़े खोजता है जिनके सदिश आपके प्रश्न के सबसे निकट हैं।", "इसे समानता खोज कहते हैं।"],
        "s6": ["सबसे मेल खाने वाले टुकड़े संदर्भ के रूप में निकाले जाते हैं।"],
        "s7": ["भाषा मॉडल आपका प्रश्न और वह संदर्भ साथ पढ़ता है,", "और आपके दस्तावेज़ों पर आधारित उत्तर लिखता है।"],
        "close": "अर्थात: पहले खोजो, फिर उत्तर बनाओ।",
        "check": "एक छोटा प्रश्न: प्रश्न को भी सदिश में क्यों बदलना पड़ता है?",
    },
}

# Follow-up explanations. One or two sentences each; the diagram does the rest.
F = {
    "en": {
        "docs": "Chunking happens right at the start, before anything is embedded. Documents are cut into pieces small enough to match one idea each.",
        "embed": "The embedding model reads text and outputs a vector. Texts that mean similar things land close together, even with different words.",
        "vdb": "The vector database stores every chunk's vector with the chunk itself, and is built to find the nearest vectors fast, even among millions.",
        "query": "Your question goes through the same embedding model, so it lives in the same space as the chunks and can be compared with them.",
        "search": "Similarity search measures how close your question's vector is to each chunk's vector, usually by cosine similarity, and keeps the closest few.",
        "retrieved": "The retrieved chunks are the few passages most similar to your question. They become the context the model is given.",
        "llm": "The language model gets your question plus the retrieved chunks in its prompt, and writes the answer from them.",
        "answer": "The answer is grounded: it should only say what the retrieved chunks support, so it can point back to its sources.",
        "chunking": ["Chunking happens here, at the very start.", "Each document is cut into small overlapping pieces before anything is embedded."],
        "bad": ["If retrieval brings back the wrong chunks,", "the model answers from the wrong context, and it sounds just as confident.",
                "So most RAG bugs are retrieval bugs: check what was retrieved first."],
        "hallucination": ["Hallucination happens in the language model,",
                          "when the retrieved context doesn't contain the answer and the model fills the gap from memory."],
        "finetune": ["Fine-tuning is the other way to teach a model.", "RAG keeps knowledge in a database you can update any time, and it can show sources.",
                     "Fine-tuning bakes knowledge into the weights: updating means retraining, and it's best for style and behaviour."],
        "bigger": "Here it is, bigger.",
        "cmp_title": "RAG vs fine-tuning",
        "cmp_rag": ["Knowledge lives in a database", "Update: add documents", "Can cite its sources"],
        "cmp_ft": ["Knowledge baked into weights", "Update: retrain the model", "Best for style and behaviour"],
        "bad_tag": "wrong chunks → wrong answer",
        "hall_tag": "fills gaps from memory",
    },
    "hinglish": {
        "docs": "Chunking bilkul shuru mein hoti hai, embedding se pehle. Documents ko itne chhote pieces mein kaata jaata hai ki har piece ek idea ho.",
        "embed": "Embedding model text padh kar ek vector deta hai. Jo cheezein matlab mein milti julti hain, unke vectors paas paas hote hain.",
        "vdb": "Vector database har chunk ka vector uske text ke saath rakhta hai, aur lakhon mein se bhi sabse kareeb vectors jaldi dhoond leta hai.",
        "query": "Aapka sawaal usi embedding model se guzarta hai, isliye woh chunks ke saath compare ho sakta hai.",
        "search": "Similarity search dekhta hai ki sawaal ka vector har chunk ke vector se kitna kareeb hai, aur sabse kareeb wale rakh leta hai.",
        "retrieved": "Retrieved chunks woh kuch passages hain jo sawaal se sabse zyada milte hain. Yahi model ka context bante hain.",
        "llm": "Language model ko prompt mein sawaal aur retrieved chunks milte hain, aur woh unhi se answer likhta hai.",
        "answer": "Answer grounded hota hai: sirf wahi bolna chahiye jo retrieved chunks mein hai, taaki source dikhaya ja sake.",
        "chunking": ["Chunking yahan hoti hai, bilkul shuru mein.", "Embedding se pehle har document chhote pieces mein kaata jaata hai."],
        "bad": ["Agar retrieval galat chunks le aaye,", "to model galat context se answer deta hai, aur utne hi confidence se.",
                "Isliye RAG ki zyada tar galtiyan retrieval mein hoti hain: pehle dekho kya retrieve hua."],
        "hallucination": ["Hallucination language model mein hoti hai,",
                          "jab context mein answer nahi hota aur model apni memory se gap bhar deta hai."],
        "finetune": ["Fine-tuning model ko sikhane ka doosra tareeka hai.",
                     "RAG mein knowledge database mein rehti hai, kabhi bhi update karo, aur source dikh sakta hai.",
                     "Fine-tuning mein knowledge weights mein chali jaati hai: update ke liye retraining chahiye, style aur behaviour ke liye best hai."],
        "bigger": "Yeh lo, bada karke.",
        "cmp_title": "RAG vs fine-tuning",
        "cmp_rag": ["Knowledge database mein", "Update: documents jodo", "Source dikha sakta hai"],
        "cmp_ft": ["Knowledge weights mein", "Update: retrain karo", "Style aur behaviour ke liye"],
        "bad_tag": "galat chunks → galat answer",
        "hall_tag": "memory se gap bharta hai",
    },
    "hi": {
        "docs": "Chunking बिल्कुल शुरू में होती है, embedding से पहले। Documents को इतने छोटे pieces में काटा जाता है कि हर piece एक idea हो।",
        "embed": "Embedding model text पढ़कर एक vector देता है। जो चीज़ें मतलब में मिलती-जुलती हैं, उनके vectors पास-पास होते हैं।",
        "vdb": "Vector database हर chunk का vector उसके text के साथ रखता है, और लाखों में से भी सबसे क़रीब vectors जल्दी ढूँढ लेता है।",
        "query": "आपका सवाल उसी embedding model से गुज़रता है, इसलिए वो chunks के साथ compare हो सकता है।",
        "search": "Similarity search देखता है कि सवाल का vector हर chunk के vector से कितना क़रीब है, और सबसे क़रीब वाले रख लेता है।",
        "retrieved": "Retrieved chunks वो कुछ passages हैं जो सवाल से सबसे ज़्यादा मिलते हैं। यही model का context बनते हैं।",
        "llm": "Language model को prompt में सवाल और retrieved chunks मिलते हैं, और वो उन्हीं से answer लिखता है।",
        "answer": "Answer grounded होता है: सिर्फ़ वही बोलना चाहिए जो retrieved chunks में है, ताकि source दिखाया जा सके।",
        "chunking": ["Chunking यहाँ होती है, बिल्कुल शुरू में।", "Embedding से पहले हर document छोटे pieces में काटा जाता है।"],
        "bad": ["अगर retrieval ग़लत chunks ले आए,", "तो model ग़लत context से answer देता है, और उतने ही confidence से।",
                "इसलिए RAG की ज़्यादातर ग़लतियाँ retrieval में होती हैं: पहले देखो क्या retrieve हुआ।"],
        "hallucination": ["Hallucination language model में होती है,", "जब context में answer नहीं होता और model अपनी memory से gap भर देता है।"],
        "finetune": ["Fine-tuning model को सिखाने का दूसरा तरीका है।",
                     "RAG में knowledge database में रहती है, कभी भी update करो, और source दिख सकता है।",
                     "Fine-tuning में knowledge weights में चली जाती है: update के लिए retraining चाहिए, style और behaviour के लिए best है।"],
        "bigger": "ये लो, बड़ा करके।",
        "cmp_title": "RAG vs fine-tuning",
        "cmp_rag": ["Knowledge database में", "Update: documents जोड़ो", "Source दिखा सकता है"],
        "cmp_ft": ["Knowledge weights में", "Update: retrain करो", "Style और behaviour के लिए"],
        "bad_tag": "ग़लत chunks → ग़लत answer",
        "hall_tag": "memory से gap भरता है",
    },
    "hi-pure": {
        "docs": "टुकड़े बनाना बिल्कुल शुरुआत में होता है। दस्तावेज़ों को इतने छोटे भागों में बाँटा जाता है कि हर भाग में एक विचार हो।",
        "embed": "एम्बेडिंग मॉडल पाठ पढ़कर एक सदिश देता है। जिनका अर्थ मिलता-जुलता है, उनके सदिश पास-पास होते हैं।",
        "vdb": "वेक्टर डेटाबेस हर टुकड़े का सदिश उसके पाठ के साथ रखता है, और लाखों में से भी निकटतम सदिश जल्दी खोज लेता है।",
        "query": "आपका प्रश्न उसी मॉडल से गुज़रता है, इसलिए उसकी तुलना टुकड़ों से हो सकती है।",
        "search": "समानता खोज मापती है कि प्रश्न का सदिश हर टुकड़े के सदिश से कितना निकट है, और सबसे निकट वाले रखती है।",
        "retrieved": "मिले हुए टुकड़े वे अंश हैं जो प्रश्न से सबसे अधिक मेल खाते हैं। यही मॉडल का संदर्भ बनते हैं।",
        "llm": "भाषा मॉडल को प्रश्न और मिले हुए टुकड़े साथ मिलते हैं, और वह उन्हीं से उत्तर लिखता है।",
        "answer": "उत्तर आधारित होता है: उसे वही कहना चाहिए जो मिले हुए टुकड़ों में है, ताकि स्रोत बताया जा सके।",
        "chunking": ["टुकड़े यहीं बनते हैं, बिल्कुल शुरुआत में।", "सदिश बनाने से पहले हर दस्तावेज़ छोटे भागों में बाँटा जाता है।"],
        "bad": ["अगर खोज गलत टुकड़े ले आए,", "तो मॉडल गलत संदर्भ से उत्तर देता है, और उतने ही विश्वास से।",
                "इसलिए ज़्यादातर गलतियाँ खोज में होती हैं: पहले देखिए क्या मिला।"],
        "hallucination": ["भ्रम, यानी मनगढ़ंत उत्तर, भाषा मॉडल में होता है,", "जब संदर्भ में उत्तर नहीं होता और मॉडल अपनी स्मृति से खाली जगह भर देता है।"],
        "finetune": ["मॉडल को सिखाने का दूसरा तरीका फ़ाइन-ट्यूनिंग है।",
                     "RAG में ज्ञान डेटाबेस में रहता है, जिसे कभी भी बदला जा सकता है, और स्रोत दिखाए जा सकते हैं।",
                     "फ़ाइन-ट्यूनिंग में ज्ञान मॉडल के भीतर चला जाता है: बदलने के लिए दोबारा प्रशिक्षण चाहिए, और यह शैली व व्यवहार के लिए उत्तम है।"],
        "bigger": "यह रहा, बड़ा करके।",
        "cmp_title": "RAG और फ़ाइन-ट्यूनिंग",
        "cmp_rag": ["ज्ञान डेटाबेस में", "बदलाव: दस्तावेज़ जोड़ें", "स्रोत बता सकता है"],
        "cmp_ft": ["ज्ञान मॉडल के भीतर", "बदलाव: दोबारा प्रशिक्षण", "शैली व व्यवहार के लिए"],
        "bad_tag": "गलत टुकड़े → गलत उत्तर",
        "hall_tag": "स्मृति से खाली जगह भरता है",
    },
}

GOAL = "Explain how a RAG system answers from your own documents, and where it can go wrong."
FOLLOW_UPS = ["explain the vector database again", "show where chunking happens", "what if retrieval is wrong",
              "where does hallucination happen", "compare with fine-tuning", "make the vector database bigger",
              "go back one step", "clear it"]


def words(language: str) -> dict:
    return T.get(language) or T["en"]


def labels(language: str) -> dict:
    return LABELS.get(language) or LABELS["en"]


def _geometry(area: dict) -> dict:
    # Sized by whichever of width and height runs out first — room for the diagram and the
    # comparison under it — rather than by screen width alone, which on a 1366 × 768 laptop left
    # the nodes too narrow for their names.
    work = area["work"]
    k = max(0.62, min(1.25, (work["w"] - 56) / 1260, (work["h"] - 60) / (560 + 18 + 250)))
    P = layout.panel(area, 1260 * k, 560 * k, side="center")
    P["y"] = area["work"]["y"] + round(24 * k)
    nw, nh = 186 * k, 76 * k
    gap = (P["w"] - 80 * k - 5 * nw) / 4
    rows = (P["y"] + 96 * k, P["y"] + 96 * k + nh + 170 * k)
    boxes = {}
    for n, (col, row) in GRID.items():
        boxes[n] = {"x": round(P["x"] + 40 * k + col * (nw + gap), 1), "y": round(rows[row], 1),
                    "w": round(nw, 1), "h": round(nh, 1)}
    below = {"x": P["x"], "y": P["y"] + P["h"] + 18 * k, "w": P["w"],
             "h": max(160.0, area["work"]["y"] + area["work"]["h"] - (P["y"] + P["h"] + 18 * k) - 24 * k)}
    return {"k": k, "panel": P, "boxes": boxes, "below": below}


def _node(n: str, box: dict, lab: dict, tl: str, color: str = "primary") -> dict:
    title, sub = lab[n]
    return add(obj(f"n-{n}", "node", x=box["x"], y=box["y"], w=box["w"], h=box["h"], label=title, sub=sub,
                   icon=ICON[n], style={"color": color}, anim=anim("pop", 360, timeline=tl)))


def _edge(eid: str, lab: dict, tl: str, flow: bool = True, delay: float = 0) -> dict:
    _, a, b, bend = next(e for e in EDGES if e[0] == eid)
    return add(obj(eid, "edge", **{"from": f"n-{a}", "to": f"n-{b}"}, label=lab["edges"][eid], bend=bend, flow=flow,
                   anim=anim("draw", 520, delay=delay, timeline=tl)))


def _stop_flow(*eids: str) -> list:
    return [update(e, flow=False) for e in eids]


def plan(language: str, area: dict, reduced_motion: bool = False, intro: Optional[list[str]] = None) -> LessonPlan:
    W, L = words(language), labels(language)
    g = _geometry(area)
    B, P = g["boxes"], g["panel"]
    tl = lambda n: f"rag-{n}"  # noqa: E731
    setup = [create("rag", area.get("index", 0), theme={"palette": "jarvis", "glow": 0.6, "reduced_motion": reduced_motion}),
             add(obj("rag-panel", "panel", x=P["x"], y=P["y"], w=P["w"], h=P["h"], title=L["title"], anim=anim("fade", 260)))]
    steps = [
        Step("1", "Chunking", [Phrase(W["s1"][0], [_node("docs", B["docs"], L, tl(1)), highlight("n-docs", "primary", True, 1600)])]),
        Step("2", "Embedding", [
            Phrase(W["s2"][0], [_node("embed", B["embed"], L, tl(2)), _edge("e-docs-embed", L, tl(2), delay=200)]),
            Phrase(W["s2"][1], [highlight("n-embed", "primary", True, 1600)])]),
        Step("3", "Vector database", [Phrase(W["s3"][0], _stop_flow("e-docs-embed") + [
            _node("vdb", B["vdb"], L, tl(3)), _edge("e-embed-vdb", L, tl(3), delay=200)])]),
        Step("4", "The question", [
            Phrase(W["s4"][0], _stop_flow("e-embed-vdb") + [_node("query", B["query"], L, tl(4))]),
            Phrase(W["s4"][1], [_edge("e-query-embed", L, tl(4)), highlight("n-embed", "primary", True, 1600)])]),
        Step("5", "Similarity search", [
            Phrase(W["s5"][0], _stop_flow("e-query-embed") + [_node("search", B["search"], L, tl(5)),
                                                            _edge("e-embed-search", L, tl(5), delay=150),
                                                            _edge("e-vdb-search", L, tl(5), delay=350)]),
            Phrase(W["s5"][1], [highlight("n-search", "attention", True, 1800)])]),
        Step("6", "Retrieved context", [Phrase(W["s6"][0], _stop_flow("e-embed-search", "e-vdb-search") + [
            _node("retrieved", B["retrieved"], L, tl(6)), _edge("e-search-retrieved", L, tl(6), delay=200)])]),
        Step("7", "Generation", [
            Phrase(W["s7"][0], _stop_flow("e-search-retrieved") + [_node("llm", B["llm"], L, tl(7)),
                                                                  _edge("e-retrieved-llm", L, tl(7), delay=200)]),
            Phrase(W["s7"][1], _stop_flow("e-retrieved-llm") + [_node("answer", B["answer"], L, tl(7), color="confirm"),
                                                               _edge("e-llm-answer", L, tl(7), delay=200),
                                                               highlight("n-answer", "confirm", True, 1800)])]),
        Step("8", "Summary", [
            Phrase(W["close"], _stop_flow("e-llm-answer") + [unhighlight()]),
            Phrase(W["check"], [])]),
    ]
    steps[0].phrases[:0] = [Phrase(line, []) for line in (intro or [W["intro"]])]
    return LessonPlan(topic=TOPIC, language=language if language in T else "en", learning_goal=GOAL, setup=setup,
                      steps=steps, source_context=SourceContext(kind="standalone"),
                      follow_up_options=FOLLOW_UPS, checks_for_understanding=[W["check"]],
                      cleanup_policy={"auto_clear_s": 20.0, "keep": False}, extras={"geometry": g})


# --------------------------------------------------------------------------- follow-ups
def _edges_of(node: str) -> list[str]:
    return [e[0] for e in EDGES if node in (e[1], e[2])]


def follow_up(kind: str, lesson: LessonPlan, language: str, node: Optional[str] = None) -> Optional[Step]:
    """A step that changes the diagram already on screen. None for a kind this lesson lacks."""
    Fw = F.get(language) or F["en"]
    g = lesson.extras["geometry"]
    k = g["k"]
    B = g["boxes"]
    tl = f"rag-fu-{kind}"
    reset = [unhighlight(), unfocus()]
    if kind == "explain" and node in NODES:
        targets = [f"n-{node}", *_edges_of(node)]
        return Step(f"fu-{node}", f"About {node}", [Phrase(Fw[node], reset + [focus(*targets), highlight(f"n-{node}", "primary", True, 2400)])])
    if kind == "chunking":
        b = B["docs"]
        tiles = [add(obj(f"chunk-{i}", "rect", x=round(b["x"] + 8 * k + i * (b["w"] - 16 * k) / 4, 1), y=round(b["y"] - 34 * k, 1),
                         w=round((b["w"] - 16 * k) / 4 - 6 * k, 1), h=round(22 * k, 1), r=4,
                         style={"color": "attention", "width": 1.6, "fill": "attention", "fill_opacity": 0.18, "glow": 0.3},
                         anim=anim("pop", 260, delay=i * 140, timeline=tl), group="fu"))
                 for i in range(4)]
        return Step("fu-chunking", "Where chunking happens", [
            Phrase(Fw["chunking"][0], reset + [focus("n-docs", "e-docs-embed", "chunk-0", "chunk-1", "chunk-2", "chunk-3"),
                                              highlight("n-docs", "attention", True, 2400)] + tiles),
            Phrase(Fw["chunking"][1], [update("e-docs-embed", flow=True)])])
    if kind == "bad_retrieval":
        r = B["retrieved"]
        tag = add(obj("tag-bad", "text", x=round(r["x"] + r["w"] / 2, 1), y=round(r["y"] + r["h"] + 34 * k, 1), text=Fw["bad_tag"],
                      size=round(15 * k, 1), align="middle", weight="600", backing=True, style={"color": "error"},
                      anim=anim("pop", 300, timeline=tl), group="fu"))
        return Step("fu-bad", "When retrieval is wrong", [
            Phrase(Fw["bad"][0], reset + [focus("n-search", "n-retrieved", "e-search-retrieved", "e-retrieved-llm", "n-llm", "tag-bad"),
                                         highlight("n-retrieved", "error", True, 3000)]),
            Phrase(Fw["bad"][1], [tag, highlight("e-retrieved-llm", "error", True, 3000), highlight("n-llm", "attention", False, 3000)]),
            Phrase(Fw["bad"][2], [highlight("n-search", "attention", True, 2400)])])
    if kind == "hallucination":
        m = B["llm"]
        tag = add(obj("tag-hall", "text", x=round(m["x"] + m["w"] / 2, 1), y=round(m["y"] + m["h"] + 34 * k, 1), text=Fw["hall_tag"],
                      size=round(15 * k, 1), align="middle", weight="600", backing=True, style={"color": "attention"},
                      anim=anim("pop", 300, timeline=tl), group="fu"))
        return Step("fu-hallucination", "Where hallucination happens", [
            Phrase(Fw["hallucination"][0], reset + [focus("n-llm", "e-retrieved-llm", "e-llm-answer", "tag-hall"),
                                                   highlight("n-llm", "attention", True, 3000)]),
            Phrase(Fw["hallucination"][1], [tag, highlight("e-retrieved-llm", "attention", True, 2400)])])
    if kind == "compare_finetune":
        Q = dict(g["below"])
        Q["h"] = min(Q["h"], 250 * k)
        col_w = (Q["w"] - 72 * k) / 2
        objs = [add(obj("cmp-panel", "panel", x=Q["x"], y=round(Q["y"], 1), w=Q["w"], h=round(Q["h"], 1), title=Fw["cmp_title"],
                        anim=anim("fade", 280, timeline=tl), group="fu"))]
        cols = []
        for ci, (head, rows, color) in enumerate((("RAG", Fw["cmp_rag"], "confirm"), ("Fine-tuning" if language != "hi-pure" else "फ़ाइन-ट्यूनिंग",
                                                                                      Fw["cmp_ft"], "attention"))):
            x = round(Q["x"] + 36 * k + ci * (col_w + 0), 1)
            items = [add(obj(f"cmp-{ci}-h", "text", x=x, y=round(Q["y"] + 78 * k, 1), text=head, size=round(20 * k, 1), weight="700",
                             style={"color": color}, anim=anim("fade", 260, delay=0, timeline=tl), group="fu"))]
            for ri, row in enumerate(rows):
                items.append(add(obj(f"cmp-{ci}-{ri}", "text", x=x, y=round(Q["y"] + (114 + ri * 34) * k, 1), text="•  " + row,
                                     size=round(16 * k, 1), weight="500", style={"color": "text"},
                                     anim=anim("fade", 260, delay=90 * (ri + 1), timeline=tl), group="fu")))
            cols.append(items)
        return Step("fu-finetune", "RAG vs fine-tuning", [
            Phrase(Fw["finetune"][0], reset + objs),
            Phrase(Fw["finetune"][1], cols[0] + [highlight("n-vdb", "confirm", True, 2000)]),
            Phrase(Fw["finetune"][2], cols[1] + [highlight("n-llm", "attention", True, 2000)])])
    if kind == "bigger" and node in NODES:
        b = B[node]
        f = 1.4
        nb = {"x": round(b["x"] - b["w"] * (f - 1) / 2, 1), "y": round(b["y"] - b["h"] * (f - 1) / 2, 1),
              "w": round(b["w"] * f, 1), "h": round(b["h"] * f, 1)}
        B[node] = nb
        return Step(f"fu-bigger-{node}", "Bigger", [Phrase(Fw["bigger"], reset + [
            update(f"n-{node}", **nb), highlight(f"n-{node}", "primary", True, 1600)])])
    return None


_NODE_WORDS = {
    "docs": r"chunk\w*|document\w*|docs?|दस्तावेज़|टुकड़",
    "embed": r"embedding\w*|embed\w*|एम्बेडिंग|सदिश|vector(?:s|ize)?(?!\s*(?:db|database|store))",
    "vdb": r"vector\s*(?:db|database|store)|database|डेटाबेस|वेक्टर\s*डेटाबेस",
    "query": r"query|question|sawaal|सवाल|प्रश्न",
    "search": r"similarity|search|retriev\w*|खोज",
    "retrieved": r"retrieved|context|संदर्भ",
    "llm": r"language\s*model|llm|model|मॉडल|generat\w*",
    "answer": r"answer|output|उत्तर|जवाब|jawab",
}


def node_named(text: str) -> Optional[str]:
    """Which node the person is talking about, if any. Most specific names first."""
    import re

    s = (text or "").lower()
    for n in ("vdb", "retrieved", "search", "embed", "docs", "llm", "answer", "query"):
        if re.search(_NODE_WORDS[n], s):
            return n
    return None

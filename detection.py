import os
import re
import json
from groq import Groq

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
    return _client


def _llm_call(prompt: str) -> float:
    """Shared Groq call; returns ai_probability or 0.5 on any failure."""
    try:
        response = _get_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=120,
        )
        raw = response.choices[0].message.content.strip()
        match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return max(0.0, min(1.0, float(data.get("ai_probability", 0.5))))
    except Exception:
        pass
    return 0.5


# ─────────────────────────────────────────────────────────────────────────────
# Signal 1 — LLM Classification (Groq llama-3.3-70b-versatile)
# Captures: semantic coherence, stylistic uniformity, hedging language, absence
#           of personal voice, over-polished transitions.
# Blind spot: lightly edited AI output; AI trained on a specific human style;
#             very short texts (< ~50 words).
# ─────────────────────────────────────────────────────────────────────────────
def llm_signal(text: str) -> float:
    prompt = (
        "You are an expert at distinguishing AI-generated text from human-written text.\n\n"
        "Analyze the following text and return ONLY a JSON object with:\n"
        '- "ai_probability": float 0.0–1.0 (0 = definitely human, 1 = definitely AI)\n'
        '- "reasoning": one sentence\n\n'
        "Consider: vocabulary uniformity, hedging phrases (\"furthermore\", \"it is important "
        "to note\"), absence of personal voice, sentence structure regularity, idiomatic "
        "naturalness, and emotional authenticity.\n\n"
        f'Text:\n"""\n{text}\n"""\n\n'
        'Respond with only the JSON. Example: {"ai_probability": 0.82, "reasoning": "..."}'
    )
    return _llm_call(prompt)


# ─────────────────────────────────────────────────────────────────────────────
# Signal 2 — Stylometric Heuristics (pure Python)
# Captures: sentence length variance, type-token ratio (vocabulary diversity),
#           AI filler phrase density, average sentence length clustering.
# Blind spot: formal academic human writing scores AI-like; casual AI output
#             with varied sentence lengths scores human-like.
# ─────────────────────────────────────────────────────────────────────────────
_AI_PHRASES = [
    r"\bit is important to\b", r"\bit is worth noting\b", r"\bit should be noted\b",
    r"\bfurthermore\b", r"\bmoreover\b", r"\bin addition\b",
    r"\bin conclusion\b", r"\bin summary\b", r"\bto summarize\b",
    r"\bin today's\b", r"\bin the modern\b", r"\bas we navigate\b",
    r"\bdelve into\b", r"\bfacilitate\b", r"\bleverage\b",
    r"\btransformative\b", r"\bparadigm\b", r"\bsynergies\b",
    r"\bholistic\b", r"\bproactive\b", r"\bstakeholders\b",
    r"\btailored\b", r"\bseamlessly\b", r"\bcomprehensive\b",
]


def stylometric_signal(text: str) -> float:
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if len(s.strip()) > 3]
    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    if len(words) < 10:
        return 0.5

    sent_lengths = [len(re.findall(r'\b\w+\b', s)) for s in sentences] if sentences else [15]
    mean_len = sum(sent_lengths) / len(sent_lengths)

    if len(sent_lengths) > 1:
        variance = sum((l - mean_len) ** 2 for l in sent_lengths) / len(sent_lengths)
        variance_score = max(0.0, 1.0 - variance / 40.0)
    else:
        variance_score = 0.5

    ttr = len(set(words)) / len(words)
    ttr_score = max(0.0, min(1.0, (0.7 - ttr) / 0.4))

    text_lower = text.lower()
    phrase_count = sum(1 for p in _AI_PHRASES if re.search(p, text_lower))
    phrase_score = min(1.0, (phrase_count / len(words)) * 100 / 3.0)

    if 14 <= mean_len <= 26:
        length_score = 0.6
    elif mean_len < 8 or mean_len > 35:
        length_score = 0.15
    else:
        length_score = 0.35

    return max(0.0, min(1.0,
        variance_score * 0.35 +
        ttr_score      * 0.25 +
        phrase_score   * 0.25 +
        length_score   * 0.15
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Signal 3 — Linguistic Pattern Analysis (pure Python)
# Captures: first-person pronoun absence, nominalization density (AI loves
#           -tion/-ment/-ance suffixes), informal marker absence (contractions,
#           exclamations), passive voice density.
# Blind spot: academic human writers who avoid first-person and use formal
#             register will score AI-like; AI prompts written informally won't.
# ─────────────────────────────────────────────────────────────────────────────
def linguistic_signal(text: str) -> float:
    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    if len(words) < 10:
        return 0.5

    # 1. First-person pronoun density — humans use I/me/my far more than AI
    fp_count = len(re.findall(
        r'\b(i|me|my|mine|myself|we|us|our|ours|ourselves)\b', text.lower()
    ))
    fp_density = fp_count / len(words)
    # > 6% fp → clearly human (score 0); 0% fp → AI-like (score 1)
    fp_score = max(0.0, 1.0 - fp_density / 0.06)

    # 2. Nominalization density — AI overuses abstract noun forms
    nom_count = sum(
        1 for w in words
        if re.search(r'(tion|ment|ance|ence|ity|ism|ness)$', w) and len(w) > 6
    )
    nom_score = min(1.0, (nom_count / len(words)) / 0.08)

    # 3. Informal markers — contractions and exclamations signal human voice
    contraction_count = len(re.findall(
        r"\b\w+n't\b|\b(i'm|i've|i'd|i'll|it's|that's|they're|we're|"
        r"you're|can't|won't|don't|didn't|isn't|wasn't|couldn't|wouldn't)\b",
        text.lower()
    ))
    sentence_count = max(len(re.split(r'[.!?]+', text)), 1)
    # More contractions per sentence → human
    informal_score = max(0.0, 1.0 - (contraction_count / sentence_count) * 2)

    # 4. Passive voice density — "is/was/were/been + past participle"
    passive_count = len(re.findall(
        r'\b(is|are|was|were|be|been|being)\s+\w+ed\b', text.lower()
    ))
    passive_score = min(1.0, (passive_count / sentence_count) / 0.5)

    return max(0.0, min(1.0,
        fp_score      * 0.30 +
        nom_score     * 0.30 +
        informal_score * 0.25 +
        passive_score * 0.15
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Ensemble scoring with conflict detection
# Weights: LLM 50% + Stylometric 30% + Linguistic 20%
# Conflict resolution:
#   - All 3 agree (consensus)  → confidence × 1.10 (boost, cap 1.0)
#   - 2 of 3 agree (majority)  → standard weighted average
#   - No clear majority (conflict) → pull 30% toward 0.5 (uncertain)
# ─────────────────────────────────────────────────────────────────────────────
def _ensemble(llm: float, stylo: float, linguistic: float) -> tuple:
    base = llm * 0.50 + stylo * 0.30 + linguistic * 0.20

    def vote(s):
        if s >= 0.60:  return "ai"
        if s <= 0.40:  return "human"
        return "neutral"

    votes = [vote(llm), vote(stylo), vote(linguistic)]
    ai_votes     = votes.count("ai")
    human_votes  = votes.count("human")

    if ai_votes == 3 or human_votes == 3:
        method = "consensus"
        confidence = min(1.0, base * 1.10)
    elif ai_votes >= 2 or human_votes >= 2:
        method = "majority"
        confidence = base
    else:
        method = "conflict"
        confidence = base * 0.70 + 0.50 * 0.30   # pull toward 0.5

    return round(confidence, 4), method


def _attribution(confidence: float) -> str:
    if confidence >= 0.70:  return "likely_ai"
    if confidence >= 0.40:  return "uncertain"
    return "likely_human"


def _label(confidence: float, attribution: str) -> str:
    pct = f"{confidence:.0%}"
    if attribution == "likely_ai":
        return (
            f"This content was likely generated by an AI writing tool ({pct} confidence). "
            "Our analysis — spanning semantic patterns, structural statistics, and linguistic "
            "markers — detected characteristics consistent with AI-generated text. "
            "If this is incorrect, you can submit an appeal."
        )
    elif attribution == "uncertain":
        return (
            f"We're uncertain whether this content was written by a person or generated "
            f"by AI ({pct} AI confidence). Our three detection signals returned mixed results. "
            "If you believe this classification is wrong, you can provide additional context "
            "through an appeal."
        )
    else:
        human_pct = f"{1.0 - confidence:.0%}"
        return (
            f"This content appears to be human-written ({human_pct} confidence). "
            "Our analysis found consistent human-authorship signals: natural sentence "
            "variation, vocabulary diversity, and authentic personal voice."
        )


def analyze_text(text: str) -> dict:
    llm   = llm_signal(text)
    stylo = stylometric_signal(text)
    ling  = linguistic_signal(text)
    confidence, method = _ensemble(llm, stylo, ling)
    attribution = _attribution(confidence)
    return {
        "content_type":       "text",
        "llm_score":          round(llm,   4),
        "stylometric_score":  round(stylo, 4),
        "linguistic_score":   round(ling,  4),
        "ensemble_method":    method,
        "confidence":         confidence,
        "attribution":        attribution,
        "label":              _label(confidence, attribution),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Multi-modal: Structured Metadata
# Handles: {title, description, tags (list), genre}
# Signal 1 (LLM): evaluates metadata holistically for AI-generation markers
# Signal 2 (Metadata heuristics): tag genericness, description-title overlap,
#           field-length uniformity, description stylometrics
# ─────────────────────────────────────────────────────────────────────────────
_GENERIC_TAGS = {
    "art", "music", "creative", "original", "digital", "modern", "contemporary",
    "abstract", "innovative", "unique", "beautiful", "aesthetic", "design",
    "photography", "illustration", "writing", "poetry", "story", "content",
    "media", "work", "piece", "creation", "project", "collection",
}


def _metadata_llm_signal(metadata: dict) -> float:
    tags_str = ", ".join(metadata.get("tags") or []) or "none"
    text_repr = (
        f"Title: {metadata.get('title', '')}\n"
        f"Description: {metadata.get('description', '')}\n"
        f"Tags: {tags_str}\n"
        f"Genre: {metadata.get('genre', '')}"
    )
    prompt = (
        "You are analyzing structured metadata for a creative work (title, description, tags, genre).\n\n"
        "Assess whether this metadata was generated by AI or written by a real human creator.\n\n"
        "Consider: generic vs. specific tags, polished but impersonal description language, "
        "formulaic title patterns, and whether the metadata feels like it came from a real person "
        "who made this specific work.\n\n"
        f"Metadata:\n{text_repr}\n\n"
        'Respond with only JSON: {"ai_probability": 0.0-1.0, "reasoning": "one sentence"}'
    )
    return _llm_call(prompt)


def _metadata_heuristic_signal(metadata: dict) -> float:
    scores = []
    title       = metadata.get("title", "")
    description = metadata.get("description", "")
    tags        = metadata.get("tags") or []
    genre       = metadata.get("genre", "")

    # 1. Tag genericness — AI uses broad, common tags
    if tags:
        generic_ratio = sum(1 for t in tags if t.lower().strip() in _GENERIC_TAGS) / len(tags)
        scores.append(generic_ratio)

    # 2. Description stylometrics (reuse Signal 2 on the description text)
    if description and len(description.split()) >= 10:
        scores.append(stylometric_signal(description))

    # 3. Description-title word overlap — AI echoes title words in description
    if title and description:
        t_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', title.lower()))
        d_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', description.lower()))
        if t_words:
            overlap = len(t_words & d_words) / len(t_words)
            scores.append(min(1.0, overlap / 0.5))

    # 4. Field-length uniformity — AI fills all fields proportionally
    field_texts = [f for f in [title, description, genre, " ".join(tags)] if f]
    if len(field_texts) > 1:
        lengths = [len(f) for f in field_texts]
        mean_l  = sum(lengths) / len(lengths)
        var_l   = sum((l - mean_l) ** 2 for l in lengths) / len(lengths)
        uniformity = max(0.0, 1.0 - var_l / 2000)
        scores.append(uniformity)

    return sum(scores) / len(scores) if scores else 0.5


def analyze_metadata(metadata: dict) -> dict:
    llm   = _metadata_llm_signal(metadata)
    stylo = _metadata_heuristic_signal(metadata)
    # Linguistic signal not applicable to metadata (no prose to parse)
    confidence = round(llm * 0.65 + stylo * 0.35, 4)
    # Ensemble method is N/A for metadata (only 2 signals)
    attribution = _attribution(confidence)
    return {
        "content_type":       "metadata",
        "llm_score":          round(llm,   4),
        "stylometric_score":  round(stylo, 4),
        "linguistic_score":   None,
        "ensemble_method":    "weighted_2signal",
        "confidence":         confidence,
        "attribution":        attribution,
        "label":              _label(confidence, attribution),
    }

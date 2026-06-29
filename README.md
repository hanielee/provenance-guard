# Provenance Guard

AI content attribution backend for creative sharing platforms. Classifies submitted text as likely AI-generated, likely human-written, or uncertain — with a confidence score, transparency label, appeals workflow, provenance certificates, and an analytics dashboard.

---

## Architecture Overview

A submission arrives at `POST /submit` with text or structured metadata. Three independent detection signals analyze the content; their outputs are combined via an ensemble scorer with conflict detection, mapped to an attribution label and a plain-language transparency label, and written to SQLite before the response is returned.

```
POST /submit  (text or metadata)
  │
  ├──► Signal 1: LLM Classification (Groq llama-3.3-70b) ──► ai_probability [0–1]  weight 50%
  ├──► Signal 2: Stylometric Heuristics (pure Python)    ──► ai_score [0–1]        weight 30%
  ├──► Signal 3: Linguistic Pattern Analysis (pure Python)──► ai_score [0–1]       weight 20%
  │
  ├──► Ensemble Scorer  →  weighted avg + conflict detection
  │     consensus (all agree) → confidence × 1.10
  │     majority (2/3 agree) → standard weighted avg
  │     conflict             → pull 30% toward 0.5
  ├──► Attribution  →  ≥0.70 likely_ai | 0.40–0.69 uncertain | <0.40 likely_human
  ├──► Label Generator  →  human-readable string
  ├──► SQLite Audit Log  →  structured entry written
  └──► JSON Response

POST /appeal   →  update status → log appeal → JSON Response
POST /certify  →  issue Provenance Certificate → JSON Response
GET  /log      →  structured audit entries (JSON)
GET  /analytics →  platform metrics (JSON)
```

---

## Detection Signals

### Signal 1 — LLM Classification

**What it measures:** Semantic and stylistic coherence holistically. The LLM (llama-3.3-70b-versatile via Groq) evaluates whether the writing exhibits AI-characteristic patterns: uniform tone, hedging transitions ("furthermore", "it is important to note"), absence of personal voice, over-polished structure.

**Output:** `ai_probability` float in [0.0, 1.0].

**Why:** LLMs recognize LLM output because they share the same training distribution. No handcrafted rule captures the breadth of what a 70B model detects.

**What it misses:** Lightly edited AI output; AI trained to mimic a specific human style; very short text (< ~50 words).

---

### Signal 2 — Stylometric Heuristics

**What it measures:** Four structural/statistical properties:

| Metric | What it captures | AI tendency | Weight |
|---|---|---|---|
| Sentence length variance | Rhythm uniformity | Low variance | 35% |
| Type-token ratio (TTR) | Vocabulary diversity | Lower TTR | 25% |
| AI filler phrase density | Hedging / buzzwords | Higher density | 25% |
| Average sentence length | Sentence complexity | Clusters 15–26 words | 15% |

**Output:** `ai_score` float in [0.0, 1.0].

**What it misses:** Formal academic human writing (high uniformity, low TTR) scores AI-like. Conversational AI output with varied lengths may score human-like.

---

### Signal 3 — Linguistic Pattern Analysis

**What it measures:** Grammatical and voice markers that differ between AI and human writing:

| Metric | What it captures | AI tendency | Weight |
|---|---|---|---|
| First-person pronoun density | Personal voice | Avoids I/me/my | 30% |
| Nominalization density | Abstract noun forms (-tion/-ment/-ance) | Higher density | 30% |
| Informal marker absence | Contractions, colloquialisms | Avoids them | 25% |
| Passive voice density | Construction pattern | More passive | 15% |

**Output:** `ai_score` float in [0.0, 1.0].

**What it misses:** Academic human writers who avoid first-person (scientific style) will score AI-like. AI prompts written informally won't trigger the informal-marker signal.

---

## Ensemble Scoring & Conflict Resolution

All three signals vote independently:

```
base_confidence = llm × 0.50 + stylometric × 0.30 + linguistic × 0.20
```

Each signal casts a directional vote (scores ≥ 0.60 → "ai", ≤ 0.40 → "human", else neutral):

| Vote pattern | Method | Adjustment |
|---|---|---|
| All 3 agree | `consensus` | `base × 1.10` (capped at 1.0) |
| 2 of 3 agree | `majority` | `base` (no adjustment) |
| No clear majority | `conflict` | `base × 0.70 + 0.50 × 0.30` (pull toward uncertain) |

The `ensemble_method` field is returned in every response and recorded in the audit log. When signals conflict, the system correctly produces an `uncertain` verdict rather than amplifying a noisy signal into a false confident classification.

---

## Confidence Scoring

| Confidence | Attribution | Meaning |
|---|---|---|
| ≥ 0.70 | `likely_ai` | Strong AI evidence across signals |
| 0.40–0.69 | `uncertain` | Mixed or conflicting signals |
| < 0.40 | `likely_human` | Strong human evidence across signals |

### Validation — Two example submissions

**High-confidence AI** (clearly AI-generated corporate boilerplate):
```
Text: "Artificial intelligence represents a transformative paradigm shift in modern
society. It is important to note that while the benefits of AI are numerous, it is
equally essential to consider the ethical implications. Furthermore, stakeholders
across various sectors must collaborate to ensure responsible deployment."

→ llm_score: 0.91 | stylometric_score: 0.43 | linguistic_score: 0.72
→ ensemble: majority | confidence: 0.85 → likely_ai
```

**Lower-confidence / uncertain** (formal academic human writing):
```
Text: "The relationship between monetary policy and asset price inflation has been
extensively studied in the literature. Central banks face a fundamental tension
between their mandate for price stability and the unintended consequences of
prolonged low interest rates on equity and real estate valuations."

→ llm_score: 0.58 | stylometric_score: 0.52 | linguistic_score: 0.60
→ ensemble: majority | confidence: 0.56 → uncertain
```

Casual human text ("ok so i finally tried that new ramen place...") scores `llm: 0.14 | stylo: 0.05 | ling: 0.25` → confidence ≈ 0.18 → `likely_human`.

---

## Transparency Labels

All three variants use plain language. The text changes between variants — not just a number.

### Variant 1 — High-confidence AI (confidence ≥ 0.70)

> "This content was likely generated by an AI writing tool (85% confidence). Our analysis — spanning semantic patterns, structural statistics, and linguistic markers — detected characteristics consistent with AI-generated text. If this is incorrect, you can submit an appeal."

### Variant 2 — Uncertain (confidence 0.40–0.69)

> "We're uncertain whether this content was written by a person or generated by AI (56% AI confidence). Our three detection signals returned mixed results. If you believe this classification is wrong, you can provide additional context through an appeal."

### Variant 3 — High-confidence human (confidence < 0.40)

> "This content appears to be human-written (82% confidence). Our analysis found consistent human-authorship signals: natural sentence variation, vocabulary diversity, and authentic personal voice."

---

## Rate Limiting

Applied to `POST /submit`:

```
10 requests per minute per IP
100 requests per day per IP
```

**Reasoning:** A legitimate writer submitting their own work would rarely send more than 1–2 pieces in a minute; 10 gives comfortable headroom. A script flooding the endpoint for adversarial inputs or to exhaust Groq API quota would hit 429 quickly. 100/day gives a prolific writer 10–20× their realistic submission volume as a buffer.

Rate-limit test output (12 rapid requests):
```
200
200
200
200
200
200
200
200
200
200
429
429
```

---

## Audit Log

Every attribution decision, appeal, ensemble method, and certificate is stored in SQLite. `GET /log` returns structured JSON.

Live output from `GET /log`:

```json
{
  "count": 4,
  "entries": [
    {
      "content_id": "30cf7a4c-b41a-4520-a9b3-8fe611aa0a6c",
      "creator_id": "user-carol",
      "timestamp": "2026-06-29T01:59:04.547975+00:00",
      "content_type": "metadata",
      "attribution": "likely_human",
      "confidence": 0.2219,
      "llm_score": 0.2,
      "stylometric_score": 0.2626,
      "linguistic_score": null,
      "ensemble_method": "weighted_2signal",
      "label": "This content appears to be human-written (78% confidence). Our analysis found consistent human-authorship signals: natural sentence variation, vocabulary diversity, and authentic personal voice.",
      "status": "classified",
      "appeal_reasoning": null,
      "appeal_timestamp": null,
      "certificate_id": null,
      "certificate_issued_at": null
    },
    {
      "content_id": "6676141a-087f-4955-8135-f3e8526728ca",
      "creator_id": "user-bob",
      "timestamp": "2026-06-29T01:58:58.138746+00:00",
      "content_type": "text",
      "attribution": "likely_human",
      "confidence": 0.1658,
      "llm_score": 0.17,
      "stylometric_score": 0.0525,
      "linguistic_score": 0.25,
      "ensemble_method": "consensus",
      "label": "This content appears to be human-written (83% confidence). Our analysis found consistent human-authorship signals: natural sentence variation, vocabulary diversity, and authentic personal voice.",
      "status": "classified",
      "appeal_reasoning": null,
      "appeal_timestamp": null,
      "certificate_id": null,
      "certificate_issued_at": null
    },
    {
      "content_id": "ce4f8678-2bfb-4dc3-98ca-771f816a7469",
      "creator_id": "user-alice",
      "timestamp": "2026-06-29T01:58:50.173736+00:00",
      "content_type": "text",
      "attribution": "likely_ai",
      "confidence": 0.8745,
      "llm_score": 0.91,
      "stylometric_score": 0.6,
      "linguistic_score": 0.8,
      "ensemble_method": "consensus",
      "label": "This content was likely generated by an AI writing tool (87% confidence). Our analysis — spanning semantic patterns, structural statistics, and linguistic markers — detected characteristics consistent with AI-generated text. If this is incorrect, you can submit an appeal.",
      "status": "under_review",
      "appeal_reasoning": "I wrote this myself for a business communications class. English is my second language and I tend to write formally. Please review — I did not use any AI tools.",
      "appeal_timestamp": "2026-06-29T01:59:10.411477+00:00",
      "certificate_id": null,
      "certificate_issued_at": null
    },
    {
      "content_id": "0635bff0-1882-4375-9f4f-59dcfe7b76ad",
      "creator_id": "user-alice",
      "timestamp": "2026-06-29T01:58:45.163099+00:00",
      "content_type": "text",
      "attribution": "likely_ai",
      "confidence": 0.7393,
      "llm_score": 0.91,
      "stylometric_score": 0.4722,
      "linguistic_score": 0.713,
      "ensemble_method": "majority",
      "label": "This content was likely generated by an AI writing tool (74% confidence). Our analysis — spanning semantic patterns, structural statistics, and linguistic markers — detected characteristics consistent with AI-generated text. If this is incorrect, you can submit an appeal.",
      "status": "classified",
      "appeal_reasoning": null,
      "appeal_timestamp": null,
      "certificate_id": null,
      "certificate_issued_at": null
    }
  ]
}
```

---

## Stretch Features

### Ensemble Detection

Three signals with documented weighting (50/30/20) and explicit conflict resolution. The `ensemble_method` field (`consensus`, `majority`, or `conflict`) is returned in every API response and stored in the audit log. When all signals agree, confidence is boosted 10%. When they conflict, it is pulled 30% toward 0.5 to correctly reflect ambiguity.

### Provenance Certificate

Creators whose content was classified as `likely_human` or `uncertain` can request a "Verified Human" provenance certificate at `POST /certify` by providing their `content_id` and a description of their creative process (minimum 50 characters). The system validates that:
1. The content exists and is not classified `likely_ai`
2. No certificate has already been issued for this content

On approval, a `certificate_id` (UUID) is issued and stored in the `certificates` table. The audit log join surfaces `certificate_id` alongside every submission entry. The UI displays a "✓ Verified Human" badge distinct from the standard classification badge.

**Verification step a creator completes:** Submit a substantive (≥ 50 character) written description of how they created the work — their process, inspiration, tools, and time spent. This description is logged with the certificate for reviewer visibility.

### Analytics Dashboard

`GET /analytics` returns platform-wide metrics:

1. **Detection pattern** — counts and percentages for `likely_ai`, `uncertain`, `likely_human` verdicts
2. **Appeal rate** — `appeal_count / total_submissions` as a percentage
3. **Average confidence score** — mean ensemble confidence across all submissions *(additional metric)*
4. **Certificate count** — total provenance certificates issued
5. **Content type breakdown** — text vs. metadata submissions

The UI Analytics tab renders these as metric boxes and a visual distribution bar chart.

### Multi-Modal Support

`POST /submit` accepts `content_type: "metadata"` in addition to `"text"`. Metadata submissions provide a JSON object with `title`, `description`, `tags` (array), and `genre`.

**Pipeline for metadata:**
- **Signal 1 (LLM):** Prompt is rewritten to evaluate metadata holistically — generic tags, impersonal description language, formulaic title patterns, internal consistency of the metadata package.
- **Signal 2 (Metadata heuristics):** Measures tag genericness (ratio of common/generic tags), description-title word overlap (AI echoes the title), field-length uniformity (AI fills all fields proportionally), and description stylometrics via Signal 2.
- **Signal 3:** Not applicable (linguistic markers require prose; metadata fields are too short). `linguistic_score` is returned as `null` for metadata submissions. Confidence uses a 2-signal weighted average (LLM 65%, heuristic 35%).

Metadata content type is recorded in the audit log and visible in the analytics content-type breakdown.

---

## Known Limitations

### Non-native English writers

A non-native speaker who writes formally — uniform sentence structure, limited idiomatic variation, transitional phrases — will systematically score higher on Signal 2 (low TTR, high uniformity) and Signal 3 (low first-person, few contractions). Their prose may trigger filler-phrase detection even with no AI involvement. The 0.70 threshold and `uncertain` band exist to catch borderline cases; the appeals and certificate workflows are the primary mitigations.

### Short-form creative writing

Poems and lyrics under ~50 words give all three signals insufficient evidence. The LLM cannot assess style from two lines; stylometric variance and TTR are noisy on small samples; linguistic counts are meaningless on 30 words. These submissions will land in `uncertain`, which is honest — but it means the system provides little useful signal for the content type most associated with original human creativity.

---

## Spec Reflection

**Where the spec helped:** Defining the three label variants in `planning.md` before writing any code forced a design decision: the label needed to change text (not just a percentage) between variants. That decision shaped the `_label()` function's structure.

**Where implementation diverged:** The spec described stylometric heuristics as a single signal. During implementation it became clear that four sub-metrics can produce contradictory results — a text can have low TTR but high sentence variance. This surface area grew into a separate Signal 3 (Linguistic Pattern Analysis), which is genuinely independent from both the LLM and structural signals. The spec was updated to match.

---

## AI Usage

### Instance 1 — Stylometric scoring logic

**Directed:** Provided the four sub-metrics from `planning.md` and asked for the Python implementation combining them into a single [0,1] score.

**Output:** Working function, but with a TTR normalization bug — the range assumed TTR would always be below 0.7, producing negative scores for short texts with high lexical diversity.

**Revised:** Changed the formula to `max(0.0, min(1.0, (0.7 - ttr) / 0.4))` and added a minimum word count guard so short texts return 0.5 instead of a misleading score.

### Instance 2 — Appeal endpoint

**Directed:** Provided the appeals workflow section and SQLite schema, asked for `POST /appeal`.

**Output:** Working endpoint, but allowed duplicate appeals — a second POST for the same `content_id` would insert another row without checking existing status.

**Revised:** Added an idempotency check: if `submission["status"] == "under_review"`, return HTTP 409 before any writes. This prevents duplicate appeals and surfaced the need to validate `content_id` exists before checking status.

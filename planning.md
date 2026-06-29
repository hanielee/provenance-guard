# Provenance Guard — Planning Document

## Architecture

### Diagram

```
SUBMISSION FLOW
═══════════════

POST /submit
  │
  ├──► [Signal 1] LLM Classification
  │    Model: llama-3.3-70b-versatile (Groq)
  │    Input: raw text
  │    Output: ai_probability [0.0–1.0]
  │    Weight: 65%
  │
  ├──► [Signal 2] Stylometric Heuristics
  │    Input: raw text
  │    Output: ai_score [0.0–1.0]
  │    Weight: 35%
  │
  ├──► Confidence Scorer
  │    confidence = LLM×0.65 + Stylo×0.35
  │    ≥0.70 → likely_ai
  │    0.40–0.69 → uncertain
  │    <0.40 → likely_human
  │
  ├──► Transparency Label Generator
  │    Maps (attribution, confidence) → label text string
  │
  ├──► Audit Log (SQLite)
  │    Writes: content_id, creator_id, timestamp, scores, status
  │
  └──► JSON Response


APPEAL FLOW
═══════════

POST /appeal
  │
  ├──► Validate content_id exists in SQLite
  │
  ├──► Update status: "classified" → "under_review"
  │
  ├──► Log appeal (appeals table: content_id, reasoning, timestamp)
  │
  └──► JSON Response (status: under_review)
```

### Narrative

A submission arrives at `POST /submit` with `text` and `creator_id`. Both signals run against the raw text; their outputs are combined into a single confidence score using a weighted average. The score is mapped to an attribution label and a human-readable transparency label. Everything is written to SQLite before the response is returned. Appeals arrive at `POST /appeal`, update the submission's status to `under_review`, and write the creator's reasoning into a linked `appeals` table so both the original decision and the dispute are visible in the same log query.

---

## Detection Signals

### Signal 1 — LLM Classification (Groq, llama-3.3-70b-versatile)

**What it measures:** Semantic and stylistic coherence holistically. The model evaluates whether the writing feels like it was produced by a language model: uniform tone, hedging transitions, absence of personal voice, over-polished structure.

**Output:** A float `ai_probability` in [0.0, 1.0].

**Why this signal:** LLMs can recognize the characteristic patterns of LLM output because they share the same underlying training distribution. No hand-crafted rule set can match the breadth of what a 70B model picks up.

**What it misses:** Lightly edited AI output. AI trained to mimic a specific human style. Very short text (under ~50 words) where the model cannot gather enough evidence. It also has stochastic variance — the same text can score ±0.05 across calls.

---

### Signal 2 — Stylometric Heuristics (pure Python)

**What it measures:** Four structural/statistical properties:

| Metric | What it captures | AI tendency |
|---|---|---|
| Sentence length variance | Uniformity of rhythm | Low variance |
| Type-token ratio (TTR) | Vocabulary diversity | Lower TTR |
| AI filler phrase density | Hedging, buzzword use | Higher density |
| Average sentence length | Sentence complexity | Clusters 15–26 words |

**Output:** A float `ai_score` in [0.0, 1.0], weighted combination of the four sub-metrics.

**Why this signal:** Entirely independent of the LLM signal — it measures structure, not semantics. Two independent signals that agree increase confidence; when they diverge, the uncertain zone is appropriate.

**What it misses:** Formal academic human writing (high uniformity, low TTR) can score AI-like. Conversational AI output with injected errors and varied sentence lengths may score human-like.

---

## Uncertainty Representation

### Score → Attribution mapping

| Confidence range | Attribution | Meaning |
|---|---|---|
| ≥ 0.70 | `likely_ai` | Strong evidence of AI generation |
| 0.40–0.69 | `uncertain` | Mixed signals; cannot confidently classify |
| < 0.40 | `likely_human` | Strong evidence of human authorship |

### Combining signals

```
confidence = llm_score × 0.65 + stylometric_score × 0.35
```

LLM carries more weight because it captures semantics; stylometrics provides an independent structural check. A 0.5 confidence score means the signals are genuinely in conflict or both neutral — the "uncertain" label is not a failure state, it is an honest representation of ambiguity.

### Calibration validation

Validated by testing four deliberate inputs (see Milestone 4):
- Clearly AI-generated corporate text → confidence ≥ 0.75
- Clearly human casual writing → confidence ≤ 0.35
- Formal academic human text → typically 0.45–0.60 (uncertain, as expected)
- Lightly edited AI → typically 0.55–0.70 (uncertain to likely_ai)

Thresholds were tuned so that a false positive (human labeled AI) falls into the uncertain band rather than the AI band in borderline cases. This reflects the design principle: false positives on a creative platform are more harmful than false negatives.

---

## Transparency Label Design

All three variants are written in plain language — no technical jargon. Each variant changes based on both attribution and confidence score.

### Variant 1 — High-confidence AI (confidence ≥ 0.70)

```
"This content was likely generated by an AI writing tool (XX% confidence).
Our analysis detected patterns consistent with AI-generated text, including
writing uniformity, structural regularity, and common AI phrasing.
If this is incorrect, you can submit an appeal."
```

### Variant 2 — Uncertain (confidence 0.40–0.69)

```
"We're uncertain whether this content was written by a person or generated
by AI (XX% AI confidence). The writing shows a mix of human and AI
characteristics. If you believe this classification is wrong, you can
provide additional context through an appeal."
```

### Variant 3 — High-confidence human (confidence < 0.40)

```
"This content appears to be human-written (XX% confidence).
Our analysis found patterns consistent with authentic human authorship,
including natural variation in sentence structure, vocabulary diversity,
and personal voice."
```

The XX% value is populated dynamically from the confidence score. Labels change text — not just a number — to make the distinction legible to a non-technical reader.

---

## Appeals Workflow

### Who can appeal

Any creator who submitted content and has a `content_id` in the response.

### What they provide

- `content_id`: identifier linking back to the original classification
- `creator_reasoning`: free-text explanation of why the classification is wrong

### What the system does

1. Validates `content_id` exists and has not already been appealed
2. Updates `status` in the `submissions` table from `"classified"` to `"under_review"`
3. Inserts a row into the `appeals` table with `content_id`, `creator_reasoning`, and `timestamp`
4. Returns confirmation JSON

### What a human reviewer sees (via GET /log)

A log entry with the original attribution, confidence, both signal scores, the current status (`under_review`), and the full appeal reasoning text. Both the decision and its dispute are in the same record — no separate queue to consult.

### Automated re-classification

Not implemented. The spec does not require it, and automated re-classification on creator request would create an adversarial loop.

---

## Anticipated Edge Cases

### Edge case 1: Non-native English writers

A non-native English speaker may use formal sentence structures, limited vocabulary variety, and transitional phrases that closely resemble AI output patterns. The stylometric signal (low TTR, high uniformity) would push their score toward `uncertain` or `likely_ai` even for entirely human writing. Mitigation: the `uncertain` band exists precisely for this, and the label explicitly invites appeals. The threshold of 0.70 for `likely_ai` is deliberately conservative.

### Edge case 2: Short poems or lyrics

Short-form creative writing (< 50 words) gives both signals very little to work with. The LLM lacks context; stylometric metrics (variance, TTR) are noisy on small samples. The system will typically return `uncertain` for short texts, which is the honest answer. A minimum of 20 characters is enforced, but very short inputs should not be trusted.

### Edge case 3: Quoted or collaborative content

A human piece that quotes extensively from another source (a journalist quoting an official statement, a blogger quoting a product description) may embed AI-like passages. The detection signals cannot distinguish embedded quotes from the author's own voice. This will inflate the AI probability of otherwise human writing.

### Edge case 4: AI text with heavy human editing

If a writer uses AI as a first draft and rewrites substantially, the stylometric signal will reflect the final human-edited structure, but the LLM signal may still detect residual AI patterns in word choice. These submissions are likely to land in the `uncertain` band — correct behavior, since the content is genuinely hybrid.

---

## AI Tool Plan

### M3 — Submission endpoint + Signal 1

**Spec sections provided:** Detection Signals (Signal 1), Architecture diagram, Uncertainty Representation (output format)

**What to generate:** Flask app skeleton with `POST /submit` route stub; `llm_signal()` function with Groq API call and JSON response parsing; SQLite schema and `log_submission()` helper.

**Verification:** Call `llm_signal()` directly on 3 test strings and inspect raw scores before wiring into the endpoint. Confirm `content_id` appears in both the API response and the log.

---

### M4 — Signal 2 + confidence scoring

**Spec sections provided:** Detection Signals (Signal 2), Uncertainty Representation (score formula and thresholds), Architecture diagram

**What to generate:** `stylometric_signal()` function implementing the four sub-metrics; `_combine_scores()` function matching the 65/35 weighting; updated audit log schema adding `stylometric_score`.

**Verification:** Run both signals independently on all four test inputs (clearly AI, clearly human, formal human, lightly edited AI). Check that combined scores fall in the expected bands. If a signal misbehaves, print individual sub-metric scores to isolate the problem.

---

### M5 — Production layer

**Spec sections provided:** Transparency Label Design (all three variants with exact text), Appeals Workflow, Architecture diagram, Rate limiting reasoning

**What to generate:** `_label()` function mapping (attribution, confidence) → label string; `POST /appeal` endpoint with status update and appeal log insert; Flask-Limiter configuration with `10 per minute;100 per day`.

**Verification:** Test all three label variants by submitting inputs that produce each band. Test appeal endpoint with a `content_id` from an earlier submission and confirm `GET /log` shows `under_review` status and `appeal_reasoning` populated. Run the 12-request rate-limit test and confirm 429 responses appear at request 11+.

---

## Stretch Feature Plans

### S1 — Ensemble Detection

**Signal 3 — Linguistic Pattern Analysis** measures grammatical and voice markers that are independent of both semantic (Signal 1) and structural (Signal 2) signals:
- First-person pronoun density (humans use I/me/my; AI avoids it)
- Nominalization density (AI overuses -tion/-ment/-ance suffix forms)
- Informal marker absence (contractions, exclamations signal human voice)
- Passive voice density (AI uses more passive constructions)

**Weighting:** LLM 50% + Stylometric 30% + Linguistic 20%

**Conflict resolution:**
- All 3 votes agree → `consensus`: confidence × 1.10 (cap 1.0)
- 2 of 3 votes agree → `majority`: standard weighted average
- No clear majority → `conflict`: confidence × 0.70 + 0.50 × 0.30 (pull toward uncertain)

Vote thresholds: score ≥ 0.60 → "ai", score ≤ 0.40 → "human", else "neutral". `ensemble_method` is returned in every response and logged.

---

### S2 — Provenance Certificate

**Design:** A "Verified Human" certificate is a record issued to creators who (a) received a non-`likely_ai` classification and (b) submitted a substantive description of their creative process (≥ 50 characters).

**Verification step:** Creator calls `POST /certify` with their `content_id` and `process_description`. The system checks:
1. `content_id` exists and status is not `likely_ai`
2. No certificate already issued for this content
3. `process_description` meets minimum length

**Certificate storage:** `certificates` table with `certificate_id` (UUID), `content_id`, `creator_id`, `process_description`, `issued_at`. The audit log join surfaces `certificate_id` alongside submissions.

**Display distinction:** A "✓ Verified Human" badge distinct from the standard attribution badges (different border/style in UI). The standard labels read "This content appears to be human-written" — the certificate badge reads "Verified Human" and indicates the creator completed an additional verification step.

---

### S3 — Analytics Dashboard

`GET /analytics` returns:
1. **Verdict distribution** (count + % for each attribution) — detection pattern
2. **Appeal rate** (appeal_count / total_submissions as %) — creator dispute frequency
3. **Average confidence score** — calibration health indicator *(additional metric)*
4. Certificate count and content-type breakdown as supplementary metrics

UI renders verdict distribution as percentage bars, plus metric boxes for the other values.

---

### S4 — Multi-Modal Support

**Second content type: structured metadata** (`content_type: "metadata"`)
Accepts: `{title, description, tags[], genre}` — the metadata package a creator provides when uploading a creative work to a platform.

**Signal 1 (LLM):** Rewritten prompt evaluates the metadata package holistically — generic vs. specific tags, impersonal description language, formulaic title patterns, internal consistency.

**Signal 2 (Metadata heuristics):**
- Tag genericness ratio (common tags like "art", "music", "creative" vs. specific ones)
- Description-title word overlap (AI echoes title words in description)
- Field-length uniformity (AI fills all fields proportionally)
- Description stylometrics (reuse Signal 2 on the description text)

**Signal 3:** Not applicable to metadata (fields too short for linguistic pattern analysis). `linguistic_score` returned as `null`. Confidence uses 2-signal weighted average (LLM 65%, heuristic 35%).

**Audit log:** `content_type` field records "text" or "metadata" for every entry. Analytics shows text vs. metadata submission breakdown.

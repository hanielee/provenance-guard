import os
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

from database import (
    get_analytics, get_certificate, get_log_entries, get_submission,
    init_db, log_appeal, log_certificate, log_submission, update_status,
)
from detection import analyze_metadata, analyze_text

load_dotenv()

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

init_db()


@app.route("/")
def index():
    return render_template("index.html")


# ─────────────────────────────────────────────────────────────────────────────
# POST /submit  — text or structured metadata
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    creator_id   = (data.get("creator_id") or "").strip()
    content_type = (data.get("content_type") or "text").strip().lower()

    if not creator_id:
        return jsonify({"error": "Missing required field: creator_id"}), 400
    if content_type not in ("text", "metadata"):
        return jsonify({"error": "content_type must be 'text' or 'metadata'"}), 400

    if content_type == "text":
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Missing required field: text"}), 400
        if len(text) < 20:
            return jsonify({"error": "Text too short for analysis (minimum 20 characters)"}), 400
        if len(text) > 10000:
            return jsonify({"error": "Text too long (maximum 10,000 characters)"}), 400
        result = analyze_text(text)
        content_text = text

    else:  # metadata
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            return jsonify({"error": "metadata must be a JSON object with title, description, tags, genre"}), 400
        if not metadata.get("title") and not metadata.get("description"):
            return jsonify({"error": "metadata must include at least title or description"}), 400
        if isinstance(metadata.get("tags"), str):
            metadata["tags"] = [t.strip() for t in metadata["tags"].split(",") if t.strip()]
        result = analyze_metadata(metadata)
        import json as _json
        content_text = _json.dumps(metadata)

    content_id = str(uuid.uuid4())
    timestamp  = datetime.now(timezone.utc).isoformat()

    log_submission({
        "content_id":        content_id,
        "creator_id":        creator_id,
        "timestamp":         timestamp,
        "content_type":      result["content_type"],
        "content_text":      content_text,
        "attribution":       result["attribution"],
        "confidence":        result["confidence"],
        "llm_score":         result["llm_score"],
        "stylometric_score": result["stylometric_score"],
        "linguistic_score":  result.get("linguistic_score"),
        "ensemble_method":   result["ensemble_method"],
        "label":             result["label"],
        "status":            "classified",
    })

    return jsonify({
        "content_id":        content_id,
        "content_type":      result["content_type"],
        "attribution":       result["attribution"],
        "confidence":        result["confidence"],
        "llm_score":         result["llm_score"],
        "stylometric_score": result["stylometric_score"],
        "linguistic_score":  result.get("linguistic_score"),
        "ensemble_method":   result["ensemble_method"],
        "label":             result["label"],
        "status":            "classified",
        "timestamp":         timestamp,
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /appeal
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    content_id = (data.get("content_id") or "").strip()
    reasoning  = (data.get("creator_reasoning") or "").strip()

    if not content_id or not reasoning:
        return jsonify({"error": "Missing required fields: content_id, creator_reasoning"}), 400

    submission = get_submission(content_id)
    if not submission:
        return jsonify({"error": "content_id not found"}), 404
    if submission["status"] == "under_review":
        return jsonify({"error": "An appeal has already been submitted for this content"}), 409

    timestamp = datetime.now(timezone.utc).isoformat()
    update_status(content_id, "under_review")
    log_appeal(content_id, reasoning, timestamp)

    return jsonify({
        "content_id": content_id,
        "status":     "under_review",
        "message":    "Your appeal has been received. This content is now under review.",
        "timestamp":  timestamp,
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /certify  — issue a Provenance Certificate
# Requirements: content must not be likely_ai; not already certified
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/certify", methods=["POST"])
def certify():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    content_id  = (data.get("content_id") or "").strip()
    description = (data.get("process_description") or "").strip()

    if not content_id or not description:
        return jsonify({"error": "Missing required fields: content_id, process_description"}), 400
    if len(description) < 50:
        return jsonify({"error": "process_description must be at least 50 characters"}), 400

    submission = get_submission(content_id)
    if not submission:
        return jsonify({"error": "content_id not found"}), 404
    if submission["attribution"] == "likely_ai":
        return jsonify({
            "error": "Certificate cannot be issued: content was classified as likely AI-generated. Submit an appeal first."
        }), 409
    if get_certificate(content_id):
        return jsonify({"error": "A certificate has already been issued for this content"}), 409

    cert_id   = str(uuid.uuid4())
    issued_at = datetime.now(timezone.utc).isoformat()
    log_certificate(cert_id, content_id, submission["creator_id"], description, issued_at)

    return jsonify({
        "certificate_id": cert_id,
        "content_id":     content_id,
        "creator_id":     submission["creator_id"],
        "status":         "verified_human",
        "message":        "Provenance certificate issued. This content is now marked as verified human-authored.",
        "issued_at":      issued_at,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /analytics
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/analytics", methods=["GET"])
def analytics():
    return jsonify(get_analytics())


# ─────────────────────────────────────────────────────────────────────────────
# GET /log
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/log", methods=["GET"])
def get_log():
    limit = max(1, min(request.args.get("limit", 50, type=int), 200))
    entries = get_log_entries(limit)
    return jsonify({"entries": entries, "count": len(entries)})


@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({
        "error":       "Rate limit exceeded",
        "message":     "Submission limit reached: 10 per minute or 100 per day.",
        "retry_after": "Try again in 60 seconds.",
    }), 429


if __name__ == "__main__":
    app.run(debug=True)

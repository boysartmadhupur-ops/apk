"""Flask backend for Boys Art Mobile Skin Cutting Plotter."""
import base64
import os
import threading
import time
import uuid
from collections import OrderedDict

from flask import Flask, jsonify, request, abort, Response
from flask_cors import CORS

import drive
import payments
from config import API_KEY, PORT, DATA_DIR

app = Flask(__name__)
CORS(app)

os.makedirs(DATA_DIR, exist_ok=True)

# In-memory job queue (FIFO, dedup by signature, no duplicates)
_QUEUE_LOCK = threading.Lock()
_QUEUE: "OrderedDict[str, dict]" = OrderedDict()
_RECENT_SIGS: dict = {}  # signature -> timestamp, prevents duplicate cuts
_DEDUP_WINDOW = 5.0  # seconds


def _check_auth():
    key = request.headers.get("X-API-Key", "")
    if key != API_KEY:
        abort(401, description="Invalid API key")


@app.before_request
def auth_gate():
    if request.path in ("/", "/health"):
        return None
    _check_auth()


@app.route("/")
def root():
    return jsonify({"service": "boys-art-backend", "status": "ok"})


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/get-templates")
def get_templates():
    try:
        templates = drive.list_templates()
        approved = set(payments.list_approved())
        for t in templates:
            t["paid"] = t["id"] in approved
        return jsonify({"templates": templates})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/download-template")
def download_template():
    file_id = request.args.get("id", "")
    if not file_id:
        return jsonify({"error": "missing id"}), 400
    try:
        data = drive.download_template(file_id)
        return Response(data, mimetype="application/octet-stream")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/payment-status")
def payment_status():
    file_id = request.args.get("id", "")
    return jsonify({"id": file_id, "paid": payments.is_paid(file_id)})


@app.route("/approve-payment", methods=["POST"])
def approve_payment():
    body = request.get_json(force=True, silent=True) or {}
    file_id = body.get("id", "")
    if not file_id:
        return jsonify({"error": "missing id"}), 400
    payments.approve(file_id)
    return jsonify({"id": file_id, "paid": True})


@app.route("/cut", methods=["POST"])
def cut():
    body = request.get_json(force=True, silent=True) or {}
    template_id = body.get("template_id", "")
    if not template_id:
        return jsonify({"error": "missing template_id"}), 400
    if not payments.is_paid(template_id):
        return jsonify({"error": "payment required"}), 402

    rotation = int(body.get("rotation", 0))
    flip_h = bool(body.get("flip_h", False))
    flip_v = bool(body.get("flip_v", False))
    speed = int(body.get("speed", 40))
    force = int(body.get("force", 80))
    com_port = body.get("com_port", "")

    # Optional inline HPGL data (base64). Else bridge will download it.
    hpgl_b64 = body.get("hpgl_b64")

    sig = f"{template_id}|{rotation}|{flip_h}|{flip_v}|{speed}|{force}"
    now = time.time()

    with _QUEUE_LOCK:
        # Dedup: ignore identical job within a small window
        last = _RECENT_SIGS.get(sig, 0)
        if now - last < _DEDUP_WINDOW:
            return jsonify({"queued": False, "reason": "duplicate"}), 200
        _RECENT_SIGS[sig] = now
        # Prevent same template already in queue
        for j in _QUEUE.values():
            if j["template_id"] == template_id and j["status"] == "queued":
                return jsonify({"queued": False, "reason": "already queued"}), 200

        job_id = uuid.uuid4().hex
        _QUEUE[job_id] = {
            "id": job_id,
            "template_id": template_id,
            "rotation": rotation,
            "flip_h": flip_h,
            "flip_v": flip_v,
            "speed": speed,
            "force": force,
            "com_port": com_port,
            "hpgl_b64": hpgl_b64,
            "status": "queued",
            "created": now,
        }
    return jsonify({"queued": True, "job_id": job_id})


@app.route("/next-job")
def next_job():
    with _QUEUE_LOCK:
        for job in _QUEUE.values():
            if job["status"] == "queued":
                job["status"] = "in_progress"
                # Inline HPGL if not provided by client
                payload = dict(job)
                if not payload.get("hpgl_b64"):
                    try:
                        data = drive.download_template(job["template_id"])
                        payload["hpgl_b64"] = base64.b64encode(data).decode()
                    except Exception as e:
                        payload["hpgl_b64"] = None
                        payload["error"] = str(e)
                return jsonify({"job": payload})
    return jsonify({"job": None})


@app.route("/job-done", methods=["POST"])
def job_done():
    body = request.get_json(force=True, silent=True) or {}
    job_id = body.get("id", "")
    success = bool(body.get("success", True))
    with _QUEUE_LOCK:
        if job_id in _QUEUE:
            _QUEUE[job_id]["status"] = "done" if success else "failed"
    return jsonify({"ok": True})


@app.route("/abort-cut", methods=["POST"])
def abort_cut():
    with _QUEUE_LOCK:
        for j in _QUEUE.values():
            if j["status"] in ("queued", "in_progress"):
                j["status"] = "aborted"
    return jsonify({"ok": True})


@app.route("/jobs")
def jobs():
    with _QUEUE_LOCK:
        return jsonify({"jobs": list(_QUEUE.values())})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)

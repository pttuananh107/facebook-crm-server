import os
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from supabase import create_client, Client

app = Flask(__name__)

VERIFY_TOKEN = "lagtuz2026"
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HOT_KEYWORDS = ["giá", "bao nhiêu", "mua", "đặt hàng", "order", "báo giá", "chi phí", "phí", "mất bao nhiêu"]
WARM_KEYWORDS = ["thông tin", "tư vấn", "hỏi", "như thế nào", "có không", "được không", "hợp tác"]


def get_sender_name(sender_id):
    try:
        resp = requests.get(
            f"https://graph.facebook.com/{sender_id}",
            params={"fields": "name", "access_token": PAGE_ACCESS_TOKEN},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("name")
    except Exception:
        return None


def score_lead(text):
    if not text or not text.strip():
        return "Cold"
    text_lower = text.lower()
    if any(kw in text_lower for kw in HOT_KEYWORDS):
        return "Hot"
    if any(kw in text_lower for kw in WARM_KEYWORDS):
        return "Warm"
    return "Cold"


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def receive_webhook():
    data = request.get_json(silent=True)
    if not data:
        return "Bad Request", 400

    if data.get("object") == "page":
        for entry in data.get("entry", []):
            page_id = entry.get("id")
            for messaging in entry.get("messaging", []):
                sender_id = messaging.get("sender", {}).get("id")
                timestamp = messaging.get("timestamp")
                message = messaging.get("message", {})
                text = message.get("text")

                sender_name = get_sender_name(sender_id) if sender_id else None

                record = {
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                    "page_id": page_id,
                    "timestamp": timestamp,
                    "received_at": datetime.utcnow().isoformat() + "Z",
                    "message_id": message.get("mid"),
                    "text": text,
                    "attachments": message.get("attachments"),
                    "score": score_lead(text),
                }

                supabase.table("messages").insert(record).execute()

    return "EVENT_RECEIVED", 200


@app.route("/messages", methods=["GET"])
def get_messages():
    score_filter = request.args.get("score")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    offset = (page - 1) * per_page

    query = supabase.table("messages").select("*", count="exact")
    if score_filter:
        query = query.ilike("score", score_filter)
    result = query.range(offset, offset + per_page - 1).execute()

    return jsonify({
        "total": result.count,
        "page": page,
        "per_page": per_page,
        "messages": result.data,
    })


@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "service": "Facebook CRM Webhook Server"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

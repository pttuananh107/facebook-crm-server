import json
import os
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

VERIFY_TOKEN = "lagtuz2026"
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
MESSAGES_FILE = "messages.json"

HOT_KEYWORDS = ["giá", "bao nhiêu", "mua", "đặt hàng", "order", "báo giá", "chi phí", "phí", "mất bao nhiêu"]
WARM_KEYWORDS = ["thông tin", "tư vấn", "hỏi", "như thế nào", "có không", "được không", "hợp tác"]


def load_messages():
    if not os.path.exists(MESSAGES_FILE):
        return []
    with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_messages(messages):
    with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)


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

                record = {
                    "sender_id": sender_id,
                    "page_id": page_id,
                    "timestamp": timestamp,
                    "received_at": datetime.utcnow().isoformat() + "Z",
                    "message_id": message.get("mid"),
                    "text": text,
                    "attachments": message.get("attachments"),
                    "score": score_lead(text),
                }

                messages = load_messages()
                messages.append(record)
                save_messages(messages)

    return "EVENT_RECEIVED", 200


@app.route("/messages", methods=["GET"])
def get_messages():
    messages = load_messages()
    score_filter = request.args.get("score")
    if score_filter:
        messages = [m for m in messages if m.get("score", "").lower() == score_filter.lower()]
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    start = (page - 1) * per_page
    end = start + per_page
    return jsonify({
        "total": len(messages),
        "page": page,
        "per_page": per_page,
        "messages": messages[start:end],
    })


@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "service": "Facebook CRM Webhook Server"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

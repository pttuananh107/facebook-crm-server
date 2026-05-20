import os
import uuid
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify
from supabase import create_client, Client

app = Flask(__name__)

VERIFY_TOKEN = "lagtuz2026"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HOT_KEYWORDS = ["giá", "bao nhiêu", "mua", "đặt hàng", "order", "báo giá", "chi phí", "phí", "mất bao nhiêu"]
WARM_KEYWORDS = ["thông tin", "tư vấn", "hỏi", "như thế nào", "có không", "được không", "hợp tác"]

TZ7 = timezone(timedelta(hours=7))


def get_page_token(page_id):
    try:
        result = supabase.table("pages").select("access_token").eq("page_id", page_id).single().execute()
        return result.data.get("access_token") if result.data else None
    except Exception:
        return None


def get_sender_name(sender_id, page_token):
    try:
        resp = requests.get(
            "https://graph.facebook.com/v19.0/me/conversations",
            params={"fields": "participants,id", "access_token": page_token},
            timeout=5,
        )
        resp.raise_for_status()
        conversations = resp.json().get("data", [])
        for conv in conversations:
            participants = conv.get("participants", {}).get("data", [])
            participant_ids = [p.get("id") for p in participants]
            if sender_id in participant_ids:
                for p in participants:
                    if p.get("id") != sender_id:
                        continue
                    return p.get("name")
        return None
    except Exception:
        return None


def score_lead(messages):
    customer_text = " ".join(m.get("text", "") for m in messages if m.get("role") == "customer")
    if not customer_text.strip():
        return "Cold"
    text_lower = customer_text.lower()
    if any(kw in text_lower for kw in HOT_KEYWORDS):
        return "Hot"
    if any(kw in text_lower for kw in WARM_KEYWORDS):
        return "Warm"
    return "Cold"


def find_or_create_conversation(sender_id, page_id, sender_name):
    cutoff = (datetime.now(TZ7) - timedelta(days=3)).isoformat()
    try:
        result = (
            supabase.table("conversations")
            .select("*")
            .eq("sender_id", sender_id)
            .eq("page_id", page_id)
            .gte("last_message_at", cutoff)
            .order("last_message_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]
    except Exception:
        pass

    now = datetime.now(TZ7).isoformat()
    new_conv = {
        "conversation_id": str(uuid.uuid4()),
        "sender_id": sender_id,
        "sender_name": sender_name,
        "page_id": page_id,
        "messages": [],
        "message_count": 0,
        "score": "Cold",
        "last_message_at": now,
        "created_at": now,
        "updated_at": now,
    }
    result = supabase.table("conversations").insert(new_conv).execute()
    return result.data[0]


def append_message_to_conversation(conv, new_msg):
    messages = list(conv.get("messages") or [])
    messages.append(new_msg)
    now = datetime.now(TZ7).isoformat()
    supabase.table("conversations").update({
        "messages": messages,
        "message_count": len(messages),
        "score": score_lead(messages),
        "last_message_at": now,
        "updated_at": now,
    }).eq("conversation_id", conv["conversation_id"]).execute()


def extract_referral_info(referral):
    if not referral:
        return None, None, None
    ad_id = referral.get("ad_id")
    ads_ctx = referral.get("ads_context_data", {})
    ad_title = ads_ctx.get("ad_title")
    referral_source = referral.get("source")
    return ad_id, ad_title, referral_source


def update_conversation_ad_info(conv, ad_id, ad_title, referral_source):
    if conv.get("ad_id"):
        return
    supabase.table("conversations").update({
        "ad_id": ad_id,
        "ad_title": ad_title,
        "referral_source": referral_source,
        "updated_at": datetime.now(TZ7).isoformat(),
    }).eq("conversation_id", conv["conversation_id"]).execute()


def find_conversation_by_thread_id(thread_id, page_id):
    try:
        result = (
            supabase.table("conversations")
            .select("*")
            .eq("thread_id", thread_id)
            .eq("page_id", page_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception:
        return None


def find_conversation_by_sender_id(sender_id, page_id):
    try:
        result = (
            supabase.table("conversations")
            .select("*")
            .eq("sender_id", sender_id)
            .eq("page_id", page_id)
            .order("last_message_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception:
        return None


def append_label_to_conversation(conv, label_name):
    labels = list(conv.get("labels") or [])
    if label_name in labels:
        return
    labels.append(label_name)
    supabase.table("conversations").update({
        "labels": labels,
        "updated_at": datetime.now(TZ7).isoformat(),
    }).eq("conversation_id", conv["conversation_id"]).execute()


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

            # inbox_labels: entry.changes[].field == "inbox_labels"
            for change in entry.get("changes", []):
                if change.get("field") != "inbox_labels":
                    continue
                value = change.get("value", {})
                label_name = value.get("label_name")
                if not label_name:
                    continue
                conv = None
                thread_id = value.get("thread_id")
                sender_id = value.get("sender_id")
                if thread_id:
                    conv = find_conversation_by_thread_id(thread_id, page_id)
                if not conv and sender_id:
                    conv = find_conversation_by_sender_id(sender_id, page_id)
                if conv:
                    append_label_to_conversation(conv, label_name)

            if not entry.get("messaging"):
                continue

            page_token = get_page_token(page_id)
            if not page_token:
                continue

            for messaging in entry.get("messaging", []):
                referral = messaging.get("referral")
                message = messaging.get("message", {})

                # messaging_referrals: referral present but no message text
                if referral and not message:
                    sender_id = messaging.get("sender", {}).get("id")
                    if not sender_id:
                        continue
                    sender_name = get_sender_name(sender_id, page_token)
                    conv = find_or_create_conversation(sender_id, page_id, sender_name)
                    ad_id, ad_title, referral_source = extract_referral_info(referral)
                    update_conversation_ad_info(conv, ad_id, ad_title, referral_source)
                    continue

                text = message.get("text")
                if not text or not text.strip():
                    continue

                is_echo = message.get("is_echo", False)
                if is_echo:
                    role = "page"
                    sender_id = messaging.get("recipient", {}).get("id")
                else:
                    role = "customer"
                    sender_id = messaging.get("sender", {}).get("id")

                if not sender_id:
                    continue

                timestamp_ms = messaging.get("timestamp")
                timestamp_iso = (
                    datetime.fromtimestamp(timestamp_ms / 1000, tz=TZ7).isoformat()
                    if timestamp_ms else datetime.now(TZ7).isoformat()
                )

                sender_name = get_sender_name(sender_id, page_token) if not is_echo else None

                conv = find_or_create_conversation(sender_id, page_id, sender_name)
                append_message_to_conversation(conv, {
                    "role": role,
                    "text": text,
                    "timestamp": timestamp_iso,
                    "message_id": message.get("mid"),
                    "sender_name": sender_name,
                })

                # Referral kèm theo tin nhắn thường
                if referral:
                    ad_id, ad_title, referral_source = extract_referral_info(referral)
                    update_conversation_ad_info(conv, ad_id, ad_title, referral_source)

    return "EVENT_RECEIVED", 200


@app.route("/messages", methods=["GET"])
def get_messages():
    score_filter = request.args.get("score")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    offset = (page - 1) * per_page

    query = supabase.table("conversations").select("*", count="exact")
    if score_filter:
        query = query.ilike("score", score_filter)
    result = query.order("last_message_at", desc=True).range(offset, offset + per_page - 1).execute()

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

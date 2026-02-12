"""
WhatsApp webhook handler — receives and routes incoming messages.

Handles Meta Cloud API webhook verification, validates incoming
webhook signatures using the app secret, and dispatches messages
to the conversation state machine.
"""

import hashlib
import hmac
import logging

from fastapi import APIRouter, Request, Response, HTTPException, Query

from app.config import settings
from app.whatsapp.bot import WhatsAppBot

logger = logging.getLogger(__name__)

router = APIRouter()
bot = WhatsAppBot()


def _validate_signature(payload: bytes, signature_header: str) -> bool:
    """
    Validate the X-Hub-Signature-256 header from Meta.

    Meta sends an HMAC-SHA256 of the raw request body using the
    app secret as the key, prefixed with ``sha256=``.
    """
    if not settings.WHATSAPP_APP_SECRET:
        # No secret configured — skip validation (dev mode)
        return True

    if not signature_header:
        return False

    expected = hmac.new(
        settings.WHATSAPP_APP_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """
    Meta webhook verification endpoint.

    Called by Meta when setting up the webhook URL.
    Must return the hub.challenge value if the verify token matches.
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_message(request: Request):
    """
    Receive incoming WhatsApp messages and route to the bot.

    Validates the webhook signature, extracts sender and message
    content, and dispatches to the conversation state machine.
    Handles text, button_reply, image, and document message types.
    """
    body_bytes = await request.body()

    # Validate webhook signature
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _validate_signature(body_bytes, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    body = await request.json()

    try:
        entry = body["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            # Status update (delivered, read, etc.) — acknowledge only
            return {"status": "ok"}

        message = value["messages"][0]
        sender = message["from"]
        message_type = message["type"]

        if message_type == "text":
            text = message["text"]["body"]
            await bot.handle_message(sender=sender, text=text)

        elif message_type == "interactive":
            interactive = message.get("interactive", {})
            reply_id = (
                interactive.get("button_reply", {}).get("id")
                or interactive.get("list_reply", {}).get("id")
            )
            if reply_id:
                await bot.handle_interactive(sender=sender, reply_id=reply_id)

        elif message_type == "image":
            caption = message.get("image", {}).get("caption", "")
            media_id = message.get("image", {}).get("id", "")
            await bot.handle_media(
                sender=sender,
                media_type="image",
                media_id=media_id,
                caption=caption,
            )

        elif message_type == "document":
            filename = message.get("document", {}).get("filename", "")
            media_id = message.get("document", {}).get("id", "")
            caption = message.get("document", {}).get("caption", "")
            await bot.handle_media(
                sender=sender,
                media_type="document",
                media_id=media_id,
                caption=caption,
                filename=filename,
            )

        else:
            logger.info("Unsupported message type %s from %s", message_type, sender)

    except (KeyError, IndexError):
        pass  # Malformed payload — ignore silently

    return {"status": "ok"}

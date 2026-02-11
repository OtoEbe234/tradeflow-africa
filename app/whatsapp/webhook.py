"""
WhatsApp webhook handler — receives and routes incoming messages.

Handles Meta Cloud API webhook verification and incoming message
dispatch to the conversation state machine.
"""

from fastapi import APIRouter, Request, Response, HTTPException, Query

from app.config import settings
from app.whatsapp.bot import WhatsAppBot

router = APIRouter()
bot = WhatsAppBot()


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

    Processes message webhooks from Meta Cloud API, extracts
    the sender and message content, and dispatches to the
    conversation state machine.
    """
    body = await request.json()

    # Extract message data from Meta's webhook payload
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
            # Button or list reply
            interactive = message.get("interactive", {})
            reply_id = (
                interactive.get("button_reply", {}).get("id")
                or interactive.get("list_reply", {}).get("id")
            )
            await bot.handle_interactive(sender=sender, reply_id=reply_id)

    except (KeyError, IndexError):
        pass  # Malformed payload — ignore silently

    return {"status": "ok"}

"""
Conversation state machine for the WhatsApp bot.

Manages per-user conversation state in Redis and routes
messages to the appropriate flow handler based on context.
"""

import json

from app.redis_client import redis
from app.whatsapp.flows import registration, payment, status, menu


STATE_KEY_PREFIX = "wa:state:"


class WhatsAppBot:
    """WhatsApp conversation state machine."""

    FLOWS = {
        "menu": menu,
        "registration": registration,
        "payment": payment,
        "status": status,
    }

    async def get_state(self, sender: str) -> dict:
        """Retrieve the conversation state for a user from Redis."""
        raw = await redis.get(f"{STATE_KEY_PREFIX}{sender}")
        if raw:
            return json.loads(raw)
        return {"flow": "menu", "step": "start", "data": {}}

    async def set_state(self, sender: str, state: dict):
        """Persist conversation state to Redis with a 1-hour TTL."""
        await redis.setex(
            f"{STATE_KEY_PREFIX}{sender}",
            3600,
            json.dumps(state),
        )

    async def clear_state(self, sender: str):
        """Clear conversation state (return to main menu)."""
        await redis.delete(f"{STATE_KEY_PREFIX}{sender}")

    async def handle_message(self, sender: str, text: str):
        """Route an incoming text message to the current flow."""
        state = await self.get_state(sender)
        flow_name = state.get("flow", "menu")
        flow = self.FLOWS.get(flow_name, menu)

        new_state = await flow.handle_text(sender=sender, text=text, state=state)
        if new_state:
            await self.set_state(sender, new_state)

    async def handle_interactive(self, sender: str, reply_id: str):
        """Route an interactive reply (button/list) to the current flow."""
        state = await self.get_state(sender)
        flow_name = state.get("flow", "menu")
        flow = self.FLOWS.get(flow_name, menu)

        new_state = await flow.handle_interactive(
            sender=sender, reply_id=reply_id, state=state
        )
        if new_state:
            await self.set_state(sender, new_state)

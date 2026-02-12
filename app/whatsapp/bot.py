"""
Conversation state machine for the WhatsApp bot.

Manages per-user conversation state in Redis and routes
messages to the appropriate flow handler based on context.

State expires after 15 minutes of inactivity.  Global commands
(CANCEL, HELP, STATUS) are intercepted before flow routing.
"""

import json
import logging

from app.redis_client import redis
from app.whatsapp.flows import registration, payment, status, menu

logger = logging.getLogger(__name__)

STATE_KEY_PREFIX = "wa:state:"
STATE_TTL_SECONDS = 900  # 15 minutes

# Global commands — handled regardless of current flow
GLOBAL_COMMANDS = {
    "cancel": "cancel",
    "help": "help",
    "status": "status",
    "menu": "menu",
}


class WhatsAppBot:
    """WhatsApp conversation state machine."""

    FLOWS = {
        "menu": menu,
        "registration": registration,
        "payment": payment,
        "status": status,
    }

    # ── State management ─────────────────────────────────────────────

    async def get_state(self, sender: str) -> dict:
        """Retrieve the conversation state for a user from Redis."""
        raw = await redis.get(f"{STATE_KEY_PREFIX}{sender}")
        if raw:
            return json.loads(raw)
        return {"flow": "menu", "step": "start", "data": {}}

    async def set_state(self, sender: str, state: dict):
        """Persist conversation state to Redis with a 15-minute TTL."""
        await redis.setex(
            f"{STATE_KEY_PREFIX}{sender}",
            STATE_TTL_SECONDS,
            json.dumps(state),
        )

    async def clear_state(self, sender: str):
        """Clear conversation state (return to main menu)."""
        await redis.delete(f"{STATE_KEY_PREFIX}{sender}")

    # ── Global command handling ───────────────────────────────────────

    async def _handle_global_command(self, sender: str, text: str) -> bool:
        """
        Check for global commands and handle them.

        Returns True if a global command was handled, False otherwise.
        """
        from app.whatsapp.messages import send_text, send_menu

        command = GLOBAL_COMMANDS.get(text.strip().lower())
        if command is None:
            return False

        if command == "cancel":
            await self.clear_state(sender)
            await send_text(sender, "Cancelled. Returning to main menu.")
            await send_menu(sender)
            return True

        if command == "menu":
            await self.clear_state(sender)
            await send_menu(sender)
            return True

        if command == "help":
            await send_text(
                sender,
                "*TradeFlow Help*\n\n"
                "Available commands:\n"
                "- *menu* — Return to main menu\n"
                "- *cancel* — Cancel current action\n"
                "- *status* — Check transaction status\n"
                "- *help* — Show this message\n\n"
                "You can also type *pay* to start a payment "
                "or *register* to create an account.",
            )
            return True

        if command == "status":
            new_state = {"flow": "status", "step": "start", "data": {}}
            await self.set_state(sender, new_state)
            flow = self.FLOWS["status"]
            result = await flow.handle_text(sender=sender, text="", state=new_state)
            if result:
                await self.set_state(sender, result)
            return True

        return False

    # ── Message routing ──────────────────────────────────────────────

    async def handle_message(self, sender: str, text: str):
        """Route an incoming text message to the current flow."""
        # Check global commands first
        if await self._handle_global_command(sender, text):
            return

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

    async def handle_media(
        self,
        sender: str,
        media_type: str,
        media_id: str,
        caption: str = "",
        filename: str = "",
    ):
        """
        Handle incoming image or document messages.

        Stores the media reference in the current flow's state data
        so flow handlers can process it (e.g., invoice uploads).
        If the media has a caption, it's also routed as text.
        """
        from app.whatsapp.messages import send_text

        state = await self.get_state(sender)
        data = state.get("data", {})
        data["last_media"] = {
            "type": media_type,
            "media_id": media_id,
            "caption": caption,
            "filename": filename,
        }
        state["data"] = data
        await self.set_state(sender, state)

        # If the media has a caption, treat it as text input too
        if caption:
            await self.handle_message(sender, caption)
        else:
            await send_text(
                sender,
                f"Received your {media_type}. "
                "Please continue with the current step or type *menu* for options.",
            )

"""
Status check flow — lets traders check transaction status via WhatsApp.

States: START → SELECT_TRANSACTION → SHOW_STATUS

Shows the last 5 transactions and allows selection by number or
direct reference lookup.
"""

import logging

from sqlalchemy import select

from app.whatsapp.messages import send_text, send_status_update, send_menu
from app.whatsapp.flows.helpers import (
    get_trader_by_phone,
    get_trader_transactions,
    get_user_lang,
    format_direction,
    format_status,
)
from app.database import async_session
from app.models.transaction import Transaction

logger = logging.getLogger(__name__)


async def handle_text(sender: str, text: str, state: dict) -> dict | None:
    """Handle text input during the status check flow."""
    step = state.get("step", "start")
    data = state.get("data", {})

    # ── START ────────────────────────────────────────────────────────
    if step == "start":
        trader = await get_trader_by_phone(sender)
        if not trader:
            await send_text(
                sender,
                "You don't have an account yet.\n"
                "Type *register* to create one.",
            )
            await send_menu(sender)
            return {"flow": "menu", "step": "start", "data": {}}

        txns = await get_trader_transactions(sender, limit=5)
        if not txns:
            await send_text(
                sender,
                "You have no transactions yet.\nType *menu* for options.",
            )
            await send_menu(sender)
            return {"flow": "menu", "step": "start", "data": {}}

        # Build numbered list
        lines = ["Your recent transactions:\n"]
        refs = []
        for i, txn in enumerate(txns, 1):
            direction_label = format_direction(txn.direction.value if hasattr(txn.direction, 'value') else txn.direction)
            status_label = format_status(txn.status.value if hasattr(txn.status, 'value') else txn.status)
            lines.append(
                f"*{i}.* {txn.reference} — {txn.source_amount} "
                f"({direction_label})\n   Status: {status_label}"
            )
            refs.append(txn.reference)

        lines.append("\nReply with a number (1-5) or a transaction reference:")
        await send_text(sender, "\n".join(lines))
        data["refs"] = refs
        return {"flow": "status", "step": "select_transaction", "data": data}

    # ── SELECT TRANSACTION ───────────────────────────────────────────
    if step == "select_transaction":
        choice = text.strip()
        refs = data.get("refs", [])

        # Try numeric selection
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(refs):
                return await _show_transaction(sender, refs[idx])

        # Try direct reference lookup
        upper = choice.upper()
        if upper.startswith("TXN-"):
            return await _show_transaction(sender, upper)

        # Also check if it matches one of the refs
        if upper in refs:
            return await _show_transaction(sender, upper)

        await send_text(
            sender,
            "Invalid selection. Please reply with a number (1-5) or a transaction reference:",
        )
        return {"flow": "status", "step": "select_transaction", "data": data}

    return None


async def handle_interactive(sender: str, reply_id: str, state: dict) -> dict | None:
    """Handle interactive replies during status check."""
    return await handle_text(sender, reply_id, state)


async def _show_transaction(sender: str, reference: str) -> dict:
    """Fetch a transaction by reference and display its details."""
    lang = await get_user_lang(sender)

    async with async_session() as session:
        result = await session.execute(
            select(Transaction).where(Transaction.reference == reference)
        )
        txn = result.scalar_one_or_none()

    if not txn:
        await send_text(
            sender,
            f"Transaction *{reference}* not found.\nType *menu* for options.",
        )
        await send_menu(sender)
        return {"flow": "menu", "step": "start", "data": {}}

    direction_label = format_direction(txn.direction.value if hasattr(txn.direction, 'value') else txn.direction)
    status_label = format_status(txn.status.value if hasattr(txn.status, 'value') else txn.status)

    details = (
        f"Direction: {direction_label}\n"
        f"Amount: {txn.source_amount} → {txn.target_amount or 'pending'}\n"
        f"Rate: {txn.exchange_rate or 'N/A'}\n"
        f"Fee: {txn.fee_amount}"
    )

    await send_status_update(
        sender,
        reference=txn.reference,
        status=status_label,
        details=details,
        lang=lang,
    )
    await send_menu(sender)
    return {"flow": "menu", "step": "start", "data": {}}

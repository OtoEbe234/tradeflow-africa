"""Tests for WhatsApp conversation state machine (bot.py).

Covers state management, global commands, flow routing,
media handling, and state expiry TTL.
"""

import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio

from app.whatsapp.bot import WhatsAppBot, STATE_KEY_PREFIX, STATE_TTL_SECONDS


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    """AsyncMock Redis client for state storage."""
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.setex = AsyncMock()
    r.delete = AsyncMock()
    return r


@pytest.fixture
def bot(mock_redis):
    """WhatsAppBot instance with mocked Redis."""
    with patch("app.whatsapp.bot.redis", mock_redis):
        yield WhatsAppBot()


@pytest.fixture
def patched_redis(mock_redis):
    """Context manager that patches the bot module's redis."""
    return patch("app.whatsapp.bot.redis", mock_redis)


# ── State management ─────────────────────────────────────────────────────


class TestStateManagement:
    """get_state / set_state / clear_state."""

    @pytest.mark.asyncio
    async def test_get_state_no_existing(self, bot, mock_redis, patched_redis):
        """Returns default menu state when no state exists."""
        with patched_redis:
            state = await bot.get_state("+2348012345678")
        assert state == {"flow": "menu", "step": "start", "data": {}}

    @pytest.mark.asyncio
    async def test_get_state_existing(self, bot, mock_redis, patched_redis):
        """Returns stored state from Redis."""
        stored = {"flow": "payment", "step": "amount", "data": {"direction": "ngn_to_cny"}}
        mock_redis.get = AsyncMock(return_value=json.dumps(stored))
        with patched_redis:
            state = await bot.get_state("+2348012345678")
        assert state == stored

    @pytest.mark.asyncio
    async def test_set_state_uses_15min_ttl(self, bot, mock_redis, patched_redis):
        """State is saved with 900-second (15-min) TTL."""
        state = {"flow": "payment", "step": "direction", "data": {}}
        with patched_redis:
            await bot.set_state("+2348012345678", state)
        mock_redis.setex.assert_called_once_with(
            f"{STATE_KEY_PREFIX}+2348012345678",
            STATE_TTL_SECONDS,
            json.dumps(state),
        )
        assert STATE_TTL_SECONDS == 900

    @pytest.mark.asyncio
    async def test_clear_state(self, bot, mock_redis, patched_redis):
        """clear_state deletes the Redis key."""
        with patched_redis:
            await bot.clear_state("+2348012345678")
        mock_redis.delete.assert_called_once_with(
            f"{STATE_KEY_PREFIX}+2348012345678",
        )


# ── Global commands ──────────────────────────────────────────────────────


class TestGlobalCommands:
    """Global commands are handled regardless of current flow."""

    @pytest.mark.asyncio
    async def test_cancel_clears_state_and_sends_menu(self, bot, mock_redis, patched_redis):
        """CANCEL clears state and sends the main menu."""
        with (
            patched_redis,
            patch("app.whatsapp.bot.menu") as mock_menu_flow,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock) as mock_send_text,
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock) as mock_send_menu,
        ):
            await bot.handle_message("+2348012345678", "cancel")

        mock_redis.delete.assert_called_once()
        mock_send_text.assert_called_once()
        assert "Cancelled" in mock_send_text.call_args[0][1]
        mock_send_menu.assert_called_once()

    @pytest.mark.asyncio
    async def test_help_sends_help_text(self, bot, mock_redis, patched_redis):
        """HELP sends a help message with available commands."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock) as mock_send_text,
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock),
        ):
            await bot.handle_message("+2348012345678", "help")

        mock_send_text.assert_called_once()
        help_text = mock_send_text.call_args[0][1]
        assert "Help" in help_text
        assert "menu" in help_text
        assert "cancel" in help_text

    @pytest.mark.asyncio
    async def test_status_command_enters_status_flow(self, bot, mock_redis, patched_redis):
        """STATUS global command transitions to status flow."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock),
        ):
            await bot.handle_message("+2348012345678", "status")

        # State should be set to status flow
        assert mock_redis.setex.called
        last_call = mock_redis.setex.call_args_list[-1]
        saved_state = json.loads(last_call[0][2])
        assert saved_state["flow"] == "status"

    @pytest.mark.asyncio
    async def test_menu_command_clears_state(self, bot, mock_redis, patched_redis):
        """MENU global command clears state and sends menu."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock) as mock_send_menu,
        ):
            await bot.handle_message("+2348012345678", "menu")

        mock_redis.delete.assert_called_once()
        mock_send_menu.assert_called_once()

    @pytest.mark.asyncio
    async def test_global_command_case_insensitive(self, bot, mock_redis, patched_redis):
        """Global commands work regardless of case."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock) as mock_send_text,
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock),
        ):
            await bot.handle_message("+2348012345678", "HELP")

        mock_send_text.assert_called_once()
        assert "Help" in mock_send_text.call_args[0][1]

    @pytest.mark.asyncio
    async def test_non_global_routes_to_flow(self, bot, mock_redis, patched_redis):
        """Non-global text is routed to the current flow handler."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock),
        ):
            # Default state is menu flow. "hi" triggers menu display.
            await bot.handle_message("+2348012345678", "hi")

        # Should have set state (menu flow responded)
        assert mock_redis.setex.called


# ── Flow routing ─────────────────────────────────────────────────────────


class TestFlowRouting:
    """Messages are routed to the correct flow based on state."""

    @pytest.mark.asyncio
    async def test_menu_routes_pay_to_payment_flow(self, bot, mock_redis, patched_redis):
        """Typing 'pay' from menu transitions to payment flow."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock),
        ):
            await bot.handle_message("+2348012345678", "pay")

        last_call = mock_redis.setex.call_args_list[-1]
        saved_state = json.loads(last_call[0][2])
        assert saved_state["flow"] == "payment"
        assert saved_state["step"] == "start"

    @pytest.mark.asyncio
    async def test_menu_routes_register_to_registration_flow(self, bot, mock_redis, patched_redis):
        """Typing 'register' from menu transitions to registration flow."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock),
        ):
            await bot.handle_message("+2348012345678", "register")

        last_call = mock_redis.setex.call_args_list[-1]
        saved_state = json.loads(last_call[0][2])
        assert saved_state["flow"] == "registration"

    @pytest.mark.asyncio
    async def test_payment_flow_direction_step(self, bot, mock_redis, patched_redis):
        """Payment flow: direction step stores chosen direction."""
        # Simulate being in payment flow at direction step
        mock_redis.get = AsyncMock(
            return_value=json.dumps({"flow": "payment", "step": "direction", "data": {}})
        )
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
        ):
            await bot.handle_message("+2348012345678", "1")

        last_call = mock_redis.setex.call_args_list[-1]
        saved_state = json.loads(last_call[0][2])
        assert saved_state["flow"] == "payment"
        assert saved_state["step"] == "amount"
        assert saved_state["data"]["direction"] == "ngn_to_cny"

    @pytest.mark.asyncio
    async def test_payment_flow_amount_step_valid(self, bot, mock_redis, patched_redis):
        """Payment flow: valid amount is parsed and stored."""
        mock_redis.get = AsyncMock(
            return_value=json.dumps({
                "flow": "payment", "step": "amount",
                "data": {"direction": "ngn_to_cny"},
            })
        )
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
        ):
            await bot.handle_message("+2348012345678", "50m")

        last_call = mock_redis.setex.call_args_list[-1]
        saved_state = json.loads(last_call[0][2])
        assert saved_state["step"] == "beneficiary_name"
        assert saved_state["data"]["amount"] == "50000000"

    @pytest.mark.asyncio
    async def test_payment_flow_amount_step_invalid(self, bot, mock_redis, patched_redis):
        """Payment flow: invalid amount keeps user on same step."""
        mock_redis.get = AsyncMock(
            return_value=json.dumps({
                "flow": "payment", "step": "amount",
                "data": {"direction": "ngn_to_cny"},
            })
        )
        with (
            patched_redis,
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock) as mock_send,
        ):
            await bot.handle_message("+2348012345678", "xyz invalid")

        last_call = mock_redis.setex.call_args_list[-1]
        saved_state = json.loads(last_call[0][2])
        assert saved_state["step"] == "amount"  # stays on same step
        assert "couldn't understand" in mock_send.call_args[0][1]

    @pytest.mark.asyncio
    async def test_registration_flow_business_name(self, bot, mock_redis, patched_redis):
        """Registration flow: business name is stored."""
        mock_redis.get = AsyncMock(
            return_value=json.dumps({"flow": "registration", "step": "business_name", "data": {}})
        )
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
        ):
            await bot.handle_message("+2348012345678", "Lagos Trading Co.")

        last_call = mock_redis.setex.call_args_list[-1]
        saved_state = json.loads(last_call[0][2])
        assert saved_state["step"] == "trader_type"
        assert saved_state["data"]["business_name"] == "Lagos Trading Co."


# ── Interactive routing ──────────────────────────────────────────────────


class TestInteractiveRouting:
    """Button and list replies are routed correctly."""

    @pytest.mark.asyncio
    async def test_action_pay_button(self, bot, mock_redis, patched_redis):
        """action_pay button reply transitions to payment flow."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock),
        ):
            await bot.handle_interactive("+2348012345678", "action_pay")

        last_call = mock_redis.setex.call_args_list[-1]
        saved_state = json.loads(last_call[0][2])
        assert saved_state["flow"] == "payment"

    @pytest.mark.asyncio
    async def test_action_status_button(self, bot, mock_redis, patched_redis):
        """action_status button reply transitions to status flow."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock),
        ):
            await bot.handle_interactive("+2348012345678", "action_status")

        last_call = mock_redis.setex.call_args_list[-1]
        saved_state = json.loads(last_call[0][2])
        assert saved_state["flow"] == "status"

    @pytest.mark.asyncio
    async def test_action_register_button(self, bot, mock_redis, patched_redis):
        """action_register button reply transitions to registration flow."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock),
        ):
            await bot.handle_interactive("+2348012345678", "action_register")

        last_call = mock_redis.setex.call_args_list[-1]
        saved_state = json.loads(last_call[0][2])
        assert saved_state["flow"] == "registration"


# ── Media handling ───────────────────────────────────────────────────────


class TestMediaHandling:
    """handle_media stores media info and routes captions."""

    @pytest.mark.asyncio
    async def test_image_with_caption(self, bot, mock_redis, patched_redis):
        """Image with caption stores media and routes caption as text."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock),
        ):
            await bot.handle_media(
                sender="+2348012345678",
                media_type="image",
                media_id="img-123",
                caption="hi",
            )

        # Should have stored media in state AND routed caption
        assert mock_redis.setex.call_count >= 1
        first_set = json.loads(mock_redis.setex.call_args_list[0][0][2])
        assert first_set["data"]["last_media"]["type"] == "image"
        assert first_set["data"]["last_media"]["media_id"] == "img-123"

    @pytest.mark.asyncio
    async def test_document_without_caption(self, bot, mock_redis, patched_redis):
        """Document without caption stores media and sends acknowledgement."""
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock) as mock_send,
        ):
            await bot.handle_media(
                sender="+2348012345678",
                media_type="document",
                media_id="doc-456",
                filename="invoice.pdf",
            )

        # Should store media and send ack
        assert mock_redis.setex.called
        saved_state = json.loads(mock_redis.setex.call_args_list[0][0][2])
        assert saved_state["data"]["last_media"]["filename"] == "invoice.pdf"

        mock_send.assert_called_once()
        assert "document" in mock_send.call_args[0][1]


# ── Cancel mid-flow ──────────────────────────────────────────────────────


class TestCancelMidFlow:
    """CANCEL works from any flow step."""

    @pytest.mark.asyncio
    async def test_cancel_from_payment_flow(self, bot, mock_redis, patched_redis):
        """Cancel during payment flow returns to menu."""
        mock_redis.get = AsyncMock(
            return_value=json.dumps({
                "flow": "payment", "step": "beneficiary_name",
                "data": {"direction": "ngn_to_cny", "amount": "50000000"},
            })
        )
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock) as mock_menu,
        ):
            await bot.handle_message("+2348012345678", "cancel")

        mock_redis.delete.assert_called_once()
        mock_menu.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_from_registration_flow(self, bot, mock_redis, patched_redis):
        """Cancel during registration flow returns to menu."""
        mock_redis.get = AsyncMock(
            return_value=json.dumps({
                "flow": "registration", "step": "kyc_document",
                "data": {"business_name": "Test Corp"},
            })
        )
        with (
            patched_redis,
            patch("app.whatsapp.messages.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.messages.send_menu", new_callable=AsyncMock) as mock_menu,
        ):
            await bot.handle_message("+2348012345678", "CANCEL")

        mock_redis.delete.assert_called_once()
        mock_menu.assert_called_once()

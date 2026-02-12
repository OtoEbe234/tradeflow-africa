"""
Backward-compatible re-exports from app.api.deps.

Existing code that imports ``from app.api.dependencies import get_current_trader``
continues to work unchanged.
"""

from app.api.deps import get_current_trader, require_auth, require_tier, require_pin

__all__ = ["get_current_trader", "require_auth", "require_tier", "require_pin"]

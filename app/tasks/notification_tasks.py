"""
Notification Celery tasks — async delivery of SMS and WhatsApp messages.

Offloads notification delivery to background workers to avoid
blocking API responses.
"""

import asyncio
import logging

from app.tasks.celery_app import celery_app
from app.services.notification_service import notification_service

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.notification_tasks.send_otp_notification")
def send_otp_notification(phone: str, otp: str):
    """Send OTP via SMS and WhatsApp in the background."""
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(notification_service.send_otp(phone, otp))
        logger.info("OTP sent to %s: %s", phone, result)
        return result
    finally:
        loop.close()


@celery_app.task(name="app.tasks.notification_tasks.send_match_notification")
def send_match_notification(phone: str, match_details: dict):
    """Notify a trader about a successful match."""
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            notification_service.notify_match(phone, match_details)
        )
        logger.info("Match notification sent to %s", phone)
        return result
    finally:
        loop.close()


@celery_app.task(name="app.tasks.notification_tasks.send_status_update")
def send_status_update(phone: str, reference: str, new_status: str):
    """Notify a trader about a transaction status change."""
    loop = asyncio.new_event_loop()
    try:
        message = f"Transaction {reference}: status updated to *{new_status}*."
        result = loop.run_until_complete(
            notification_service.send_whatsapp(phone, message)
        )
        logger.info("Status update sent to %s for %s", phone, reference)
        return result
    finally:
        loop.close()

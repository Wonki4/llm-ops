"""Slack webhook integration for team join request notifications."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def send_slack_notification(
    requester_id: str,
    team_alias: str,
    team_id: str,
    message: str | None = None,
) -> bool:
    """Send a Slack notification when a new team join request is created."""
    if not settings.slack_webhook_url:
        logger.warning("Slack webhook URL not configured, skipping notification")
        return False

    frontend_url = settings.cors_origins[0] if settings.cors_origins else "http://localhost:3000"
    admin_link = f"{frontend_url}/admin/requests"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔔 새로운 팀 가입 요청", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*요청자 (사번):*\n{requester_id}"},
                {"type": "mrkdwn", "text": f"*팀:*\n{team_alias} (`{team_id}`)"},
            ],
        },
    ]

    if message:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*메시지:*\n{message}"},
            }
        )

    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "요청 확인하기"},
                    "url": admin_link,
                    "style": "primary",
                }
            ],
        }
    )

    payload = {"blocks": blocks}

    try:
        async with httpx.AsyncClient(verify=settings.ssl_verify) as client:
            resp = await client.post(settings.slack_webhook_url, json=payload, timeout=10.0)
            resp.raise_for_status()
            return True
    except Exception:
        logger.exception("Failed to send Slack notification")
        return False


async def send_deployment_event_notification(
    *,
    model_name: str,
    namespace: str,
    event_type: str,
    severity: str,
    message: str | None,
) -> bool:
    """Fan-out a deployment event to Slack. severity ∈ {info, warning, error}."""
    if not settings.slack_webhook_url:
        return False

    emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🛑"}.get(severity, "ℹ️")
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} 모델 배포 알림 — {event_type}", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*모델:*\n`{model_name}`"},
                {"type": "mrkdwn", "text": f"*네임스페이스:*\n`{namespace}`"},
                {"type": "mrkdwn", "text": f"*심각도:*\n{severity}"},
            ],
        },
    ]
    if message:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```{message}```"}})

    try:
        async with httpx.AsyncClient(verify=settings.ssl_verify) as client:
            resp = await client.post(settings.slack_webhook_url, json={"blocks": blocks}, timeout=10.0)
            resp.raise_for_status()
            return True
    except Exception:
        logger.exception("Failed to send deployment Slack notification")
        return False

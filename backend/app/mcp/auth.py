"""Who is calling the portal MCP.

Tokens are Keycloak access tokens Claude Code obtained for the MCP client
(authorization code + PKCE). They are accepted only when they carry the MCP
audience *and* were issued to the MCP client (`azp`): a portal login token can
carry the MCP audience too, and an MCP token can carry the portal audience
(Keycloak's roles scope adds it), so `aud` alone cannot tell them apart.

The token is never forwarded anywhere; tools run in-process as the caller.
"""

import dataclasses
import logging

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TokenPayload, verify_token
from app.config import settings
from app.db.models.custom_user import CustomUser, GlobalRole
from app.services.user_provisioning import IdentityMismatchError, upsert_portal_user

logger = logging.getLogger(__name__)


class McpTokenVerifier:
    """SDK ``TokenVerifier``: ``None`` makes the SDK answer 401, which sends Claude Code to log in again."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            payload = await verify_token(token, audience=settings.mcp_token_audience)
        except ValueError as e:
            logger.info("MCP token rejected: %s", e)
            return None
        if payload.azp != settings.mcp_client_id:
            logger.info("MCP token rejected: issued to client %r", payload.azp)
            return None
        return AccessToken(
            token=token,
            client_id=payload.azp,
            scopes=[],
            expires_at=payload.exp,
            subject=payload.sub,
            claims=dataclasses.asdict(payload),
        )


def current_token_payload() -> TokenPayload:
    """The verified token of the MCP request being handled."""
    access_token = get_access_token()
    if access_token is None or access_token.claims is None:
        raise ToolError("unauthenticated")
    return TokenPayload(**access_token.claims)


async def require_mcp_super_user(db: AsyncSession) -> CustomUser:
    """The caller's portal user, provisioned like a portal login; super users only."""
    try:
        user = await upsert_portal_user(db, current_token_payload())
    except IdentityMismatchError as e:
        raise ToolError("계정 확인이 필요합니다. 관리자에게 문의하세요.") from e
    if user.global_role != GlobalRole.SUPER_USER:
        raise ToolError("super user only")
    return user

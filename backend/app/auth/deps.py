import logging

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TokenPayload, verify_token
from app.auth.session import SessionData, refresh_session_if_needed
from app.db.models.custom_user import CustomUser, GlobalRole
from app.db.session import get_db
from app.services.user_provisioning import upsert_portal_user

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_token(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> TokenPayload:
    cookie_names = list(request.cookies.keys())
    logging.debug("Auth check - cookies present: %s, has Bearer: %s", cookie_names, bool(credentials))
    session: SessionData | None = await refresh_session_if_needed(request, response)
    if session and session.access_token:
        try:
            return await verify_token(session.access_token)
        except ValueError as e:
            logging.error("Session token verification failed: %s", e)

    if credentials:
        try:
            return await verify_token(credentials.credentials)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


async def get_current_user(
    token: TokenPayload = Depends(get_current_token),
    db: AsyncSession = Depends(get_db),
) -> CustomUser:
    return await upsert_portal_user(db, token)


def require_super_user(user: CustomUser = Depends(get_current_user)) -> CustomUser:
    if user.global_role != GlobalRole.SUPER_USER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super user access required")
    return user

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TokenPayload, verify_token
from app.auth.session import SessionData, refresh_session_if_needed
from app.config import settings
from app.db.models.custom_user import CustomUser, GlobalRole
from app.db.session import get_db

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_token(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> TokenPayload:
    session: SessionData | None = await refresh_session_if_needed(request, response)
    if session and session.access_token:
        try:
            return await verify_token(session.access_token)
        except ValueError:
            pass

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
    result = await db.execute(select(CustomUser).where(CustomUser.user_id == token.preferred_username))
    user = result.scalar_one_or_none()

    if not user:
        all_roles = (token.realm_roles or []) + (token.client_roles or [])
        is_super = settings.super_user_role in all_roles

        user = CustomUser(
            user_id=token.preferred_username,
            email=token.email,
            display_name=token.name,
            global_role=GlobalRole.SUPER_USER if is_super else GlobalRole.USER,
        )
        db.add(user)
        await db.flush()
    else:
        all_roles = (token.realm_roles or []) + (token.client_roles or [])
        is_super = settings.super_user_role in all_roles
        new_role = GlobalRole.SUPER_USER if is_super else user.global_role
        if user.global_role != new_role:
            user.global_role = new_role
            await db.flush()

    return user


def require_super_user(user: CustomUser = Depends(get_current_user)) -> CustomUser:
    if user.global_role != GlobalRole.SUPER_USER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super user access required")
    return user

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from backend.api.config import settings
from backend.api.deps import (
    SESSION_COOKIE_NAME,
    get_auth_service,
    get_current_user_unchecked,
    get_rate_limiter,
)
from backend.auth.rate_limit import LoginRateLimiter
from backend.auth.service import AuthenticatedUser, AuthService, InvalidCurrentPasswordError

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class UserOut(BaseModel):
    user_id: str
    username: str
    role: str
    must_change_password: bool = False


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_lifetime_hours * 3600,
    )


@router.post("/login", response_model=UserOut)
def login(
    body: LoginRequest,
    response: Response,
    auth: AuthService = Depends(get_auth_service),
    limiter: LoginRateLimiter = Depends(get_rate_limiter),
):
    if limiter.is_locked_out(body.username):
        wait = limiter.seconds_until_unlocked(body.username)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {wait} seconds.",
        )

    user = auth.authenticate(body.username, body.password)
    if not user:
        limiter.record_failure(body.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    limiter.record_success(body.username)
    token = auth.create_session(user.user_id)
    _set_session_cookie(response, token)
    # must_change_password flows through so the frontend can show the
    # forced-change screen instead of the main app -- login itself succeeds
    # (the password IS correct), the account is just not fully usable yet.
    return UserOut(
        user_id=user.user_id, username=user.username, role=user.role, must_change_password=user.must_change_password
    )


@router.post("/logout")
def logout(
    response: Response,
    logtool_session: str | None = Cookie(default=None),
    auth: AuthService = Depends(get_auth_service),
):
    # Cookie value is read manually here (not via the auth guard) so logout
    # works even if the session already expired -- it should always clear
    # the browser's cookie regardless of server-side session state.
    if logtool_session:
        auth.revoke_session(logtool_session)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: AuthenticatedUser = Depends(get_current_user_unchecked)):
    return UserOut(
        user_id=user.user_id, username=user.username, role=user.role, must_change_password=user.must_change_password
    )


@router.post("/change-password", response_model=UserOut)
def change_password(
    body: ChangePasswordRequest,
    response: Response,
    user: AuthenticatedUser = Depends(get_current_user_unchecked),
    auth: AuthService = Depends(get_auth_service),
):
    """Used both for the forced first-login flow and any later voluntary
    password change -- same endpoint, same requirement to prove you know
    the current password first."""
    try:
        new_token = auth.self_change_password(user.user_id, body.current_password, body.new_password)
    except InvalidCurrentPasswordError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    _set_session_cookie(response, new_token)
    return UserOut(user_id=user.user_id, username=user.username, role=user.role, must_change_password=False)

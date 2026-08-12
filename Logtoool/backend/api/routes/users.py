from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.deps import get_auth_service, require_admin
from backend.auth.service import AuthenticatedUser, AuthService, UsernameTakenError

router = APIRouter(prefix="/api/users", tags=["users"])


class CreateUserRequest(BaseModel):
    username: str
    password: str = Field(min_length=8)
    role: str = "member"


class SetActiveRequest(BaseModel):
    is_active: bool


@router.get("")
def list_users(
    _admin: AuthenticatedUser = Depends(require_admin),
    auth: AuthService = Depends(get_auth_service),
):
    return auth.list_users()


@router.post("")
def create_user(
    body: CreateUserRequest,
    _admin: AuthenticatedUser = Depends(require_admin),
    auth: AuthService = Depends(get_auth_service),
):
    if body.role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'member'")
    try:
        user = auth.create_user(body.username, body.password, role=body.role)
    except UsernameTakenError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"user_id": user.user_id, "username": user.username, "role": user.role, "must_change_password": user.must_change_password}


@router.patch("/{user_id}/active")
def set_user_active(
    user_id: str,
    body: SetActiveRequest,
    admin: AuthenticatedUser = Depends(require_admin),
    auth: AuthService = Depends(get_auth_service),
):
    if user_id == admin.user_id and not body.is_active:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    try:
        auth.set_user_active(user_id, body.is_active)
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}


@router.delete("/{user_id}")
def delete_user(
    user_id: str,
    admin: AuthenticatedUser = Depends(require_admin),
    auth: AuthService = Depends(get_auth_service),
):
    if user_id == admin.user_id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    auth.delete_user(user_id)
    return {"ok": True}


@router.post("/{user_id}/force-password-reset")
def force_password_reset(
    user_id: str,
    _admin: AuthenticatedUser = Depends(require_admin),
    auth: AuthService = Depends(get_auth_service),
):
    """Marks the user's current password as expired -- they'll be forced
    through the set-new-password flow on their next login, without the
    admin needing to know or set an interim password."""
    try:
        auth.force_password_reset(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}

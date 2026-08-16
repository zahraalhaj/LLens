from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.deps import get_remote_machine_service, require_admin
from backend.auth.service import AuthenticatedUser
from backend.remote import crypto
from backend.remote.service import MachineNotFoundError, RemoteMachineService

router = APIRouter(prefix="/api/machines", tags=["remote-machines"])


class CreateMachineRequest(BaseModel):
    label: str
    host: str
    port: int = 22
    username: str
    auth_type: str  # 'password' | 'key'
    secret: str = Field(min_length=1)  # password, or PEM-format private key contents
    remote_directory: str
    recursive: bool = True
    poll_interval_minutes: int = Field(default=15, ge=1)


class UpdateMachineRequest(BaseModel):
    label: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    auth_type: Optional[str] = None
    secret: Optional[str] = None  # omit or empty to leave credential unchanged
    remote_directory: Optional[str] = None
    recursive: Optional[bool] = None
    poll_interval_minutes: Optional[int] = None
    enabled: Optional[bool] = None


def _handle_encryption_errors(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except crypto.EncryptionKeyMissingError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except crypto.DecryptionFailedError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_machines(
    _admin: AuthenticatedUser = Depends(require_admin),
    service: RemoteMachineService = Depends(get_remote_machine_service),
):
    return service.list_machines()


@router.post("")
def create_machine(
    body: CreateMachineRequest,
    admin: AuthenticatedUser = Depends(require_admin),
    service: RemoteMachineService = Depends(get_remote_machine_service),
):
    if body.auth_type not in ("password", "key"):
        raise HTTPException(status_code=400, detail="auth_type must be 'password' or 'key'")
    return _handle_encryption_errors(
        service.create_machine,
        label=body.label,
        host=body.host,
        port=body.port,
        username=body.username,
        auth_type=body.auth_type,
        secret=body.secret,
        remote_directory=body.remote_directory,
        recursive=body.recursive,
        poll_interval_minutes=body.poll_interval_minutes,
        created_by_user_id=admin.user_id,
    )


@router.get("/{machine_id}")
def get_machine(
    machine_id: str,
    _admin: AuthenticatedUser = Depends(require_admin),
    service: RemoteMachineService = Depends(get_remote_machine_service),
):
    try:
        return service.get_machine(machine_id)
    except MachineNotFoundError:
        raise HTTPException(status_code=404, detail="Machine not found")


@router.put("/{machine_id}")
def update_machine(
    machine_id: str,
    body: UpdateMachineRequest,
    _admin: AuthenticatedUser = Depends(require_admin),
    service: RemoteMachineService = Depends(get_remote_machine_service),
):
    try:
        return _handle_encryption_errors(service.update_machine, machine_id, **body.model_dump())
    except MachineNotFoundError:
        raise HTTPException(status_code=404, detail="Machine not found")


@router.delete("/{machine_id}")
def delete_machine(
    machine_id: str,
    _admin: AuthenticatedUser = Depends(require_admin),
    service: RemoteMachineService = Depends(get_remote_machine_service),
):
    service.delete_machine(machine_id)
    return {"ok": True}


@router.post("/{machine_id}/test-connection")
def test_connection(
    machine_id: str,
    _admin: AuthenticatedUser = Depends(require_admin),
    service: RemoteMachineService = Depends(get_remote_machine_service),
):
    try:
        return _handle_encryption_errors(service.test_connection, machine_id)
    except MachineNotFoundError:
        raise HTTPException(status_code=404, detail="Machine not found")


@router.post("/{machine_id}/poll-now")
def poll_now(
    machine_id: str,
    _admin: AuthenticatedUser = Depends(require_admin),
    service: RemoteMachineService = Depends(get_remote_machine_service),
):
    try:
        result = _handle_encryption_errors(service.poll_machine, machine_id)
    except MachineNotFoundError:
        raise HTTPException(status_code=404, detail="Machine not found")
    return {
        "machine_id": result.machine_id,
        "files_found": result.files_found,
        "files_ingested": result.files_ingested,
        "files_unchanged": result.files_unchanged,
        "files_rotated": result.files_rotated,
        "total_events_ingested": result.total_events_ingested,
        "errors": result.errors,
    }

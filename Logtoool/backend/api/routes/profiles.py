from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.deps import get_profile_manager, get_current_user, get_profile_generator, require_admin
from backend.auth.service import AuthenticatedUser
from backend.core.profiles import ProfileManager
from backend.core.schema import ParserProfile
from backend.llm.profile_gen import LLMProfileGenerator, extract_sample_lines

from backend.core.custom_parser_registry import list_custom_profiles

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class GenerateProfileRequest(BaseModel):
    sample_lines: list[str]
    suggested_name: str = "Auto-Generated Profile"


@router.get("")
def list_profiles(
    _user: AuthenticatedUser = Depends(get_current_user),
    pm: ProfileManager = Depends(get_profile_manager),
):
    # Custom (code-defined) parsers are appended so they show up in the
    # Upload picker and Settings alongside declarative profiles, even though
    # they don't live in the profiles/ directory as JSON files.
    return [p.model_dump() for p in pm.list_profiles()] + [p.model_dump() for p in list_custom_profiles()]


@router.post("")
def create_profile(
    profile: ParserProfile,
    _admin: AuthenticatedUser = Depends(require_admin),
    pm: ProfileManager = Depends(get_profile_manager),
):
    if pm.get_profile_by_name(profile.name):
        raise HTTPException(status_code=409, detail=f"Profile '{profile.name}' already exists")
    path = pm.save_profile(profile)
    return {"ok": True, "path": path}


@router.post("/generate")
def generate_profile(
    body: GenerateProfileRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
    generator: LLMProfileGenerator = Depends(get_profile_generator),
):
    """Runs the sample-and-repair-loop LLM profile generator. Returns a
    candidate profile that the caller must still review and POST to
    /api/profiles to save -- this endpoint never writes anything itself,
    matching the spec's require-explicit-confirmation-before-save rule."""
    if not body.sample_lines:
        raise HTTPException(status_code=400, detail="sample_lines must not be empty")
    sample = extract_sample_lines(body.sample_lines)
    profile, status = generator.generate_profile(sample, suggested_name=body.suggested_name)
    if profile is None:
        raise HTTPException(status_code=503, detail=status)
    return {"profile": profile.model_dump(), "status": status}

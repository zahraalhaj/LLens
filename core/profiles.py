import json
from pathlib import Path
from typing import Dict, List

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


def load_profile(name: str) -> dict:
    path = PROFILES_DIR / f"{name}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_profiles() -> List[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))


def load_all() -> Dict[str, dict]:
    return {name: load_profile(name) for name in list_profiles()}

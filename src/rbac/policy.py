"""
RBAC policy resolution (BUILD_SPEC.md Part 6).

Single source of truth for "which roles may see this chunk" lives in
config/rbac_policy.yaml. This module exposes the policy to:
  - the retag/apply script (src/rbac/apply_policy.py)
  - chunk building (src/chunking/build_chunks.py)
  - the retriever's audit logger (query-time role checks)
"""
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_policy() -> dict:
    return yaml.safe_load(open(PROJECT_ROOT / "config" / "rbac_policy.yaml"))


def allowed_roles_for(sector: str) -> list[str]:
    """Roles that may see a chunk belonging to the given sector."""
    policy = load_policy()
    restriction = policy.get("sector_restrictions", {}).get(sector)
    if restriction:
        return list(restriction["allowed_roles"])
    return list(policy["default_allowed_roles"])


def is_allowed(roles: list[str], role: str) -> bool:
    return role in (roles or [])


def all_roles() -> list[str]:
    return sorted(load_policy()["roles"].keys())
"""Executable BDD Acceptance Criteria for US-0005: Verify Public Surface Boundaries.

Governed by:
- ADR-0006 (The Public Surface Is Gated)
- ADR-0047 (Four Adapter Paths Are Stable)
- PRD-0001
- US-0005
"""

from __future__ import annotations

import re
from pathlib import Path

import redstring


def test_verifying_public_api_surface_is_selfcontained_and_isolated() -> None:
    """Scenario: Verifying public API surface is self-contained and isolated (US-0005)."""
    assert hasattr(redstring, "__all__")
    assert isinstance(redstring.__all__, list)
    assert len(redstring.__all__) > 0

    # Ensure all symbols listed in __all__ are reachable on the redstring package
    for symbol_name in redstring.__all__:
        assert hasattr(redstring, symbol_name), f"Missing exported symbol: {symbol_name}"


def test_verifying_dualdeclaration_version_synchronization() -> None:
    """Scenario: Verifying dual-declaration version synchronization (US-0005)."""
    root = Path(__file__).resolve().parent.parent.parent
    pyproject_file = root / "pyproject.toml"

    assert pyproject_file.exists()
    content = pyproject_file.read_text(encoding="utf-8")
    m = re.search(r'version\s*=\s*"(.*?)"', content)
    assert m is not None, "Version declaration missing in pyproject.toml"
    pyproject_ver = m.group(1).strip()

    pkg_ver = getattr(redstring, "__version__", None)
    assert pkg_ver is not None, "Missing __version__ in redstring package"
    assert pkg_ver == pyproject_ver, f"Version mismatch: {pkg_ver} != {pyproject_ver}"

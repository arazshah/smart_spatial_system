"""
plugins._shared.local_path_validation

Shared local-file path validation for source-loader plugins.

REFACTOR_PLAN.md Phase 6 (source abstraction): local_vector_loader.py and
local_raster_loader.py each carried a byte-identical copy of this
allowed-roots/extension validation (only the "Vector"/"Raster" wording and
default extension set differed). This is security-relevant path-traversal
guarding, so keeping it duplicated meant a fix to one copy could silently
miss the other. Both plugins now call this shared implementation.
"""

from __future__ import annotations

from pathlib import Path


def ensure_under_allowed_roots(
    path: Path,
    allowed_roots: list[str] | None,
    *,
    label: str,
) -> None:
    """
    Ensure path is under one of allowed_roots.

    If allowed_roots is empty or None, no restriction is applied.
    """
    if not allowed_roots:
        return

    resolved_path = path.resolve()
    resolved_roots = [Path(root).expanduser().resolve() for root in allowed_roots]

    for root in resolved_roots:
        if resolved_path == root or root in resolved_path.parents:
            return

    raise ValueError(
        f"{label} path is not under any allowed root: {resolved_path}. "
        f"Allowed roots: {[str(r) for r in resolved_roots]}"
    )


def validate_local_path(
    path: str,
    *,
    label: str,
    default_extensions: set[str],
    strict_extensions: bool = True,
    allowed_extensions: set[str] | None = None,
    allowed_roots: list[str] | None = None,
) -> Path:
    """
    Validate a local file path shared by loader plugins.

    Args:
        path:
            Local file path.
        label:
            Human-readable file kind for error messages, e.g. "Vector" or
            "Raster".
        default_extensions:
            Extension set used when allowed_extensions is not provided.
        strict_extensions:
            If True, file extension must be one of the effective allowed
            extensions.

    Returns:
        Resolved pathlib.Path.

    Raises:
        ValueError:
            If path is empty, not a string, not a file, has an invalid
            extension, or is outside allowed_roots.
        FileNotFoundError:
            If the file does not exist.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string.")

    resolved_path = Path(path).expanduser().resolve()

    if not resolved_path.exists():
        raise FileNotFoundError(f"{label} file not found: {resolved_path}")

    if not resolved_path.is_file():
        raise ValueError(f"{label} path is not a file: {resolved_path}")

    ensure_under_allowed_roots(resolved_path, allowed_roots, label=label)

    suffix = resolved_path.suffix.lower()
    effective_extensions = allowed_extensions or default_extensions

    if strict_extensions and suffix not in effective_extensions:
        raise ValueError(
            f"Unsupported {label.lower()} extension "
            f"'{suffix}'. Allowed extensions: {sorted(effective_extensions)}"
        )

    return resolved_path

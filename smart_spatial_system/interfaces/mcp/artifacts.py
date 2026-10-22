"""
smart_spatial_system.interfaces.mcp.artifacts

Server-side artifact store so large or non-JSON-friendly results (rasters,
big feature collections) are passed between tool calls by handle instead of
being copied through the client. Aligns with the ``artifact`` direction of
ADR-002.

Safety properties:
- Handles are 192-bit random tokens; client input is matched against a strict
  regex before it ever touches the filesystem (no path traversal).
- Storage is JSON only. Nothing is pickled or loaded with ``allow_pickle``.
- The directory is server-owned (never client-chosen), mode 0700, files 0600,
  writes are atomic, and size, count and age are bounded.
- Handles are bearer capabilities within one deployment: anyone holding the
  API key and the handle can read it (same trust model as ``api/auth.py``).
  The store is a filesystem directory, so workers sharing a volume share
  artifacts; an ephemeral container loses them (they expire anyway).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ARTIFACT_ID_RE = re.compile(r"^art_[A-Za-z0-9_-]{32}$")


class ArtifactError(RuntimeError):
    """Safe-to-show artifact failure (not found, too large, store full)."""


@dataclass(frozen=True)
class StoredArtifact:
    artifact_id: str
    kind: str
    summary: dict[str, Any]
    data: Any
    created_at: float
    size_bytes: int


class ArtifactStore:
    def __init__(
        self,
        root: str | Path,
        *,
        ttl_seconds: int = 3600,
        max_artifact_bytes: int = 50_000_000,
        max_total_bytes: int = 1_000_000_000,
    ) -> None:
        self.root = Path(root)
        self.ttl_seconds = ttl_seconds
        self.max_artifact_bytes = max_artifact_bytes
        self.max_total_bytes = max_total_bytes
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _path(self, artifact_id: str) -> Path:
        if not isinstance(artifact_id, str) or not ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise ArtifactError("Artifact not found or expired.")
        return self.root / f"{artifact_id}.json"

    def purge_expired(self) -> int:
        removed = 0
        cutoff = time.time() - self.ttl_seconds
        for path in self.root.glob("art_*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def _total_bytes(self) -> int:
        total = 0
        for path in self.root.glob("art_*.json"):
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    def put(self, kind: str, data: Any, summary: dict[str, Any] | None = None) -> StoredArtifact:
        body = json.dumps(
            {"kind": kind, "summary": summary or {}, "data": data}, separators=(",", ":")
        ).encode("utf-8")
        if len(body) > self.max_artifact_bytes:
            raise ArtifactError(
                f"Result is {len(body)} bytes; the artifact limit is {self.max_artifact_bytes}."
            )
        self.purge_expired()
        if self._total_bytes() + len(body) > self.max_total_bytes:
            raise ArtifactError("Artifact store is full; delete artifacts or wait for expiry.")
        artifact_id = "art_" + secrets.token_urlsafe(24)
        fd, tmp_name = tempfile.mkstemp(dir=self.root, prefix=".tmp_")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(body)
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self._path(artifact_id))
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return StoredArtifact(artifact_id, kind, summary or {}, data, time.time(), len(body))

    def get(self, artifact_id: str) -> StoredArtifact:
        path = self._path(artifact_id)
        try:
            stat = path.stat()
            if stat.st_mtime < time.time() - self.ttl_seconds:
                path.unlink(missing_ok=True)
                raise FileNotFoundError(artifact_id)
            payload = json.loads(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise ArtifactError("Artifact not found or expired.") from exc
        return StoredArtifact(
            artifact_id,
            payload["kind"],
            payload.get("summary", {}),
            payload["data"],
            stat.st_mtime,
            stat.st_size,
        )

    def delete(self, artifact_id: str) -> bool:
        path = self._path(artifact_id)
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed


def summarize_vector(data: Any) -> dict[str, Any]:
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        return {}
    geometry_types: dict[str, int] = {}
    for feature in features:
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        kind = geometry.get("type") if isinstance(geometry, dict) else None
        geometry_types[str(kind)] = geometry_types.get(str(kind), 0) + 1
    return {"feature_count": len(features), "geometry_types": geometry_types}


def raster_shape(data: Any) -> list[int]:
    shape: list[int] = []
    while isinstance(data, list) and len(shape) < 4:
        shape.append(len(data))
        data = data[0] if data else None
    return shape


def summarize_raster(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    summary: dict[str, Any] = {"shape": raster_shape(data.get("data"))}
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        for key in ("crs", "nodata", "transform", "bounds", "band_count", "dtype"):
            if key in metadata:
                summary[key] = metadata[key]
    return summary

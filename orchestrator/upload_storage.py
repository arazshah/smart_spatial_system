"""
orchestrator.upload_storage

File upload storage for operational Smart Spatial System usage.

MVP capabilities:
    - Store uploaded files under uploads/{upload_id}/
    - Store metadata.json
    - Parse JSON uploads for direct pipeline usage
    - Keep non-JSON raster files for future rasterio/local_raster_loader integration

Supported immediate flow:
    POST /uploads/raster with a JSON raster payload
    -> upload_id
    POST /query with {"inputs": {"raster_ref": upload_id}}
    -> service resolves JSON content and runs pipeline

Future:
    - GeoTIFF parsing via rasterio
    - COG validation
    - Upload to S3/MinIO
    - PostGIS/vector upload support
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UPLOAD_STORAGE_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class UploadStorageConfig:
    root_dir: str | Path = "uploads"
    max_size_bytes: int = 100 * 1024 * 1024
    allowed_extensions: tuple[str, ...] = (
        ".json",
        ".geojson",
        ".tif",
        ".tiff",
        ".gpkg",
        ".zip",
        ".shp",
        ".kml",
        ".csv",
        ".tsv",
    )
    indent: int = 2
    ensure_ascii: bool = False

    def __post_init__(self) -> None:
        if self.max_size_bytes <= 0:
            raise ValueError("max_size_bytes must be > 0.")

        if self.indent < 0:
            raise ValueError("indent must be >= 0.")

        if not self.allowed_extensions:
            raise ValueError("allowed_extensions must not be empty.")


class UploadStorageError(RuntimeError):
    pass


class UploadStorage:
    """
    File-based upload storage.
    """

    def __init__(
        self,
        config: UploadStorageConfig | None = None,
    ) -> None:
        self.config = config or UploadStorageConfig()
        self.root_dir = Path(self.config.root_dir)

    def save_upload(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None = None,
        kind: str = "raster",
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not filename:
            raise UploadStorageError("filename is required.")

        if len(content) > self.config.max_size_bytes:
            raise UploadStorageError(
                f"Uploaded file is too large: {len(content)} bytes."
            )

        extension = Path(filename).suffix.lower()

        if extension not in self.config.allowed_extensions:
            raise UploadStorageError(
                f"Unsupported upload extension: {extension}"
            )

        upload_id = f"upl-{uuid.uuid4()}"
        directory = self.upload_dir(upload_id)
        directory.mkdir(parents=True, exist_ok=True)

        safe_filename = _safe_filename(filename)
        file_path = directory / safe_filename

        try:
            file_path.write_bytes(content)
        except OSError as exc:
            raise UploadStorageError(
                f"Failed to store uploaded file: {exc}"
            ) from exc

        parsed_json_available = False
        parsed_json_error = None

        if extension in {".json", ".geojson"}:
            try:
                json.loads(content.decode("utf-8"))
                parsed_json_available = True
            except Exception as exc:
                parsed_json_error = str(exc)

        sha256 = hashlib.sha256(content).hexdigest()

        media_type = (
            content_type
            or mimetypes.guess_type(safe_filename)[0]
            or "application/octet-stream"
        )

        metadata = {
            "schema_version": UPLOAD_STORAGE_SCHEMA_VERSION,
            "upload_id": upload_id,
            "kind": kind,
            "filename": safe_filename,
            "original_filename": filename,
            "extension": extension,
            "content_type": media_type,
            "size_bytes": len(content),
            "sha256": sha256,
            "stored_at": datetime.now(timezone.utc).isoformat(),
            "directory": str(directory),
            "path": str(file_path),
            "parsed_json_available": parsed_json_available,
            "parsed_json_error": parsed_json_error,
            "user_context": _json_safe(user_context or {}),
        }

        self._write_json(
            directory / "metadata.json",
            metadata,
        )

        return metadata


    def save_external_source(
        self,
        *,
        source_type: str,
        kind: str,
        display_name: str,
        payload: dict[str, Any],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Store a non-file data source registration as metadata.

        Used by DSM for sources such as CSV/Table URL, WMS, WFS, PostGIS, API.
        It creates an upload-like record so the existing project/uploads/data-source
        pipeline can list, preview, edit and delete it consistently.
        """
        source_type = str(source_type or "").strip().lower()
        kind = str(kind or "external").strip().lower()
        display_name = str(display_name or "").strip()

        if not source_type:
            raise UploadStorageError("source_type is required.")

        if not display_name:
            display_name = source_type.upper()

        upload_id = f"upl-{uuid.uuid4()}"
        directory = self.upload_dir(upload_id)
        directory.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()
        safe_payload = _json_safe(payload or {})

        metadata = {
            "schema_version": UPLOAD_STORAGE_SCHEMA_VERSION,
            "upload_id": upload_id,
            "kind": kind,
            "source_type": source_type,
            "external": True,
            "display_name": display_name,
            "description": safe_payload.get("description") or "",
            "tags": safe_payload.get("tags") or [],
            "filename": None,
            "original_filename": None,
            "extension": None,
            "content_type": "application/json",
            "size_bytes": 0,
            "sha256": None,
            "stored_at": now,
            "updated_at": now,
            "directory": str(directory),
            "path": None,
            "parsed_json_available": False,
            "parsed_json_error": None,
            "project_id": project_id,
            "status": "ready",
            "connection": safe_payload,
            "user_context": {
                "source_type": source_type,
                "project_id": project_id,
            },
        }

        self._write_json(
            directory / "metadata.json",
            metadata,
        )

        return metadata

    def upload_dir(
        self,
        upload_id: str,
    ) -> Path:
        return self.root_dir / _safe_upload_id(upload_id)

    def metadata_path(
        self,
        upload_id: str,
    ) -> Path:
        return self.upload_dir(upload_id) / "metadata.json"

    def read_metadata(
        self,
        upload_id: str,
    ) -> dict[str, Any]:
        path = self.metadata_path(upload_id)

        if not path.exists():
            raise UploadStorageError(
                f"Unknown upload_id: {upload_id}"
            )

        return self._read_json(path)

    def list_uploads(self) -> list[dict[str, Any]]:
        if not self.root_dir.exists():
            return []

        items: list[dict[str, Any]] = []

        for child in sorted(self.root_dir.iterdir()):
            if not child.is_dir():
                continue

            metadata_file = child / "metadata.json"

            if not metadata_file.exists():
                continue

            try:
                items.append(self._read_json(metadata_file))
            except UploadStorageError:
                continue

        return items

    def get_file_path(
        self,
        upload_id: str,
    ) -> Path:
        metadata = self.read_metadata(upload_id)

        directory = self.upload_dir(upload_id).resolve()
        filename = metadata.get("filename")

        if not filename:
            raise UploadStorageError(
                f"Upload metadata missing filename: {upload_id}"
            )

        path = (directory / filename).resolve()

        try:
            path.relative_to(directory)
        except ValueError as exc:
            raise UploadStorageError("Invalid upload file path.") from exc

        if not path.exists() or not path.is_file():
            raise UploadStorageError(
                f"Uploaded file does not exist: {upload_id}"
            )

        return path

    def read_json_content(
        self,
        upload_id: str,
    ) -> Any:
        metadata = self.read_metadata(upload_id)

        if not metadata.get("parsed_json_available"):
            raise UploadStorageError(
                f"Upload is not a parsed JSON payload: {upload_id}"
            )

        path = self.get_file_path(upload_id)

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise UploadStorageError(
                f"Invalid JSON upload content: {upload_id}: {exc}"
            ) from exc
        except OSError as exc:
            raise UploadStorageError(
                f"Failed to read upload content: {upload_id}: {exc}"
            ) from exc

    def delete_upload(
        self,
        upload_id: str,
    ) -> dict[str, Any]:
        metadata = self.read_metadata(upload_id)
        directory = self.upload_dir(upload_id)

        if not directory.exists() or not directory.is_dir():
            raise UploadStorageError(f"Upload directory not found: {upload_id}")

        try:
            shutil.rmtree(directory)
        except OSError as exc:
            raise UploadStorageError(
                f"Failed to delete upload {upload_id}: {exc}"
            ) from exc

        return metadata

    def get_media_type(
        self,
        upload_id: str,
    ) -> str:
        metadata = self.read_metadata(upload_id)
        return str(metadata.get("content_type") or "application/octet-stream")

    def update_metadata(
        self,
        upload_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise UploadStorageError("Metadata patch must be an object.")

        metadata = self.read_metadata(upload_id)

        allowed_keys = {
            "display_name",
            "description",
            "tags",
        }

        clean_patch: dict[str, Any] = {}

        for key, value in patch.items():
            if key not in allowed_keys:
                continue

            if key == "display_name":
                text = str(value or "").strip()
                clean_patch[key] = text or None

            elif key == "description":
                clean_patch[key] = str(value or "").strip()

            elif key == "tags":
                if value is None:
                    clean_patch[key] = []
                elif isinstance(value, (list, tuple, set)):
                    clean_patch[key] = [
                        str(item).strip()
                        for item in value
                        if str(item).strip()
                    ]
                else:
                    clean_patch[key] = [
                        str(value).strip()
                    ] if str(value).strip() else []

        metadata.update(clean_patch)
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._write_json(self.metadata_path(upload_id), metadata)
        return metadata

    def _write_json(
        self,
        path: Path,
        payload: Any,
    ) -> None:
        try:
            path.write_text(
                json.dumps(
                    _json_safe(payload),
                    ensure_ascii=self.config.ensure_ascii,
                    indent=self.config.indent,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            raise UploadStorageError(
                f"Failed to write upload metadata: {exc}"
            ) from exc

    @staticmethod
    def _read_json(
        path: Path,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise UploadStorageError(
                f"Invalid upload metadata JSON: {path}: {exc}"
            ) from exc
        except OSError as exc:
            raise UploadStorageError(
                f"Failed to read upload metadata: {path}: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise UploadStorageError(
                f"Upload metadata must be object: {path}"
            )

        return payload


def _safe_upload_id(value: str) -> str:
    text = str(value).strip()

    if not text.startswith("upl-"):
        raise UploadStorageError("Invalid upload_id.")

    if not re.fullmatch(r"upl-[A-Za-z0-9_.-]+", text):
        raise UploadStorageError("Invalid upload_id.")

    return text


def _safe_filename(value: str) -> str:
    filename = Path(str(value)).name
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename)
    filename = filename.strip("._")

    return filename or "upload.bin"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _json_safe(item)
            for item in value
        ]

    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass

    if is_dataclass(value):
        try:
            return _json_safe(asdict(value))
        except Exception:
            pass

    payload = getattr(value, "__dict__", None)

    if isinstance(payload, dict) and payload:
        return _json_safe(payload)

    return repr(value)

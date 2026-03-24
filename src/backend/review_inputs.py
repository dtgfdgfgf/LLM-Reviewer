"""Normalize local path-based review inputs for folder and multi-file review modes."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.tools.codebase import MAX_FILE_SIZE_BYTES, is_supported_text_file

DEFAULT_FOCUS_PROMPT = (
    "Review this material thoroughly and produce a useful engineering report. "
    "Prioritize correctness, maintainability, architecture, reliability, "
    "and user impact where relevant."
)


@dataclass(frozen=True)
class NormalizedReviewInput:
    """Canonical local review input used by the orchestration layer."""

    source_mode: Literal["folder", "files", "uploaded_files"]
    review_root: str
    selected_paths: list[str]
    focus_prompt: str
    source_label: str
    cleanup_root: str | None = None


def _sanitize_uploaded_name(raw_name: str, index: int) -> str:
    name = Path(raw_name or "").name.strip()
    if not name:
        name = f"upload-{index + 1}.txt"
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    return safe_name[:120] or f"upload-{index + 1}.txt"


def _dedupe_name(name: str, used_names: set[str]) -> str:
    if name not in used_names:
        used_names.add(name)
        return name

    stem = Path(name).stem
    suffix = Path(name).suffix
    counter = 2
    while True:
        candidate = f"{stem}-{counter}{suffix}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        counter += 1


def _write_uploaded_files(
    uploaded_files: list[dict[str, Any]],
) -> tuple[Path, list[str], str]:
    temp_root = Path(tempfile.mkdtemp(prefix="review-upload-"))
    used_names: set[str] = set()
    selected_paths: list[str] = []
    try:
        for index, file_data in enumerate(uploaded_files):
            name = _dedupe_name(
                _sanitize_uploaded_name(str(file_data.get("name", "")), index), used_names
            )
            content = str(file_data.get("content", ""))
            if "\x00" in content:
                raise ValueError(
                    f"Unsupported file type for review: {name} (contains binary null bytes)"
                )
            if len(content.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
                raise ValueError(
                    "Unsupported file type for review: "
                    f"{name} (exceeds {MAX_FILE_SIZE_BYTES} bytes)"
                )

            target = temp_root / name
            target.write_text(content, encoding="utf-8")

            supported, reason = is_supported_text_file(target)
            if not supported:
                raise ValueError(f"Unsupported file type for review: {name} ({reason})")

            selected_paths.append(name)
    except Exception:
        import shutil

        shutil.rmtree(temp_root, ignore_errors=True)
        raise

    label = (
        selected_paths[0] if len(selected_paths) == 1 else f"{len(selected_paths)} uploaded files"
    )
    return temp_root, selected_paths, label


def normalize_local_review_input(
    *,
    source_mode: Literal["folder", "files", "uploaded_files"],
    folder_path: str | None,
    file_paths: list[str] | None,
    uploaded_files: list[dict[str, Any]] | None,
    focus_prompt: str | None,
    legacy_task: str | None = None,
) -> NormalizedReviewInput:
    """Validate and normalize a local path-based review request."""
    prompt = (focus_prompt or legacy_task or "").strip() or DEFAULT_FOCUS_PROMPT

    if source_mode == "folder":
        if not folder_path:
            raise ValueError("folder_path is required when source_mode is 'folder'")
        root = Path(folder_path).expanduser()
        if not root.is_absolute():
            raise ValueError("folder_path must be an absolute path")
        if not root.exists():
            raise FileNotFoundError(f"Path does not exist: {folder_path}")
        if not root.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {folder_path}")
        return NormalizedReviewInput(
            source_mode="folder",
            review_root=str(root.resolve()),
            selected_paths=[],
            focus_prompt=prompt,
            source_label=root.name or str(root),
        )

    if source_mode == "files":
        if folder_path:
            raise ValueError("folder_path is not allowed when source_mode is 'files'")
        if uploaded_files:
            raise ValueError("uploaded_files is not allowed when source_mode is 'files'")
        if not file_paths:
            raise ValueError("file_paths must be non-empty when source_mode is 'files'")

        resolved_files: list[Path] = []
        for raw_path in file_paths:
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                raise ValueError("file_paths must contain absolute paths only")
            if not candidate.exists():
                raise FileNotFoundError(f"File does not exist: {raw_path}")
            if not candidate.is_file():
                raise IsADirectoryError(f"Expected a file path, got: {raw_path}")
            supported, reason = is_supported_text_file(candidate)
            if not supported:
                raise ValueError(f"Unsupported file type for review: {raw_path} ({reason})")
            resolved_files.append(candidate.resolve())

        try:
            common_root = Path(os.path.commonpath([str(path.parent) for path in resolved_files]))
        except ValueError as exc:
            raise ValueError("Selected files must be on the same drive") from exc

        selected_paths = sorted(
            {str(path.relative_to(common_root)).replace("\\", "/") for path in resolved_files}
        )

        label = (
            resolved_files[0].name if len(resolved_files) == 1 else f"{len(resolved_files)} files"
        )
        return NormalizedReviewInput(
            source_mode="files",
            review_root=str(common_root.resolve()),
            selected_paths=selected_paths,
            focus_prompt=prompt,
            source_label=label,
        )

    if source_mode != "uploaded_files":
        raise ValueError(f"Unsupported source_mode: {source_mode}")

    if folder_path:
        raise ValueError("folder_path is not allowed when source_mode is 'uploaded_files'")
    if file_paths:
        raise ValueError("file_paths is not allowed when source_mode is 'uploaded_files'")
    if not uploaded_files:
        raise ValueError("uploaded_files must be non-empty when source_mode is 'uploaded_files'")

    temp_root, selected_paths, label = _write_uploaded_files(uploaded_files)
    return NormalizedReviewInput(
        source_mode="uploaded_files",
        review_root=str(temp_root.resolve()),
        selected_paths=selected_paths,
        focus_prompt=prompt,
        source_label=label,
        cleanup_root=str(temp_root.resolve()),
    )

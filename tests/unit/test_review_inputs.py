import shutil
from pathlib import Path

import pytest

from backend.review_inputs import DEFAULT_FOCUS_PROMPT, normalize_local_review_input


class TestNormalizeLocalReviewInput:
    def test_folder_mode_uses_default_focus_prompt(self, tmp_codebase: Path):
        normalized = normalize_local_review_input(
            source_mode="folder",
            folder_path=str(tmp_codebase),
            file_paths=None,
            uploaded_files=None,
            focus_prompt=None,
        )

        assert normalized.source_mode == "folder"
        assert normalized.review_root == str(tmp_codebase.resolve())
        assert normalized.selected_paths == []
        assert normalized.focus_prompt == DEFAULT_FOCUS_PROMPT

    def test_files_mode_uses_common_ancestor_and_relative_paths(self, tmp_codebase: Path):
        files = [
            str(tmp_codebase / "src" / "backend" / "auth.py"),
            str(tmp_codebase / "src" / "backend" / "main.py"),
        ]

        normalized = normalize_local_review_input(
            source_mode="files",
            folder_path=None,
            file_paths=files,
            uploaded_files=None,
            focus_prompt="Focus on reliability and maintainability.",
        )

        assert normalized.source_mode == "files"
        assert normalized.review_root == str((tmp_codebase / "src" / "backend").resolve())
        assert normalized.selected_paths == ["auth.py", "main.py"]
        assert normalized.focus_prompt == "Focus on reliability and maintainability."

    def test_files_mode_rejects_binary_files(self, tmp_codebase: Path):
        binary = tmp_codebase / "diagram.png"
        binary.write_bytes(b"\x89PNG\r\n\x1a\n")

        with pytest.raises(ValueError, match="Unsupported file type"):
            normalize_local_review_input(
                source_mode="files",
                folder_path=None,
                file_paths=[str(binary)],
                uploaded_files=None,
                focus_prompt=None,
            )

    def test_uploaded_files_mode_writes_temp_workspace(self):
        normalized = normalize_local_review_input(
            source_mode="uploaded_files",
            folder_path=None,
            file_paths=None,
            uploaded_files=[
                {"name": "notes.md", "content": "# Heading\nhello"},
                {"name": "src/app.py", "content": "print('ok')\n"},
            ],
            focus_prompt=None,
        )

        try:
            review_root = Path(normalized.review_root)
            assert normalized.source_mode == "uploaded_files"
            assert normalized.selected_paths == ["notes.md", "app.py"]
            assert normalized.cleanup_root == normalized.review_root
            assert review_root.exists()
            assert (review_root / "notes.md").read_text(encoding="utf-8") == "# Heading\nhello"
            assert (review_root / "app.py").read_text(encoding="utf-8") == "print('ok')\n"
        finally:
            if normalized.cleanup_root:
                shutil.rmtree(normalized.cleanup_root, ignore_errors=True)

    def test_uploaded_files_mode_dedupes_names(self):
        normalized = normalize_local_review_input(
            source_mode="uploaded_files",
            folder_path=None,
            file_paths=None,
            uploaded_files=[
                {"name": "report.md", "content": "# one"},
                {"name": "report.md", "content": "# two"},
            ],
            focus_prompt="Focus on duplicated uploads.",
        )

        try:
            assert normalized.selected_paths == ["report.md", "report-2.md"]
        finally:
            if normalized.cleanup_root:
                shutil.rmtree(normalized.cleanup_root, ignore_errors=True)

    def test_uploaded_files_mode_rejects_binary_content(self):
        with pytest.raises(ValueError, match="binary null bytes"):
            normalize_local_review_input(
                source_mode="uploaded_files",
                folder_path=None,
                file_paths=None,
                uploaded_files=[{"name": "payload.txt", "content": "abc\x00def"}],
                focus_prompt=None,
            )

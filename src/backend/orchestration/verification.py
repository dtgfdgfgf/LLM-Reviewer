"""
Deterministic runtime verification for strict LLM-native reviews.

The strict reviewer treats runtime evidence as first-class input. This module
discovers likely validation entrypoints across CI/task runners/manifests/docs,
classifies them by relevance, runs bounded non-mutating checks, and emits
structured metadata that downstream reviewers can reason about safely.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from backend.logging_config import get_logger
from backend.orchestration.strict_types import (
    EvidenceMode,
    FindingKind,
    VerificationApplicability,
    VerificationCheckResult,
    VerificationRole,
    VerificationSummary,
)

logger = get_logger("verification")

_MAX_EXCERPT_CHARS = 1800
_CHECK_TIMEOUT_S = 120
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".pytest_cache",
}
_DOC_NAMES = {"readme.md", "spec.md", "quickstart.md"}
_RUNTIME_CHECK_NAMES = {"tests", "lint", "typecheck", "build", "security_scan"}
_TEST_FILE_NAME = re.compile(r"(^test_.*\.py$)|(_test\.py$)", re.IGNORECASE)
_OPT_IN_PATTERNS = (
    "skipped by default",
    "deselect with -m",
    "pytest.skip(",
    "requires live",
    "requires database",
    "requires db",
    "opt-in",
)
_DB_HINTS = ("db", "database", "sqlite", "postgres", "sqlalchemy", "session")
_MOCK_PATTERN = re.compile(r"\b(MagicMock|AsyncMock|Mock|monkeypatch)\b|patch\(")
_COMMAND_PATTERNS = {
    "tests": ("pytest", "npm test", "npm run test", "make test", "just test", "tox", "nox"),
    "lint": ("ruff", "npm run lint", "make lint", "just lint"),
    "typecheck": ("mypy", "npm run typecheck", "tsc", "make typecheck", "just typecheck"),
    "build": ("python -m build", "npm run build", "vite build", "make build", "just build"),
    "security_scan": ("npm audit", "pip-audit"),
}


@dataclass
class DiscoveredCheck:
    name: str
    display_name: str
    command: list[str] | None
    working_dir: Path
    scope: str
    source: set[str] = field(default_factory=set)
    kind_hint: FindingKind = FindingKind.RUNTIME_FAILURE
    confidence: float = 0.6
    status_override: str | None = None
    summary_hint: str | None = None
    output_excerpt: str | None = None
    origin_priority: int = 0


def _excerpt(text: str, limit: int = _MAX_EXCERPT_CHARS) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "\n[...已截斷]"


def _iter_repo_paths(root: Path) -> list[Path]:
    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        current_path = Path(current_root)
        for filename in filenames:
            files.append(current_path / filename)
    return files


def _relpath(path: Path, root: Path) -> str:
    if path == root:
        return "."
    return path.relative_to(root).as_posix()


def _scope_for_dir(path: Path, root: Path) -> str:
    rel = _relpath(path, root)
    if rel == ".":
        return "repo-wide"
    parts = rel.lower().split("/")
    if "frontend" in parts:
        return "frontend"
    if "backend" in parts:
        return "backend"
    if any(part in {"db", "database", "migrations"} for part in parts):
        return "db"
    if any(part in {"e2e", "integration"} for part in parts):
        return "e2e"
    return f"subproject:{parts[-1]}"


def _scope_for_file(path: str) -> str:
    normalized = path.replace("\\", "/").lower()
    parts = normalized.split("/")
    if "frontend" in parts:
        return "frontend"
    if "backend" in parts:
        return "backend"
    if any(part in {"db", "database", "migrations"} for part in parts):
        return "db"
    if any(part in {"e2e", "integration"} for part in parts):
        return "e2e"
    return "repo-wide"


def _selected_scopes(selected_paths: list[str] | None) -> set[str]:
    if not selected_paths:
        return set()
    return {_scope_for_file(path) for path in selected_paths if path}


def _scope_is_relevant(scope: str, selected_scopes: set[str]) -> bool:
    if not selected_scopes or scope == "repo-wide":
        return True
    if scope in selected_scopes:
        return True
    if scope.startswith("subproject:"):
        sub_name = scope.split(":", 1)[1]
        return any(selected == scope or selected == sub_name for selected in selected_scopes)
    return False


def _candidate_key(candidate: DiscoveredCheck, root: Path) -> tuple[str, str, FindingKind]:
    return candidate.name, _relpath(candidate.working_dir, root), candidate.kind_hint


def _add_candidate(
    candidates: dict[tuple[str, str, FindingKind], DiscoveredCheck],
    root: Path,
    candidate: DiscoveredCheck,
) -> None:
    key = _candidate_key(candidate, root)
    existing = candidates.get(key)
    if existing is None:
        candidates[key] = candidate
        return
    existing.source.update(candidate.source)
    existing.confidence = max(existing.confidence, candidate.confidence)
    if candidate.origin_priority >= existing.origin_priority:
        existing.command = candidate.command
        existing.display_name = candidate.display_name
        existing.summary_hint = candidate.summary_hint or existing.summary_hint
        existing.output_excerpt = candidate.output_excerpt or existing.output_excerpt
        existing.status_override = candidate.status_override or existing.status_override
        existing.origin_priority = candidate.origin_priority


def _load_package_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_pyproject(path: Path) -> dict | None:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _pyproject_mentions(pyproject: dict | None, key: str) -> bool:
    if not pyproject:
        return False
    return key.lower() in json.dumps(pyproject).lower()


def _pyproject_has_build_system(pyproject: dict | None) -> bool:
    return bool(pyproject and pyproject.get("build-system"))


def _has_tests(pyproject: dict | None, workdir: Path) -> bool:
    return (workdir / "tests").exists() or _pyproject_mentions(pyproject, "pytest")


def _has_ruff_config(pyproject: dict | None, workdir: Path) -> bool:
    return any((workdir / name).exists() for name in (".ruff.toml", "ruff.toml")) or _pyproject_mentions(
        pyproject, "ruff"
    )


def _safe_script_command(
    scripts: dict[str, str] | None,
    script_name: str,
    runner: list[str],
) -> tuple[str, list[str]] | None:
    if not scripts or script_name not in scripts:
        return None
    body = scripts[script_name].lower()
    if "--fix" in body or " fix " in body:
        return None
    return body, [*runner, "run", script_name]


def _discover_manifest_checks(root: Path) -> dict[tuple[str, str, FindingKind], DiscoveredCheck]:
    candidates: dict[tuple[str, str, FindingKind], DiscoveredCheck] = {}
    uv = shutil.which("uv")
    pytest = shutil.which("pytest")
    ruff = shutil.which("ruff")
    mypy = shutil.which("mypy")
    python = shutil.which("python")
    npm = shutil.which("npm")
    pip_audit = shutil.which("pip-audit")

    for path in _iter_repo_paths(root):
        if path.name == "pyproject.toml":
            pyproject = _load_pyproject(path)
            if not pyproject:
                continue
            workdir = path.parent
            rel = _relpath(path, root)
            scope = _scope_for_dir(workdir, root)
            if uv:
                if _has_ruff_config(pyproject, workdir):
                    _add_candidate(
                        candidates,
                        root,
                        DiscoveredCheck(
                            name="lint",
                            display_name="uv run ruff check .",
                            command=[uv, "run", "ruff", "check", "."],
                            working_dir=workdir,
                            scope=scope,
                            source={f"manifest:{rel}"},
                            confidence=0.75,
                            origin_priority=2,
                        ),
                    )
                if _has_tests(pyproject, workdir):
                    _add_candidate(
                        candidates,
                        root,
                        DiscoveredCheck(
                            name="tests",
                            display_name="uv run pytest -q",
                            command=[uv, "run", "pytest", "-q"],
                            working_dir=workdir,
                            scope=scope,
                            source={f"manifest:{rel}"},
                            confidence=0.8,
                            origin_priority=2,
                        ),
                    )
                if mypy or _pyproject_mentions(pyproject, "mypy"):
                    _add_candidate(
                        candidates,
                        root,
                        DiscoveredCheck(
                            name="typecheck",
                            display_name="uv run mypy .",
                            command=[uv, "run", "mypy", "."],
                            working_dir=workdir,
                            scope=scope,
                            source={f"manifest:{rel}"},
                            confidence=0.7,
                            origin_priority=2,
                        ),
                    )
            else:
                if ruff and _has_ruff_config(pyproject, workdir):
                    _add_candidate(
                        candidates,
                        root,
                        DiscoveredCheck(
                            name="lint",
                            display_name="ruff check .",
                            command=[ruff, "check", "."],
                            working_dir=workdir,
                            scope=scope,
                            source={f"manifest:{rel}"},
                            confidence=0.72,
                            origin_priority=2,
                        ),
                    )
                if pytest and _has_tests(pyproject, workdir):
                    _add_candidate(
                        candidates,
                        root,
                        DiscoveredCheck(
                            name="tests",
                            display_name="pytest -q",
                            command=[pytest, "-q"],
                            working_dir=workdir,
                            scope=scope,
                            source={f"manifest:{rel}"},
                            confidence=0.76,
                            origin_priority=2,
                        ),
                    )
                if mypy and _pyproject_mentions(pyproject, "mypy"):
                    _add_candidate(
                        candidates,
                        root,
                        DiscoveredCheck(
                            name="typecheck",
                            display_name="mypy .",
                            command=[mypy, "."],
                            working_dir=workdir,
                            scope=scope,
                            source={f"manifest:{rel}"},
                            confidence=0.68,
                            origin_priority=2,
                        ),
                    )
            if python and _pyproject_has_build_system(pyproject):
                _add_candidate(
                    candidates,
                    root,
                    DiscoveredCheck(
                        name="build",
                        display_name="python -m build --sdist --wheel",
                        command=[python, "-m", "build", "--sdist", "--wheel"],
                        working_dir=workdir,
                        scope=scope,
                        source={f"manifest:{rel}"},
                        confidence=0.7,
                        origin_priority=2,
                    ),
                )
            if pip_audit:
                _add_candidate(
                    candidates,
                    root,
                    DiscoveredCheck(
                        name="security_scan",
                        display_name="pip-audit",
                        command=[pip_audit],
                        working_dir=workdir,
                        scope=scope,
                        source={f"manifest:{rel}"},
                        confidence=0.55,
                        origin_priority=1,
                    ),
                )
        elif path.name == "package.json":
            package_json = _load_package_json(path)
            if not package_json:
                continue
            workdir = path.parent
            rel = _relpath(path, root)
            scope = _scope_for_dir(workdir, root)
            scripts = package_json.get("scripts", {})
            if npm:
                for name, display_name in (
                    ("lint", "npm run lint"),
                    ("typecheck", "npm run typecheck"),
                    ("test", "npm run test"),
                    ("build", "npm run build"),
                ):
                    script = _safe_script_command(scripts, name, [npm])
                    if script:
                        _, command = script
                        mapped_name = "tests" if name == "test" else name
                        _add_candidate(
                            candidates,
                            root,
                            DiscoveredCheck(
                                name=mapped_name,
                                display_name=display_name,
                                command=command,
                                working_dir=workdir,
                                scope=scope,
                                source={f"manifest:{rel}"},
                                confidence=0.72,
                                origin_priority=2,
                            ),
                        )
                if (workdir / "package-lock.json").exists():
                    _add_candidate(
                        candidates,
                        root,
                        DiscoveredCheck(
                            name="security_scan",
                            display_name="npm audit --json",
                            command=[npm, "audit", "--json"],
                            working_dir=workdir,
                            scope=scope,
                            source={f"manifest:{rel}"},
                            confidence=0.52,
                            origin_priority=1,
                        ),
                    )
    return candidates


def _make_candidate(
    *,
    root: Path,
    workdir: Path,
    name: str,
    display_name: str,
    command: list[str] | None,
    source_label: str,
    priority: int,
) -> DiscoveredCheck:
    return DiscoveredCheck(
        name=name,
        display_name=display_name,
        command=command,
        working_dir=workdir,
        scope=_scope_for_dir(workdir, root),
        source={source_label},
        confidence=0.85,
        origin_priority=priority,
    )


def _discover_task_runner_checks(root: Path) -> dict[tuple[str, str, FindingKind], DiscoveredCheck]:
    candidates: dict[tuple[str, str, FindingKind], DiscoveredCheck] = {}
    make = shutil.which("make")
    just = shutil.which("just")
    tox = shutil.which("tox")
    nox = shutil.which("nox")

    for path in _iter_repo_paths(root):
        rel = _relpath(path, root)
        workdir = path.parent
        if path.name.lower() == "makefile" and make:
            content = path.read_text(encoding="utf-8", errors="ignore")
            targets = {
                match.group(1).lower()
                for match in re.finditer(r"^([A-Za-z0-9_.-]+)\s*:", content, re.MULTILINE)
            }
            for target, name in (
                ("test", "tests"),
                ("lint", "lint"),
                ("typecheck", "typecheck"),
                ("build", "build"),
            ):
                if target in targets:
                    _add_candidate(
                        candidates,
                        root,
                        _make_candidate(
                            root=root,
                            workdir=workdir,
                            name=name,
                            display_name=f"make {target}",
                            command=[make, target],
                            source_label=f"task:{rel}",
                            priority=4,
                        ),
                    )
        elif path.name.lower() == "justfile" and just:
            content = path.read_text(encoding="utf-8", errors="ignore")
            targets = {
                match.group(1).lower()
                for match in re.finditer(r"^([A-Za-z0-9_.-]+)\s*:", content, re.MULTILINE)
            }
            for target, name in (
                ("test", "tests"),
                ("lint", "lint"),
                ("typecheck", "typecheck"),
                ("build", "build"),
            ):
                if target in targets:
                    _add_candidate(
                        candidates,
                        root,
                        _make_candidate(
                            root=root,
                            workdir=workdir,
                            name=name,
                            display_name=f"just {target}",
                            command=[just, target],
                            source_label=f"task:{rel}",
                            priority=4,
                        ),
                    )
        elif path.name.lower() == "tox.ini" and tox:
            content = path.read_text(encoding="utf-8", errors="ignore")
            if "[testenv" in content.lower():
                _add_candidate(
                    candidates,
                    root,
                    _make_candidate(
                        root=root,
                        workdir=workdir,
                        name="tests",
                        display_name="tox -q",
                        command=[tox, "-q"],
                        source_label=f"task:{rel}",
                        priority=4,
                    ),
                )
        elif path.name.lower() == "noxfile.py" and nox:
            content = path.read_text(encoding="utf-8", errors="ignore")
            sessions = {
                name
                for name, token in (
                    ("tests", "pytest"),
                    ("lint", "ruff"),
                    ("typecheck", "mypy"),
                )
                if token in content.lower()
            }
            for name in sessions:
                display_name = {
                    "tests": "nox -s tests",
                    "lint": "nox -s lint",
                    "typecheck": "nox -s typecheck",
                }[name]
                _add_candidate(
                    candidates,
                    root,
                    _make_candidate(
                        root=root,
                        workdir=workdir,
                        name=name,
                        display_name=display_name,
                        command=[nox, "-s", name],
                        source_label=f"task:{rel}",
                        priority=4,
                    ),
                )
    return candidates


def _candidate_patterns(candidate: DiscoveredCheck) -> tuple[str, ...]:
    joined = " ".join(candidate.command or []).lower()
    generic = list(_COMMAND_PATTERNS.get(candidate.name, (candidate.name,)))
    if joined:
        generic.append(joined)
    if candidate.display_name:
        generic.append(candidate.display_name.lower())
    return tuple(dict.fromkeys(pattern for pattern in generic if pattern))


def _discover_reference_files(root: Path) -> tuple[list[Path], list[Path]]:
    ci_files: list[Path] = []
    doc_files: list[Path] = []
    for path in _iter_repo_paths(root):
        rel = _relpath(path, root).lower()
        if rel.startswith(".github/workflows/") and path.suffix.lower() in {".yml", ".yaml"}:
            ci_files.append(path)
            continue
        if path.suffix.lower() == ".md" and (
            path.name.lower() in _DOC_NAMES or "docs/" in rel or "specs/" in rel
        ):
            doc_files.append(path)
    return ci_files, doc_files


def _reference_mentions_candidate(
    *,
    content: str,
    reference_path: Path,
    candidate: DiscoveredCheck,
    root: Path,
    candidates_by_name: dict[str, int],
) -> bool:
    lowered = content.lower()
    if not any(pattern in lowered for pattern in _candidate_patterns(candidate)):
        return False
    rel_dir = _relpath(candidate.working_dir, root)
    if rel_dir == ".":
        return True
    if rel_dir.lower() in lowered or rel_dir.replace("/", "\\").lower() in lowered:
        return True
    if f"working-directory: {rel_dir.lower()}" in lowered:
        return True
    if reference_path.name.lower() in _DOC_NAMES and candidates_by_name[candidate.name] == 1:
        return True
    return False


def _enrich_candidate_sources(
    candidates: dict[tuple[str, str, FindingKind], DiscoveredCheck],
    root: Path,
) -> None:
    if not candidates:
        return
    ci_files, doc_files = _discover_reference_files(root)
    candidates_by_name = Counter(candidate.name for candidate in candidates.values())
    for prefix, files in (("ci", ci_files), ("docs", doc_files)):
        for path in files:
            content = path.read_text(encoding="utf-8", errors="ignore")
            rel = _relpath(path, root)
            for candidate in candidates.values():
                if _reference_mentions_candidate(
                    content=content,
                    reference_path=path,
                    candidate=candidate,
                    root=root,
                    candidates_by_name=candidates_by_name,
                ):
                    candidate.source.add(f"{prefix}:{rel}")


def _discover_env_gated_checks(root: Path) -> list[DiscoveredCheck]:
    sources_by_scope: dict[str, list[str]] = defaultdict(list)
    evidence_by_scope: dict[str, list[str]] = defaultdict(list)

    for path in _iter_repo_paths(root):
        rel = _relpath(path, root)
        lowered_rel = rel.lower()
        if path.name == "pyproject.toml":
            content = path.read_text(encoding="utf-8", errors="ignore").lower()
            if "deselect with -m" in content or "integration:" in content:
                sources_by_scope["e2e"].append(f"manifest:{rel}")
                evidence_by_scope["e2e"].append(rel)
        if "tests/" not in lowered_rel and not _TEST_FILE_NAME.search(path.name):
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        lowered = content.lower()
        if not any(pattern in lowered for pattern in _OPT_IN_PATTERNS):
            continue
        scope = "db" if any(keyword in lowered for keyword in _DB_HINTS) else "e2e"
        sources_by_scope[scope].append(f"test:{rel}")
        evidence_by_scope[scope].append(rel)

    checks: list[DiscoveredCheck] = []
    for scope, sources in sources_by_scope.items():
        label = "DB-backed coverage" if scope == "db" else "Integration coverage"
        summary = (
            "預設測試 gate 未涵蓋 DB / persistence 路徑；相關案例需額外環境或額外旗標才會執行。"
            if scope == "db"
            else "預設測試 gate 未涵蓋 live integration / e2e 路徑；這些案例目前是 opt-in。"
        )
        evidence = _excerpt("\n".join(evidence_by_scope[scope][:5]))
        checks.append(
            DiscoveredCheck(
                name=f"{scope}_coverage",
                display_name=label,
                command=None,
                working_dir=root,
                scope=scope,
                source=set(sources[:5]),
                kind_hint=FindingKind.COVERAGE_GAP,
                confidence=0.84 if scope == "db" else 0.78,
                status_override="skipped",
                summary_hint=summary,
                output_excerpt=evidence,
                origin_priority=3,
            )
        )
    return checks


def _discover_label_mismatch_checks(root: Path) -> list[DiscoveredCheck]:
    integration_files: list[tuple[str, int]] = []
    for path in _iter_repo_paths(root):
        rel = _relpath(path, root)
        lowered_rel = rel.lower()
        if "tests/integration/" not in lowered_rel and not _TEST_FILE_NAME.search(path.name):
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        lowered = content.lower()
        if "@pytest.mark.integration" not in lowered and "integration tests" not in lowered_rel:
            continue
        mock_score = len(_MOCK_PATTERN.findall(content))
        if mock_score >= 4:
            integration_files.append((rel, mock_score))

    if not integration_files:
        return []

    integration_files.sort(key=lambda item: item[1], reverse=True)
    total_mock_score = sum(score for _, score in integration_files)
    top_files = [path for path, _ in integration_files[:5]]
    return [
        DiscoveredCheck(
            name="integration_label_fidelity",
            display_name="Integration label fidelity",
            command=None,
            working_dir=root,
            scope="e2e",
            source={f"test:{path}" for path in top_files},
            kind_hint=FindingKind.LABEL_MISMATCH,
            confidence=min(0.95, 0.58 + total_mock_score / 30),
            status_override="failed",
            summary_hint=(
                "多個標示為 integration 的測試主要以 patch / mock 取代內部邊界，"
                "較接近 controller 或 API-level with mocks。"
            ),
            output_excerpt=_excerpt("\n".join(top_files)),
            origin_priority=3,
        )
    ]


def discover_verification_checks(root: Path) -> list[DiscoveredCheck]:
    manifest_candidates = _discover_manifest_checks(root)
    task_candidates = _discover_task_runner_checks(root)
    for candidate in task_candidates.values():
        _add_candidate(manifest_candidates, root, candidate)
    for candidate in _discover_env_gated_checks(root):
        _add_candidate(manifest_candidates, root, candidate)
    for candidate in _discover_label_mismatch_checks(root):
        _add_candidate(manifest_candidates, root, candidate)
    _enrich_candidate_sources(manifest_candidates, root)
    return list(manifest_candidates.values())


def _classify_candidate(
    candidate: DiscoveredCheck,
    *,
    root: Path,
    selected_scopes: set[str],
) -> tuple[VerificationRole, VerificationApplicability, bool]:
    has_ci_or_task = any(source.startswith(("ci:", "task:")) for source in candidate.source)
    has_docs = any(source.startswith("docs:") for source in candidate.source)
    relevant = _scope_is_relevant(candidate.scope, selected_scopes)
    is_root = candidate.working_dir == root

    if candidate.kind_hint == FindingKind.COVERAGE_GAP:
        if has_ci_or_task or relevant or not selected_scopes:
            return VerificationRole.CANONICAL, VerificationApplicability.ENV_GATED, False
        return VerificationRole.SUPPLEMENTAL, VerificationApplicability.ENV_GATED, False

    if candidate.kind_hint == FindingKind.LABEL_MISMATCH:
        role = VerificationRole.SUPPLEMENTAL if relevant or not selected_scopes else VerificationRole.STALE_SUSPECT
        return role, VerificationApplicability.OPTIONAL, False

    if candidate.name == "security_scan" and not has_ci_or_task and not has_docs:
        role = VerificationRole.EXPLORATORY if relevant or not selected_scopes else VerificationRole.STALE_SUSPECT
        return role, VerificationApplicability.OPTIONAL, False

    if (has_ci_or_task and (relevant or candidate.scope == "repo-wide")) or (
        is_root and candidate.name in _RUNTIME_CHECK_NAMES
    ):
        return VerificationRole.CANONICAL, VerificationApplicability.REQUIRED, True
    if has_docs and relevant:
        return VerificationRole.CANONICAL, VerificationApplicability.REQUIRED, True
    if relevant or not selected_scopes:
        return VerificationRole.SUPPLEMENTAL, VerificationApplicability.OPTIONAL, False
    if has_ci_or_task or has_docs:
        return VerificationRole.SUPPLEMENTAL, VerificationApplicability.OPTIONAL, False
    return VerificationRole.STALE_SUSPECT, VerificationApplicability.OPTIONAL, False


def _check_label(check: VerificationCheckResult) -> str:
    title = check.display_name or check.name
    if check.scope != "repo-wide":
        return f"{title} [{check.scope}]"
    return title


def _build_runtime_summary(status: str, result_code: int | None = None) -> str:
    if status == "passed":
        return "檢查已通過。"
    if status == "failed" and result_code is not None:
        return f"檢查以狀態碼 {result_code} 結束。"
    if status == "unavailable":
        return "本機環境中找不到這個驗證指令。"
    return f"檢查狀態為 {status}。"


def _materialize_static_check(
    candidate: DiscoveredCheck,
    *,
    root: Path,
    selected_scopes: set[str],
) -> VerificationCheckResult:
    role, applicability, blocking = _classify_candidate(
        candidate,
        root=root,
        selected_scopes=selected_scopes,
    )
    return VerificationCheckResult(
        name=candidate.name,
        display_name=candidate.display_name,
        status=candidate.status_override or "skipped",
        command=None,
        working_dir=_relpath(candidate.working_dir, root),
        role=role,
        applicability=applicability,
        scope=candidate.scope,
        source=sorted(candidate.source),
        confidence=candidate.confidence,
        kind_hint=candidate.kind_hint,
        summary=candidate.summary_hint or "已偵測到補充驗證訊號。",
        output_excerpt=candidate.output_excerpt,
        blocking=blocking,
    )


def _run_command(
    root: Path,
    candidate: DiscoveredCheck,
    *,
    selected_scopes: set[str],
) -> VerificationCheckResult:
    role, applicability, blocking = _classify_candidate(
        candidate,
        root=root,
        selected_scopes=selected_scopes,
    )
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "CI": "1",
    }
    command = candidate.command or []
    try:
        result = subprocess.run(
            command,
            cwd=str(candidate.working_dir),
            capture_output=True,
            text=True,
            timeout=_CHECK_TIMEOUT_S,
            check=False,
            env=env,
        )
    except FileNotFoundError:
        return VerificationCheckResult(
            name=candidate.name,
            display_name=candidate.display_name,
            status="unavailable",
            command=" ".join(command),
            working_dir=_relpath(candidate.working_dir, root),
            role=role,
            applicability=applicability,
            scope=candidate.scope,
            source=sorted(candidate.source),
            confidence=candidate.confidence,
            kind_hint=candidate.kind_hint,
            summary="本機環境中找不到這個驗證指令。",
            blocking=blocking,
        )
    except subprocess.TimeoutExpired:
        return VerificationCheckResult(
            name=candidate.name,
            display_name=candidate.display_name,
            status="failed",
            command=" ".join(command),
            working_dir=_relpath(candidate.working_dir, root),
            role=role,
            applicability=applicability,
            scope=candidate.scope,
            source=sorted(candidate.source),
            confidence=candidate.confidence,
            kind_hint=candidate.kind_hint,
            summary=f"檢查在 {_CHECK_TIMEOUT_S} 秒後逾時。",
            blocking=blocking,
        )

    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    status = "passed" if result.returncode == 0 else "failed"
    return VerificationCheckResult(
        name=candidate.name,
        display_name=candidate.display_name,
        status=status,
        command=" ".join(command),
        working_dir=_relpath(candidate.working_dir, root),
        role=role,
        applicability=applicability,
        scope=candidate.scope,
        source=sorted(candidate.source),
        confidence=candidate.confidence,
        kind_hint=candidate.kind_hint,
        summary=_build_runtime_summary(status, result.returncode),
        output_excerpt=_excerpt(output),
        blocking=blocking,
    )


def _predicate_for_results(results: list[VerificationCheckResult]) -> str:
    required_failures = [
        check
        for check in results
        if check.blocking and check.status == "failed" and check.role == VerificationRole.CANONICAL
    ]
    unavailable_required = [
        check
        for check in results
        if check.applicability == VerificationApplicability.REQUIRED and check.status == "unavailable"
    ]
    coverage_gaps = [
        check
        for check in results
        if check.kind_hint == FindingKind.COVERAGE_GAP and check.status in {"failed", "skipped", "unavailable"}
    ]
    supplemental_failures = [
        check
        for check in results
        if check.status == "failed"
        and check.role in {VerificationRole.SUPPLEMENTAL, VerificationRole.EXPLORATORY}
    ]
    if required_failures:
        return "repo-wide blocking failures present"
    if unavailable_required:
        return "required validation evidence unavailable"
    if coverage_gaps:
        return "no canonical blocking failures, but significant coverage gaps remain"
    if supplemental_failures:
        return "no canonical blocking failures, but supplemental checks require review"
    return "no canonical blocking failures detected"


async def run_verification(
    root: str | Path,
    evidence_mode: EvidenceMode,
    selected_paths: list[str] | None = None,
) -> VerificationSummary:
    """
    Run deterministic checks when strict mode requests runtime evidence.

    `static_only` skips all checks.
    `static_first` and `static_runtime` run the same bounded command set. The
    distinction is consumed by orchestration when deciding whether missing
    runtime evidence should force escalation or merely inform the review.
    """
    if evidence_mode == EvidenceMode.STATIC_ONLY:
        return VerificationSummary(status="complete", checks=[], verdict_predicate="static-only mode")

    root_path = Path(root)
    selected_scopes = _selected_scopes(selected_paths)
    candidates = discover_verification_checks(root_path)
    if not candidates:
        return VerificationSummary(
            status="complete",
            checks=[
                VerificationCheckResult(
                    name="runtime_detection",
                    display_name="Runtime detection",
                    status="skipped",
                    working_dir=".",
                    role=VerificationRole.EXPLORATORY,
                    applicability=VerificationApplicability.UNKNOWN,
                    scope="repo-wide",
                    source=[],
                    confidence=0.4,
                    kind_hint=FindingKind.ENV_GAP,
                    summary="未偵測到可安全執行的 deterministic validation set；此 repo 可能未配置或目前環境不適用。",
                    blocking=False,
                )
            ],
            verdict_predicate="no deterministic validation set discovered",
        )

    import asyncio

    results: list[VerificationCheckResult] = []
    for candidate in candidates:
        if candidate.command is None:
            result = _materialize_static_check(
                candidate,
                root=root_path,
                selected_scopes=selected_scopes,
            )
        else:
            result = await asyncio.to_thread(
                _run_command,
                root_path,
                candidate,
                selected_scopes=selected_scopes,
            )
        results.append(result)
        logger.info(
            "Verification check complete",
            name=result.name,
            display_name=result.display_name,
            status=result.status,
            scope=result.scope,
            role=result.role.value,
            applicability=result.applicability.value,
            command=result.command,
        )

    blocking_failures = [
        _check_label(result)
        for result in results
        if result.blocking and result.status == "failed" and result.role == VerificationRole.CANONICAL
    ]
    unavailable_required = [
        _check_label(result)
        for result in results
        if result.applicability == VerificationApplicability.REQUIRED and result.status == "unavailable"
    ]
    return VerificationSummary(
        status="complete",
        checks=results,
        blocking_failures=blocking_failures,
        unavailable_required=unavailable_required,
        verdict_predicate=_predicate_for_results(results),
    )

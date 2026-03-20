# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH)
frontend_dist = project_root / "src" / "frontend" / "dist"

if not (frontend_dist / "index.html").exists():
    raise SystemExit("Frontend dist is missing. Run npm run build in src/frontend first.")

datas = collect_data_files("copilot")
for file in frontend_dist.rglob("*"):
    if file.is_file():
        relative_parent = file.parent.relative_to(frontend_dist)
        datas.append((str(file), str(Path("frontend_dist") / relative_parent)))

hiddenimports = collect_submodules("copilot")

a = Analysis(
    [str(project_root / "src" / "reviewer_launcher.py")],
    pathex=[str(project_root), str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Reviewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
)

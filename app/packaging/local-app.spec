# PyInstaller injects the build classes and SPECPATH before executing this file.
# ruff: noqa: E402, F821
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(root / "app" / "packaging"))
from bundle_resources import prepare_notices, verified_files

frontend = root / "app" / "frontend" / "dist"
if not (frontend / "index.html").is_file():
    raise RuntimeError("Build the browser interface before packaging.")

datas = [
    (str(source), str(Path("framework") / source.relative_to(root).parent))
    for source in verified_files(root)
]
datas += [
    (str(source), str(Path("frontend") / source.relative_to(frontend).parent))
    for source in frontend.rglob("*") if source.is_file()
]
datas += collect_data_files("claude_agent_sdk", includes=["_bundled\\*"])
datas += prepare_notices(root / "app" / "packaging" / "work" / "notices", root)

analysis = Analysis(
    [str(root / "app" / "launcher" / "main.py")],
    pathex=[str(root / "app" / "backend" / "src")],
    binaries=[], datas=datas,
    hiddenimports=[
        "uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
        "unicodedata", "csv",
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pytest", "ruff", "pip", "setuptools", "IPython", "matplotlib"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, [], exclude_binaries=True, name="AIJobSearch",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False,
)
collection = COLLECT(
    executable, analysis.binaries, analysis.datas,
    strip=False, upx=False, name="AIJobSearch",
)

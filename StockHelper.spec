import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


project_root = Path(SPECPATH)
build_cache_home = project_root / ".paddle-cache" / "home"
build_cache_home.mkdir(parents=True, exist_ok=True)
os.environ["USERPROFILE"] = str(build_cache_home)
os.environ["PADDLE_PDX_CACHE_HOME"] = str(project_root / ".paddle-cache")
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

datas = []
binaries = []
hiddenimports = []

# PaddleOCR and PaddleX discover pipeline components dynamically, so static import
# analysis alone does not find everything required by the OCR worker.
for package_name in ("paddle", "paddleocr", "paddlex"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

for distribution_name in (
    "paddlepaddle",
    "paddleocr",
    "paddlex",
    # PaddleX checks OCR extras with importlib.metadata at runtime. The modules
    # alone are insufficient; their dist-info directories must also be bundled.
    "imagesize",
    "opencv-contrib-python",
    "pyclipper",
    "pypdfium2",
    "python-bidi",
    "shapely",
):
    datas += copy_metadata(distribution_name, recursive=True)

a = Analysis(
    [str(project_root / "live_ui.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StockHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="StockHelper",
)

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata


project_root = Path(SPECPATH)
build_cache_home = project_root / ".paddle-cache" / "home"
build_cache_home.mkdir(parents=True, exist_ok=True)
os.environ["USERPROFILE"] = str(build_cache_home)
os.environ["PADDLE_PDX_CACHE_HOME"] = str(project_root / ".paddle-cache")
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

datas = []
binaries = []
hiddenimports = ["websockets.sync.client", "market_radar.ui", "market_radar.collection_ui",
                 "market_radar.shutdown_test", "market_radar.shutdown_test_ui",
                 "market_radar.one_hour", "tools.packaged_smoke"]
# Market Radar GUI also needs its pinned data contract and XNYS/timezone data.
datas += [(str(project_root / "market_radar/providers/kiwoom_contract.json"), "market_radar/providers")]
datas += collect_data_files("exchange_calendars") + collect_data_files("tzdata")
datas += copy_metadata("exchange_calendars")

# PaddleOCR and PaddleX discover pipeline components dynamically, so static import
# analysis alone does not find everything required by the OCR worker.
for package_name in (
    "paddle",
    "paddleocr",
    "paddlex",
    "nvidia.cublas",
    "nvidia.cuda_runtime",
    "nvidia.cudnn",
    "nvidia.cufft",
    "nvidia.curand",
    "nvidia.cusolver",
    "nvidia.cusparse",
    "nvidia.nvjitlink",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

for distribution_name in (
    "paddlepaddle-gpu",
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
    # Ordinary user launch: shutdown failures are reported, never auto-elevated.
    uac_admin=False,
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

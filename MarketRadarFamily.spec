"""Collector-only package: reuse Radar GUI/engine without importing OCR/GPU."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
datas = [(str(root / 'market_radar/providers/kiwoom_contract.json'), 'market_radar/providers'),
         (str(root / 'market_radar/watchdog_task.ps1'), 'market_radar'),
         (str(root / 'market_radar/audit_task.ps1'), 'market_radar')]
datas += collect_data_files('exchange_calendars') + collect_data_files('tzdata')
datas += copy_metadata('exchange_calendars')
a = Analysis([str(root / 'tools/family_collect.py')], pathex=[str(root)],
    datas=datas, binaries=[],
    hiddenimports=['market_radar.family_ui', 'market_radar.watchdog', 'tools.family_smoke', 'websockets.sync.client'],
    excludes=['paddle', 'paddleocr', 'paddlex', 'torch', 'tensorflow', 'cv2', 'matplotlib',
              'scipy', 'IPython', 'notebook', 'pytest', 'execution', 'live_ui'],
    noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MarketRadarFamily',
    console=False, debug=False, strip=False, upx=True, uac_admin=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name='MarketRadarFamily')

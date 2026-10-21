from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

project = Path(SPECPATH).resolve().parents[1]
datas = [(str(project / 'market_radar/providers/kiwoom_contract.json'), 'market_radar/providers')]
datas += collect_data_files('exchange_calendars') + collect_data_files('tzdata')
datas += copy_metadata('exchange_calendars')
a = Analysis([str(project / 'tools/one_hour_runner/entry.py')], pathex=[str(project)],
    binaries=[], datas=datas, hiddenimports=['websockets.sync.client'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['paddle', 'paddleocr', 'easyocr', 'torch', 'torchvision', 'cv2', 'execution',
              'market_radar.execution', 'live_ui', 'PyQt5', 'PyQt6', 'tkinter', 'matplotlib'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='OneHourResearch',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=True, uac_admin=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='OneHourResearch')

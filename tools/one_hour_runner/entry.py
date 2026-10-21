"""Absolute package entry for PyInstaller (no import of Stock Helper/OCR UI)."""
from tools.one_hour_runner.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())

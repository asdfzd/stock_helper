@echo off
setlocal DisableDelayedExpansion
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\launch_stock_helper.ps1" -DebugMode
set "launcher_exit=%errorlevel%"
echo.
echo Stock Helper exited with code %launcher_exit%.
pause
exit /b %launcher_exit%

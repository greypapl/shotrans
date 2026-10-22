@echo off
rem Start ShotTrans in the background (no console window).
cd /d "%~dp0"
start "" "%USERPROFILE%\anaconda3\envs\shotrans\pythonw.exe" -m shotrans

@echo off
rem Start with a throw-away data folder (shows the first-run wizard every time).
rem Quit the running tray app first; only one copy can run at a time.
rem Trial mode never creates or deletes the start-with-Windows shortcut.
set "SHOTRANS_HOME=%TEMP%\shotrans-trial"
set "SHOTRANS_TRIAL=1"
if exist "%SHOTRANS_HOME%" rmdir /s /q "%SHOTRANS_HOME%"
for %%f in ("%~dp0*.exe") do start "" "%%f"

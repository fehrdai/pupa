@echo off
rem avvia_luci.bat - lancia avvia_luci.ps1 (vedi li' le opzioni: start / restart / stop / status)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0avvia_luci.ps1" %*

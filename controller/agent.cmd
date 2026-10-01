@echo off
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0agent.py" %*
exit /b %errorlevel%

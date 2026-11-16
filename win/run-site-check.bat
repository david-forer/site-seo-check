@echo off
REM Weekly site SEO check, run unattended from Windows Task Scheduler.
REM Stage 1 is plain Python (no model), so collection finishes even if Claude is unavailable.
REM Stage 2 is claude --print for the compare and report, driven by win\prompt.txt.
REM Machine-specific settings (CLAUDE, ADD_DIRS) go in win\local.bat, which is not committed.
REM Pass a prompt filename as arg 1 to override prompt.txt.

setlocal
for %%I in ("%~dp0..") do set BASE=%%~fI
set CLAUDE=claude
set ADD_DIRS=
if exist "%BASE%\win\local.bat" call "%BASE%\win\local.bat"
set PROMPT=%BASE%\win\prompt.txt
if not "%~1"=="" set PROMPT=%BASE%\win\%~1
if not exist "%PROMPT%" (
  echo Missing %PROMPT%. Copy win\prompt.example.txt to win\prompt.txt first.
  exit /b 1
)

if not exist "%BASE%\win\logs" mkdir "%BASE%\win\logs"
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%a
set LOG=%BASE%\win\logs\run-%TODAY%.log

echo ================================================== >> "%LOG%"
echo START %DATE% %TIME% >> "%LOG%"

cd /d "%BASE%"
echo STAGE 1 collect >> "%LOG%"
py "%BASE%\execution\run_all.py" >> "%LOG%" 2>&1
echo COLLECT EXIT %ERRORLEVEL% at %TIME% >> "%LOG%"

echo STAGE 2 report >> "%LOG%"
type "%PROMPT%" | "%CLAUDE%" --print ^
  --allowedTools "Bash" "Write" "Edit" "Read" "Glob" "Grep" "Skill" ^
  --disallowedTools "Bash(git:*)" ^
  %ADD_DIRS% >> "%LOG%" 2>&1

echo EXIT %ERRORLEVEL% at %DATE% %TIME% >> "%LOG%"
endlocal

@echo off
echo === Step 1: Build webui frontend ===
cd /d %~dp0..\plugins\coding_agent_webui\frontend
call npm run build
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%

echo === Step 2: PyInstaller build backend ===
cd /d %~dp0..
.venv\Scripts\python.exe -m PyInstaller desktop\mofox-backend.spec --distpath desktop\dist --workpath desktop\build --clean
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%

echo === Step 3: Copy plugins to output root ===
robocopy desktop\dist\mofox-backend\_internal\plugins desktop\dist\mofox-backend\plugins /E /NFL /NDL /NJH /NJS
if %ERRORLEVEL% geq 8 exit /b %ERRORLEVEL%

echo === Step 4: Tauri build ===
cd /d %~dp0..\desktop\tauri
cargo tauri build --bundles nsis
if %ERRORLEVEL% neq 0 (
    echo NSIS build failed (network issue?), creating portable package...
    goto :portable
)
goto :done

:portable
cd /d %~dp0..\desktop
if exist "dist\MoFox-Code-portable" rmdir /s /q "dist\MoFox-Code-portable"
mkdir "dist\MoFox-Code-portable"
robocopy tauri\target\release dist\MoFox-Code-portable mofox-code-desktop.exe /NFL /NDL /NJH /NJS
robocopy dist\mofox-backend dist\MoFox-Code-portable\mofox-backend /E /NFL /NDL /NJH /NJS
echo Portable package: desktop\dist\MoFox-Code-portable\

:done
echo === Build complete ===

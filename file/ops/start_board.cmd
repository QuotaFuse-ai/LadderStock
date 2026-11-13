@echo off
REM 便携启动器（QUOT-592）：全相对路径，落在本安装根内。
REM 布局：<InstallRoot>/file/ops/start_board.cmd，config/data/logs 都在 <InstallRoot> 下。
REM 便携模式由 paths.py 自动解析（.portable 标记 / 冻结态），这里不再写 D:\ 绝对路径。
REM Usage: start_board.cmd [port]   default 5950
setlocal
set "ROOT=%~dp0..\.."
for %%i in ("%ROOT%") do set "ROOT=%%~fi"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=5950"
set "PYTHONPATH=%ROOT%\src"
set "LOGDIR=%ROOT%\file\logs"
set "FRONTEND=%ROOT%\frontend"
REM peer / 组件目录默认与安装根同级（可被外部环境变量覆盖）
if not defined LDST_PEER_DIR_WIRE set "LDST_PEER_DIR_WIRE=%ROOT%\..\quotafuse-wire"
if not defined LDST_COMPONENT_DIR_PROXYFLEET set "LDST_COMPONENT_DIR_PROXYFLEET=%ROOT%\..\ProxyFleet"
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"
REM LDST_CONFIG_DIR / LDST_DATA_DIR 刻意不设：便携模式由 paths.py 解析到 %ROOT%\config、%ROOT%\data
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
REM 冻结态用安装根内解释器；源码态用 PATH 上的 python
set "PYEXE="
if exist "%ROOT%\bin\LadderStock\python.exe" set "PYEXE=%ROOT%\bin\LadderStock\python.exe"
if "%PYEXE%"=="" (
  where python >nul 2>&1 || exit /b 1
  for /f "delims=" %%p in ('where python') do if not defined PYEXE set "PYEXE=%%p"
)
echo [%date% %time%] starting portable board %PORT% >> "%LOGDIR%\web-%PORT%.log"
"%PYEXE%" -m ladderstock.cli.main run --port %PORT% --host 127.0.0.1 >> "%LOGDIR%\web-%PORT%.log" 2>&1

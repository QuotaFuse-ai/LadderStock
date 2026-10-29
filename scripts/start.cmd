@echo off
rem 旧版启动脚本（兼容入口）：委托 ldst run，不再维护第二套启动逻辑。
rem
rem 过渡期用法：scripts\start.cmd [--port 5960] == ldst run [--port 5960]
rem 旧配置/旧数据目录请先跑一次 ldst config migrate：
rem   只读迁移（旧文件不改不删），迁移前自动备份，失败会回退到旧启动路径并提示。
setlocal
echo ldst: 旧版启动方式已委托 ldst run（迁移：ldst config migrate） 1>&2

where ldst >nul 2>&1
if %ERRORLEVEL%==0 (
    ldst run %*
) else (
    python -m ladderstock run %*
)
exit /b %ERRORLEVEL%

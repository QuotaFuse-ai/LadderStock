# 5951 开发/测试服一键重启（端口 scoped，只结束 5951 上的 listening 进程链）。
# 绝不触碰 5950(正式)/8000(peer)/9100/9000(mem0)；不按映像名批量杀。
# 镜像 restart-api.ps1 结构，但端口=5951、launcher=start_board_dev.cmd、匹配 start_board_dev。
$ErrorActionPreference = 'Continue'
$ApiPort = 5951
$Launcher = 'D:\workplace\LadderStock\file\ops\start_board_dev.cmd'
$own = (Get-NetTCPConnection -State Listen -LocalPort $ApiPort -ErrorAction SilentlyContinue).OwningProcess
if ($own) {
    $p = $own
    for ($i = 0; $i -lt 4; $i++) {
        $o = Get-CimInstance Win32_Process -Filter "ProcessId=$p" -ErrorAction SilentlyContinue
        if (-not $o) { break }
        if ($o.CommandLine -and $o.CommandLine -match 'ladderstock|start_board_dev|cmd\.exe /c') {
            try { Stop-Process -Id $p -Force -ErrorAction Stop; Write-Output "restart-dev: stopped pid $p" } catch {}
            $p = $o.ParentProcessId
        } else { break }
    }
    Start-Sleep -Seconds 2
}
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$Launcher`" $ApiPort" -WindowStyle Hidden
Write-Output "restart-dev: launched via start_board_dev.cmd on port $ApiPort"
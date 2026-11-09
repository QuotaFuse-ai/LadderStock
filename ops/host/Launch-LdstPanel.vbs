' LadderStock 面板启动器：用 wscript 拉起 powershell，完全不出现控制台窗口
Option Explicit
Dim sh, cmd
Set sh = CreateObject("WScript.Shell")
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""D:\QuotaFuseModules\LadderStock\ops\host\Show-LdstHostPanel.ps1"""
sh.Run cmd, 0, False
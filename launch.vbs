' Fund Analyzer Launcher
Option Explicit

Dim shell, fso, scriptDir, pythonExe, scriptPath, cmd, logPath
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir  = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = scriptDir & "\fund_analyzer_gui.py"

pythonExe = "C:\Users\user\anaconda3\envs\quant-finance\python.exe"

If Not fso.FileExists(pythonExe) Then
    MsgBox "找不到 python.exe：" & vbCrLf & pythonExe & vbCrLf & vbCrLf & _
           "請修改 launch.vbs 中的 pythonExe 變量。", 16, "Fund Analyzer"
    WScript.Quit 1
End If

If Not fso.FileExists(scriptPath) Then
    MsgBox "找不到主程序：" & vbCrLf & scriptPath, 16, "Fund Analyzer"
    WScript.Quit 1
End If

shell.CurrentDirectory = scriptDir

' 把 stdout/stderr 重定向到 launch_stdout.log
logPath = scriptDir & "\launch_stdout.log"
cmd = "cmd /c """"" & pythonExe & """ """ & scriptPath & """ > """ & logPath & """ 2>&1"""

' 第二個參數 0 = 完全隱藏窗口（無黑窗）
' 第三個參數 False = 不等待退出（VBS 立刻返回）
shell.Run cmd, 0, False
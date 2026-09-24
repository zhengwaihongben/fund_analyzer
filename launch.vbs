'' Fund Analyzer Launcher
Option Explicit

Dim shell, fso, scriptDir, pythonExe, scriptPath, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

'' 腳本所在目錄
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = scriptDir & "\fund_analyzer_gui.py"

'' pythonw.exe 路徑（按你的實際環境修改）
pythonExe = "C:\Users\user\anaconda3\envs\quant-finance\pythonw.exe"

'' 檢查文件存在
If Not fso.FileExists(pythonExe) Then
    MsgBox "找不到 pythonw.exe：" & vbCrLf & pythonExe & vbCrLf & vbCrLf & _
           "請修改 launch.vbs 中的 pythonExe 變量。", 16, "Fund Analyzer"
    WScript.Quit 1
End If

If Not fso.FileExists(scriptPath) Then
    MsgBox "找不到主程序：" & vbCrLf & scriptPath, 16, "Fund Analyzer"
    WScript.Quit 1
End If

'' 設置工作目錄並啟動
shell.CurrentDirectory = scriptDir
cmd = """" & pythonExe & """ """ & scriptPath & """"
shell.Run cmd, 0, False   '' 0 = 隱藏窗口, False = 不等待
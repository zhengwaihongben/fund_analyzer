' Fund Analyzer Launcher
'
' Python 解釋器查找順序（不再寫死某台機器的絕對路徑）：
'   1. 本目錄下的 python_path.txt（第一行寫任意 python.exe 路徑，用於臨時指定）
'   2. 專案內虛擬環境 .venv\Scripts\python.exe（install.bat 建立的環境）
'   3. 專案內虛擬環境 venv\Scripts\python.exe
'   4. %USERPROFILE%\anaconda3\envs\quant-finance\python.exe（舊環境，保留兼容）
Option Explicit

Dim shell, fso, scriptDir, pythonExe, scriptPath, cmd, logPath, logFile
Dim overrideFile, candidates, i, envPython

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir  = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = scriptDir & "\fund_analyzer_gui.py"

pythonExe = ""

' 1) 手工覆蓋：python_path.txt 第一行
overrideFile = scriptDir & "\python_path.txt"
If fso.FileExists(overrideFile) Then
    Dim ts, line
    Set ts = fso.OpenTextFile(overrideFile, 1)
    Do While Not ts.AtEndOfStream
        line = Trim(ts.ReadLine)
        If Len(line) > 0 Then
            If fso.FileExists(line) Then
                pythonExe = line
            End If
            Exit Do
        End If
    Loop
    ts.Close
End If

' 2) ~ 4) 專案相對的虛擬環境，最後才退回舊的 conda 環境
If pythonExe = "" Then
    envPython = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\anaconda3\envs\quant-finance\python.exe"
    candidates = Array( _
        scriptDir & "\.venv\Scripts\python.exe", _
        scriptDir & "\venv\Scripts\python.exe", _
        envPython _
    )
    For i = 0 To UBound(candidates)
        If fso.FileExists(candidates(i)) Then
            pythonExe = candidates(i)
            Exit For
        End If
    Next
End If

If pythonExe = "" Then
    MsgBox "找不到 Python 解釋器。請先執行 install.bat 建立 .venv，" & vbCrLf & _
           "或在本目錄建立 python_path.txt，第一行寫上 python.exe 的完整路徑。", _
           16, "Fund Analyzer"
    WScript.Quit 1
End If

If Not fso.FileExists(scriptPath) Then
    MsgBox "找不到主程序：" & vbCrLf & scriptPath, 16, "Fund Analyzer"
    WScript.Quit 1
End If

shell.CurrentDirectory = scriptDir

' 日誌改為追加寫入，並記錄每次啟動的時間與所用解釋器
logPath = scriptDir & "\launch_stdout.log"
Set logFile = fso.OpenTextFile(logPath, 8, True)
logFile.WriteLine "[" & Now() & "] ===== launch ====="
logFile.WriteLine "  python : " & pythonExe
logFile.WriteLine "  script : " & scriptPath
logFile.Close

' 把 stdout/stderr 追加到 launch_stdout.log
cmd = "cmd /c """"" & pythonExe & """ """ & scriptPath & """ >> """ & logPath & """ 2>&1"""

' 第二個參數 0 = 完全隱藏窗口（無黑窗）
' 第三個參數 False = 不等待退出（VBS 立刻返回）
shell.Run cmd, 0, False

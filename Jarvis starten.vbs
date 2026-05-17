Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = "C:\Users\smart\Jarvis"
objShell.Run Chr(34) & "C:\Users\smart\Jarvis\venv\Scripts\pythonw.exe" & Chr(34) & " " & Chr(34) & "C:\Users\smart\Jarvis\main.py" & Chr(34), 0, False

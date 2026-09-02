' ─── J.A.R.V.I.S. Listener — Ejecución invisible con acceso al micrófono ───
' pythonw.exe NO tiene acceso al micrófono en Windows (permisos de privacidad).
' Usamos python.exe con Run(..., 0, False) para ocultar la ventana.
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Jarvis"
WshShell.Run """C:\Users\MSi\AppData\Local\Programs\Python\Python311\python.exe"" ""C:\Jarvis\listener.py""", 0, False

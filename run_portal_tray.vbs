Option Explicit

Dim shell, fso, projectRoot, psScript, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

projectRoot = fso.GetParentFolderName(WScript.ScriptFullName)
psScript = projectRoot & "\portal_tray.ps1"

cmd = "powershell -NoProfile -ExecutionPolicy Bypass -File """ & psScript & """"
shell.Run cmd, 0, False

Attribute VB_Name = "ArchiveMacro"
' Files the open or selected mail through folder-explorer (README, "Outlook macro").
' FOLDER_EXPLORER_HOME is set once per PC to the checkout folder; nothing else here names a path.
' The command runs hidden and the macro does not wait for it, so Outlook never blocks on it.
Sub ArchiveCurrentItem()
    Dim it As Object, home As String, cmd As String
    If TypeName(Application.ActiveWindow) = "Inspector" Then
        Set it = Application.ActiveInspector.CurrentItem
    ElseIf Application.ActiveExplorer.Selection.Count > 0 Then
        Set it = Application.ActiveExplorer.Selection.Item(1)
    Else
        Exit Sub
    End If
    home = Environ$("FOLDER_EXPLORER_HOME")
    If home = "" Then
        MsgBox "Set FOLDER_EXPLORER_HOME to the folder-explorer folder, then restart Outlook.", vbExclamation, "Archive"
        Exit Sub
    End If
    cmd = "cmd.exe /c cd /d """ & home & """ && .venv\Scripts\pythonw.exe -m app.outlook archive" _
        & " --entry-id " & it.EntryID & " --store-id " & it.Parent.StoreID
    CreateObject("WScript.Shell").Run cmd, 0, False
End Sub

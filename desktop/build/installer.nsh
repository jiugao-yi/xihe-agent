; xihe-agent NSIS custom steps: register the embedded CLI on the per-user PATH.
; The CLI bundle lives at $INSTDIR\bin\xihe (xihe.exe + _internal/), so the
; directory is added to HKCU "Environment" Path on install and removed on
; uninstall. Uses only native NSIS commands (no third-party plugins).

!include "LogicLib.nsh"
!include "StrFunc.nsh"
${StrStr}
${Using:StrFunc} UnStrStr

Var XiheCliDir

!macro customInstall
  DetailPrint "Registering xihe CLI on user PATH"
  StrCpy $XiheCliDir "$INSTDIR\bin\xihe"
  ReadRegStr $0 HKCU "Environment" "Path"
  ${If} $0 == ""
    StrCpy $0 "$XiheCliDir;"
  ${Else}
    ${StrStr} $1 $0 "$XiheCliDir"
    ${If} $1 == ""
      StrCpy $0 "$XiheCliDir;$0"
    ${EndIf}
  ${EndIf}
  WriteRegExpandStr HKCU "Environment" "Path" $0
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
!macroend

!macro customUnInstall
  DetailPrint "Removing xihe CLI from user PATH"
  StrCpy $XiheCliDir "$INSTDIR\bin\xihe"
  ReadRegStr $0 HKCU "Environment" "Path"
  ${If} $0 != ""
    ${UnStrStr} $1 $0 "$XiheCliDir;"
    ${If} $1 != ""
      StrLen $2 "$XiheCliDir;"
      StrLen $3 $1
      StrLen $4 $0
      IntOp $5 $4 - $3       ; length of the prefix before the entry
      StrCpy $6 $0 $5        ; prefix
      StrCpy $7 $1 "" $2     ; suffix after "dir;"
      StrCpy $0 "$6$7"
      ${If} $0 == ""
        DeleteRegValue HKCU "Environment" "Path"
      ${Else}
        WriteRegExpandStr HKCU "Environment" "Path" $0
      ${EndIf}
    ${EndIf}
  ${EndIf}
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
!macroend

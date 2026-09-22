; xihe-agent NSIS custom steps: register the embedded CLI on the per-user PATH.
; The CLI bundle lives at $INSTDIR\bin\xihe (xihe.exe + _internal/), so the
; directory is added to HKCU "Environment" Path on install and removed on
; uninstall.
;
; Implementation notes:
;  - Uses only native NSIS instructions (LogicLib + StrCpy/StrLen/IntOp).
;    No StrFunc.nsh: electron-builder bundles NSIS 3.0.4.1 whose StrFunc
;    lacks the ${Using:StrFunc}/UnStrStr machinery used by newer NSIS.
;  - Functions only touch $R0-$R9 so the macros' $0-$9 stay intact.

!include "LogicLib.nsh"

Var XiheCliDir

!macro customInstall
  DetailPrint "Registering xihe CLI on user PATH"
  StrCpy $XiheCliDir "$INSTDIR\bin\xihe"
  ReadRegStr $0 HKCU "Environment" "Path"
  ${If} $0 == ""
    StrCpy $0 "$XiheCliDir;"
  ${Else}
    Push $0
    Push "$XiheCliDir;"
    Call Contains
    Pop $1
    ${If} $1 == 0
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
    Push $0
    Push "$XiheCliDir;"
    Call un.RemoveEntry
    Pop $0
    ${If} $0 == ""
      DeleteRegValue HKCU "Environment" "Path"
    ${Else}
      WriteRegExpandStr HKCU "Environment" "Path" $0
    ${EndIf}
  ${EndIf}
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
!macroend

; Contains: (stack top: needle, below: haystack) -> push 1 if haystack
; contains needle else 0.

; Conditional compilation: electron-builder compiles the installer and the
; uninstaller as separate units with -WX (warnings as errors). Each unit only
; defines the helper it actually calls so makensis has no "not referenced"
; warning to promote.
!ifndef BUILD_UNINSTALLER
Function Contains
  Pop $R6
  Pop $R7
  StrLen $R0 $R6
  StrLen $R1 $R7
  StrCpy $R2 0
  StrCpy $R4 0
  ${DoWhile} $R2 <= $R1
    StrCpy $R3 $R7 $R0 $R2
    ${If} $R3 == $R6
      StrCpy $R4 1
      ${Break}
    ${EndIf}
    IntOp $R2 $R2 + 1
  ${Loop}
  Push $R4
FunctionEnd

!else
Function un.RemoveEntry
  Pop $R6
  Pop $R7
  StrLen $R0 $R6
  StrLen $R1 $R7
  StrCpy $R2 0
  StrCpy $R8 ""
  StrCpy $R9 0
  ${DoWhile} $R2 <= $R1
    StrCpy $R3 $R7 $R0 $R2
    ${If} $R3 == $R6
      StrCpy $R8 $R7 $R2
      IntOp $R5 $R2 + $R0
      StrCpy $R5 $R7 "" $R5
      StrCpy $R8 "$R8$R5"
      StrCpy $R9 1
      ${Break}
    ${EndIf}
    IntOp $R2 $R2 + 1
  ${Loop}
  ${If} $R9 == 0
    StrCpy $R8 $R7
  ${EndIf}
  Push $R8
FunctionEnd

!endif

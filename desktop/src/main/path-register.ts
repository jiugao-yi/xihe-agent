// 首启把内嵌 CLI 接入 PATH，让用户在终端里能直接敲 `xihe`。
//
// 背景：桌面包把 CLI（extraResources → <resources>/bin/xihe/xihe[.exe]）
// 打包进应用，桌面端自己用绝对路径调用，终端却找不到 xihe 命令。
// Windows NSIS 安装版已由 installer.nsh 在安装/卸载时注册 HKCU PATH；
// 其余"复制即用"形态（Windows 便携版 / macOS dmg / Linux AppImage）没有
// 安装器钩子，只能由桌面端首启自注册兜底。
//
// 幂等：每条分支先检查目标已存在/已注册，命中即跳过，可安全重复执行。
// 容错：任何失败只记日志，绝不阻塞桌面端启动。

import { app, Notification } from 'electron'
import { join } from 'path'
import { homedir } from 'os'
import { existsSync, promises as fs } from 'fs'
import { execFile } from 'child_process'
import { promisify } from 'util'

const execFileP = promisify(execFile)

/** 当前平台内嵌 CLI 可执行文件（打包后固定名）。 */
export function cliExecutableName(): string {
  return process.platform === 'win32' ? 'xihe.exe' : 'xihe'
}

/** 打包后内嵌 CLI 目录：<resources>/bin/xihe。 */
export function packagedCliDir(resourcesPath: string): string {
  return join(resourcesPath, 'bin', 'xihe')
}

/** 生成 Windows 用户 PATH 注册用的 PowerShell 脚本（目录经环境变量传入，防注入）。 */
export function buildWindowsPs(cliDir: string): string {
  const body = [
    `$d = $env:XIHE_CLI_DIR`,
    `$p = [Environment]::GetEnvironmentVariable('Path','User')`,
    `$hit = if ([string]::IsNullOrEmpty($p)) { $false } else { $p.IndexOf($d,[System.StringComparison]::OrdinalIgnoreCase) -ge 0 }`,
    `if (-not $hit) {`,
    `  $n = if ([string]::IsNullOrEmpty($p)) { $d } else { "$d;$p" }`,
    `  [Environment]::SetEnvironmentVariable('Path',$n,'User')`,
    `}`,
  ]
  return body.join('; ')
}

/** Windows：PowerShell .NET API 追加用户 PATH，SetEnvironmentVariable 自带 WM_SETTINGCHANGE 广播。 */
export async function registerWindows(cliDir: string): Promise<boolean> {
  const ps = buildWindowsPs(cliDir)
  await execFileP('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', ps], {
    env: { ...process.env, XIHE_CLI_DIR: cliDir },
    windowsHide: true,
    timeout: 15000,
  })
  return true
}

/** macOS：/usr/local/bin/xihe → CLI，符号链接目标持久（应用已在 /Applications）。 */
export async function registerMac(cli: string): Promise<boolean> {
  const link = '/usr/local/bin/xihe'
  try {
    const real = await fs.realpath(link)
    if (real === cli) return true // 已指向我们，幂等
    return false // 名字被其他程序占用，不覆盖
  } catch {
    // 链接不存在，继续创建
  }
  try {
    await fs.mkdir('/usr/local/bin', { recursive: true })
    await fs.symlink(cli, link)
    return true
  } catch (e) {
    // 无权限等情况：返回 false，由调用方提示手动命令
    console.warn('[path-register] mac symlink failed:', e)
    return false
  }
}

/** PATH（冒号分隔）里是否已包含 binDir（忽略尾部斜杠差异）。 */
export function userBinOnPath(binDir: string, pathVar = process.env.PATH ?? ''): boolean {
  const norm = (p: string) => p.replace(/\/+$/, '')
  return pathVar.split(':').some((p) => p && norm(p) === norm(binDir))
}

/** Linux：符号链接到 ~/.local/bin（AppImage 除外，见 registerLinux 说明）。 */
export async function registerLinux(cli: string): Promise<{ registered: boolean; pathMissing: boolean }> {
  const dir = join(homedir(), '.local', 'bin')
  const link = join(dir, 'xihe')
  const pathMissing = !userBinOnPath(dir)
  try {
    const real = await fs.realpath(link).catch(() => null)
    if (real === cli) return { registered: true, pathMissing }
    await fs.mkdir(dir, { recursive: true })
    await fs.symlink(cli, link)
    return { registered: true, pathMissing }
  } catch (e) {
    console.warn('[path-register] linux symlink failed:', e)
    return { registered: false, pathMissing }
  }
}

function notify(title: string, body: string): void {
  try {
    if (Notification.isSupported()) {
      new Notification({ title, body, silent: true }).show()
    }
  } catch {
    // 通知失败不影响主流程
  }
}

/** 主入口：首启检测并注册。dev 模式 / 无内嵌 CLI 直接跳过。 */
export async function ensureCliOnPath(): Promise<void> {
  try {
    if (!app.isPackaged) return
    const cliDir = packagedCliDir(process.resourcesPath)
    const cli = join(cliDir, cliExecutableName())
    if (!existsSync(cli)) return

    if (process.platform === 'win32') {
      await registerWindows(cliDir)
    } else if (process.platform === 'darwin') {
      const ok = await registerMac(cli)
      if (!ok) {
        notify(
          'xihe CLI 未接入 PATH',
          `终端里无法直接使用 xihe 命令。可手动执行：\nsudo ln -s "${cli}" /usr/local/bin/xihe`
        )
      }
    } else if (process.platform === 'linux') {
      if (process.env.APPIMAGE) {
        // AppImage 运行时挂载在只读 squashfs，挂载点每次启动都变，
        // 符号链接指向挂载点会随卸载失效，注册没有意义 → 只提示。
        notify(
          'xihe CLI 未接入 PATH',
          'AppImage 无法稳定注册 xihe 命令，建议改用 deb 安装包，或解压 CLI 包后手动加入 PATH。'
        )
        return
      }
      const { registered, pathMissing } = await registerLinux(cli)
      if (!registered || pathMissing) {
        notify(
          'xihe CLI 未接入 PATH',
          `已创建 ${join(homedir(), '.local', 'bin', 'xihe')}，若终端仍找不到 xihe，请把 ~/.local/bin 加入 PATH。`
        )
      }
    }
  } catch (e) {
    console.warn('[path-register] skipped:', e)
  }
}

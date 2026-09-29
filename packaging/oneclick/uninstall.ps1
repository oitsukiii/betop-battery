# betop-battery 卸载脚本
#
# 做三件事：
#   1. 删除程序目录（默认 %LOCALAPPDATA%\betop-battery）
#   2. 删除桌面与开始菜单快捷方式
#   3. 删除「设置 → 应用」里的注册项
#
# 会**保留**用户配置（%APPDATA%\betop-battery），并在结束时询问是否一并删除。
#
# 注意：本文件必须以 UTF-8 with BOM 保存（见 install.ps1 顶部说明）。

[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "betop-battery"),
    [switch]$Silent,
    [switch]$KeepConfig      # 不询问、直接保留用户配置
)

$ErrorActionPreference = "Continue"
$NoPause = $Silent
$AppName = "北通手柄电量"
$AppId = "betop-battery"

Write-Host ""
Write-Host "================================================" -ForegroundColor White
Write-Host "  $AppName  卸载程序" -ForegroundColor White
Write-Host "================================================" -ForegroundColor White

if (-not $Silent) {
    $answer = Read-Host "确定要卸载吗？(Y/N)"
    if ($answer -notmatch '^[Yy]') {
        Write-Host "已取消。"
        exit 0
    }
}

# ---- 1. 先结束正在运行的程序 ----
Write-Host ""
Write-Host "==> 结束正在运行的程序" -ForegroundColor Cyan
$killed = 0
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*betop*" } |
    ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $killed++ } catch { }
    }
Write-Host "    已结束 $killed 个进程" -ForegroundColor Green

# ---- 2. 删除程序目录 ----
Write-Host "==> 删除程序目录：$InstallDir" -ForegroundColor Cyan
if (Test-Path $InstallDir) {
    Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $InstallDir) {
        Write-Host "    部分文件被占用，未能完全删除，请重启后手动删除该目录" -ForegroundColor Yellow
    } else {
        Write-Host "    已删除" -ForegroundColor Green
    }
} else {
    Write-Host "    目录不存在，跳过" -ForegroundColor Yellow
}

# ---- 3. 删除快捷方式 ----
Write-Host "==> 删除快捷方式" -ForegroundColor Cyan
$desktop = [Environment]::GetFolderPath("Desktop")
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
foreach ($p in @(
        (Join-Path $desktop   "$AppName.lnk"),
        (Join-Path $desktop   "$AppName-设置.lnk"),
        (Join-Path $startMenu "$AppName.lnk"))) {
    if (Test-Path $p) {
        Remove-Item $p -Force -ErrorAction SilentlyContinue
        Write-Host "    已删除 $p" -ForegroundColor Green
    }
}

# ---- 4. 删除注册项 ----
Write-Host "==> 删除注册信息" -ForegroundColor Cyan
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
if (Test-Path $key) {
    Remove-Item $key -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "    已删除" -ForegroundColor Green
} else {
    Write-Host "    注册项不存在，跳过" -ForegroundColor Yellow
}

# ---- 5. 询问是否删除用户配置 ----
$configDir = Join-Path $env:APPDATA $AppId
if (Test-Path $configDir) {
    Write-Host ""
    Write-Host "==> 用户配置还留在：$configDir" -ForegroundColor Cyan
    $del = if ($KeepConfig) { "N" } else { Read-Host "    是否一并删除（包含你调过的样式与 HUD 设置）？(Y/N)" }
    if ($del -match '^[Yy]') {
        Remove-Item $configDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "    已删除" -ForegroundColor Green
    } else {
        Write-Host "    已保留（下次安装会沿用）" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "  卸载完成，感谢使用！" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
if (-not $Silent) { Read-Host "按回车键关闭本窗口" }

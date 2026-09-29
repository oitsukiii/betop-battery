# betop-battery 一键安装脚本
#
# 目标用户：不熟悉命令行的普通用户。
# 用户只需要解压压缩包，然后双击「安装.bat」。
#
# 脚本会自动完成：
#   1. 检查系统里的 Python（版本需 >= 3.9）
#   2. 没有就通过 winget 安装官方 Python（免管理员、per-user）
#   3. 把程序复制到 %LOCALAPPDATA%\betop-battery（位置固定，避免用户挪动文件后快捷方式失效）
#   4. 安装依赖（hidapi / pystray / Pillow），失败时自动改用国内镜像
#   5. 生成图标
#   6. 创建桌面 + 开始菜单快捷方式（用 pythonw，无黑窗口）
#   7. 在「设置 → 应用」里注册卸载项
#   8. 启动托盘图标
#
# 参数（一般用户用不到，供测试使用）：
#   -InstallDir <路径>   自定义安装目录
#   -SkipDeps            跳过依赖安装（调试用）
#   -SkipShortcuts       不创建快捷方式（调试用）
#   -NoLaunch            安装完不自动启动
#
# 注意：本文件必须以 **UTF-8 with BOM** 保存，否则 Windows PowerShell 5.1
# 会按 ANSI 解析，中文全部乱码（打包脚本 tools/make_oneclick_zip.py 会自动加 BOM）。

[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "betop-battery"),
    [switch]$SkipDeps,
    [switch]$SkipShortcuts,
    [switch]$NoLaunch,
    [switch]$NoPause        # 供自动化测试使用：不等待按键
)

# 注意：这里**必须**是 Continue。
# 若设为 Stop，PowerShell 会把原生程序（pip / python）写到 stderr 的**普通警告**
# 当成终止性错误抛出 —— 实测导致"图标生成失败""依赖安装失败"等假故障。
# 因此统一用显式的 $LASTEXITCODE 判断成败。
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# 压缩包根目录（本脚本位于 packaging\oneclick\ 下）
$SourceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $SourceRoot "run.py"))) {
    # 兼容"扁平解压"的情况
    $SourceRoot = $PSScriptRoot
}

$AppName = "北通手柄电量"
$AppId = "betop-battery"
$Version = "1.0.5"

function Write-Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "    $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "    $text" -ForegroundColor Yellow }
function Fail($text) {
    Write-Host ""
    Write-Host "[安装失败] $text" -ForegroundColor Red
    Write-Host "可以把上面的信息截图反馈给作者。" -ForegroundColor Red
    if (-not $NoPause) { Read-Host "按回车键退出" }
    exit 1
}

Write-Host ""
Write-Host "================================================" -ForegroundColor White
Write-Host "  $AppName  一键安装程序  v$Version" -ForegroundColor White
Write-Host "================================================" -ForegroundColor White
Write-Host "  这个程序会在通知区域显示北通手柄的电量。"
Write-Host "  安装过程全自动，请耐心等待（约 1~3 分钟）。"

# ---------------------------------------------------------------- 1. 找 Python
function Find-Python {
    <#
      返回可用 python.exe 的完整路径；找不到返回 $null。
      优先使用 py 启动器（它能正确处理多版本），其次 PATH，最后常见安装目录。
    #>
    $checks = @(
        @{ Exe = "py";      Args = @("-3", "-c", "import sys;print(sys.executable)") },
        @{ Exe = "python";  Args = @("-c",    "import sys;print(sys.executable)") },
        @{ Exe = "python3"; Args = @("-c",    "import sys;print(sys.executable)") }
    )
    foreach ($c in $checks) {
        $cmd = Get-Command $c.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $out = & $c.Exe @($c.Args) 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $exe = ($out | Select-Object -First 1).ToString().Trim()
                if (Test-Path $exe) {
                    $ver = & $exe -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
                    if ([version]$ver -ge [version]"3.9") { return $exe }
                    Write-Warn2 "发现 Python $ver，但版本过低（需要 3.9+），继续查找…"
                }
            }
        } catch { }
    }
    # 常见安装位置（winget / 官网安装器默认路径）
    $patterns = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe"),
        "C:\Program Files\Python3*\python.exe",
        "C:\Python3*\python.exe"
    )
    foreach ($p in $patterns) {
        $found = Get-ChildItem $p -ErrorAction SilentlyContinue |
                 Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

Write-Step "检查 Python"
$Python = Find-Python
if ($Python) {
    Write-Ok "已找到：$Python"
} else {
    Write-Warn2 "系统里没有 Python 3.9+，准备自动安装……"

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Ok "使用 winget 安装官方 Python（免管理员权限）"
        & winget install -e --id Python.Python.3.12 --scope user `
            --accept-package-agreements --accept-source-agreements `
            --disable-interactivity 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Warn2 "winget 返回码 $LASTEXITCODE，稍后重试查找" }
        Write-Ok "等待安装完成…"
        Start-Sleep -Seconds 5
        $Python = Find-Python
    }

    if (-not $Python) {
        # 没有 winget（老系统）→ 从官网下载安装包
        Write-Warn2 "未找到 winget，改为从 python.org 下载安装包"
        $ver = "3.12.10"
        $url = "https://www.python.org/ftp/python/$ver/python-$ver-amd64.exe"
        $tmp = Join-Path $env:TEMP "python-installer.exe"
        try {
            Write-Ok "下载 $url"
            Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
            Write-Ok "静默安装（仅当前用户）"
            Start-Process -FilePath $tmp -Wait -ArgumentList `
                "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0"
            Start-Sleep -Seconds 3
            $Python = Find-Python
        } catch {
            Fail "自动安装 Python 失败：$_`n请手动安装 Python 3.9+（https://www.python.org/downloads/）后重新运行本脚本。"
        }
    }

    if (-not $Python) {
        Fail "仍然找不到 Python。请手动安装 Python 3.9+（安装时勾选 Add to PATH）后重新运行。"
    }
    Write-Ok "Python 就绪：$Python"
}

$PythonW = Join-Path (Split-Path $Python) "pythonw.exe"
if (-not (Test-Path $PythonW)) { $PythonW = $Python }   # 极端情况退化为带控制台

# ---------------------------------------------------------------- 2. 复制文件
Write-Step "复制程序文件到：$InstallDir"
try {
    if (Test-Path $InstallDir) {
        # 保留用户配置（配置在 %APPDATA% 下，这里只覆盖程序文件）
        Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    foreach ($item in @("run.py", "src", "tools", "docs", "packaging",
                        "requirements.txt", "README.md", "LICENSE")) {
        $src = Join-Path $SourceRoot $item
        if (Test-Path $src) {
            Copy-Item $src -Destination $InstallDir -Recurse -Force
        }
    }
    if (-not (Test-Path (Join-Path $InstallDir "run.py"))) {
        Fail "复制文件失败：在 $SourceRoot 里找不到 run.py"
    }
    # 在安装目录里放一个「卸载.bat」，方便用户日后卸载
    $unBat = Join-Path $InstallDir "卸载.bat"
    @"
@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\oneclick\uninstall.ps1"
if errorlevel 1 pause
"@ | Set-Content -Path $unBat -Encoding OEM
    Write-Ok "已复制"
} catch {
    Fail "复制文件失败：$_"
}

# ---------------------------------------------------------------- 3. 装依赖
if (-not $SkipDeps) {
    Write-Step "安装运行依赖（hidapi / pystray / Pillow）"
    $deps = @("hidapi>=0.14", "pystray>=0.19", "Pillow>=10.0")
    $ok = $false
    Write-Ok "从默认源安装…"
    $pipOut = & $Python -m pip install --disable-pip-version-check @deps 2>&1
    if ($LASTEXITCODE -eq 0) {
        $ok = $true
    } else {
        Write-Warn2 "默认源失败，改用清华镜像重试…"
        $pipOut = & $Python -m pip install --disable-pip-version-check `
            -i https://pypi.tuna.tsinghua.edu.cn/simple @deps 2>&1
        if ($LASTEXITCODE -eq 0) { $ok = $true }
    }
    if ($ok) {
        Write-Ok "依赖安装完成"
    } else {
        Write-Host ($pipOut | Select-Object -Last 6 | Out-String) -ForegroundColor DarkGray
        Fail "依赖安装失败。请检查网络后重试，或手动执行：`n  $Python -m pip install hidapi pystray Pillow"
    }
} else {
    Write-Warn2 "已跳过依赖安装（-SkipDeps）"
}

# ---------------------------------------------------------------- 4. 生成图标
Write-Step "生成应用图标"
$IconPath = Join-Path $InstallDir "assets\icon.ico"
$iconOut = & $Python (Join-Path $InstallDir "tools\make_icon.py") 2>&1
if (Test-Path $IconPath) {
    Write-Ok "图标已生成"
} else {
    Write-Warn2 "图标生成失败（不影响使用，只是快捷方式没有自定义图标）"
    if ($iconOut) { Write-Warn2 ("  原因：" + ($iconOut | Select-Object -Last 2 | Out-String).Trim()) }
    $IconPath = $null
}

# ---------------------------------------------------------------- 5. 快捷方式
if (-not $SkipShortcuts) {
    Write-Step "创建快捷方式"
    $ws = New-Object -ComObject WScript.Shell
    $desktop = [Environment]::GetFolderPath("Desktop")
    $startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"

    $targets = @(
        @{ Path = (Join-Path $desktop   "$AppName.lnk");            Args = "tray"; Desc = "在通知区域显示北通手柄电量" },
        @{ Path = (Join-Path $desktop   "$AppName-设置.lnk");       Args = "gui";  Desc = "打开 betop-battery 设置界面" },
        @{ Path = (Join-Path $startMenu "$AppName.lnk");            Args = "tray"; Desc = "在通知区域显示北通手柄电量" }
    )
    foreach ($t in $targets) {
        try {
            $lnk = $ws.CreateShortcut($t.Path)
            $lnk.TargetPath = $PythonW
            $lnk.Arguments = '"' + (Join-Path $InstallDir "run.py") + '" ' + $t.Args
            $lnk.WorkingDirectory = $InstallDir
            $lnk.Description = $t.Desc
            if ($IconPath) { $lnk.IconLocation = $IconPath }
            $lnk.Save()
        } catch {
            Write-Warn2 "创建 $($t.Path) 失败：$_"
        }
    }
    Write-Ok "桌面与开始菜单快捷方式已创建"
}

# ---------------------------------------------------------------- 6. 注册卸载
Write-Step "注册卸载信息（可在「设置 → 应用」里卸载）"
try {
    $uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
    New-Item -Path $uninstallKey -Force | Out-Null
    $uninstallCmd = 'powershell -NoProfile -ExecutionPolicy Bypass -File "' +
                    (Join-Path $InstallDir "packaging\oneclick\uninstall.ps1") + '"'
    Set-ItemProperty -Path $uninstallKey -Name "DisplayName"     -Value $AppName
    Set-ItemProperty -Path $uninstallKey -Name "DisplayVersion"  -Value $Version
    Set-ItemProperty -Path $uninstallKey -Name "Publisher"       -Value "betop-battery contributors"
    Set-ItemProperty -Path $uninstallKey -Name "UninstallString" -Value $uninstallCmd
    Set-ItemProperty -Path $uninstallKey -Name "InstallLocation" -Value $InstallDir
    Set-ItemProperty -Path $uninstallKey -Name "NoModify"        -Value 1 -Type DWord
    Set-ItemProperty -Path $uninstallKey -Name "NoRepair"        -Value 1 -Type DWord
    if ($IconPath) { Set-ItemProperty -Path $uninstallKey -Name "DisplayIcon" -Value $IconPath }
    Write-Ok "已注册"
} catch {
    Write-Warn2 "注册卸载信息失败（不影响使用）：$_"
}

# ---------------------------------------------------------------- 7. 启动
if (-not $NoLaunch) {
    Write-Step "启动程序"
    try {
        Start-Process -FilePath $PythonW `
            -ArgumentList ('"' + (Join-Path $InstallDir "run.py") + '" tray') `
            -WorkingDirectory $InstallDir
        Write-Ok "已启动，请查看屏幕右下角通知区域"
    } catch {
        Write-Warn2 "自动启动失败，请双击桌面快捷方式：$_"
    }
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "  安装完成！" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  · 通知区域会出现一个圆角数字图标，显示手柄电量"
Write-Host "  · 手柄休眠时读不到，按一下手柄按键即可"
Write-Host "  · 桌面上的「$AppName-设置」可以调整样式与 HUD"
Write-Host "  · 卸载：开始菜单搜索「应用和功能」，或双击安装目录里的 卸载.bat"
Write-Host ""
if (-not $NoPause) { Read-Host "按回车键关闭本窗口" }

# Code signing policy

**Free code signing provided by [SignPath.io](https://about.signpath.io),
certificate by [SignPath Foundation](https://signpath.org).**

## Team roles

| Role | Member |
|---|---|
| Committers and reviewers | [@oitsukiii](https://github.com/oitsukiii) |
| Approvers | [@oitsukiii](https://github.com/oitsukiii) |

All team members use multi-factor authentication for both GitHub and SignPath access.

## Privacy policy

**This program will not transfer any information to other networked systems
unless specifically requested by the user or the person installing or operating it.**

本程序不会向任何联网系统传输信息，除非用户（或安装/运行它的人）明确要求。
具体说明：

- 程序只通过本地 **HID（USB）接口** 读取手柄状态，不发起任何网络请求
- 不收集、不上传任何遥测、使用统计或设备标识
- 设置与读数缓存只保存在本机用户目录（`%APPDATA%\betop-battery\`）
- 唯一的网络行为是用户手动执行 `git clone` / `pip install`（由包管理器发起）

## What gets signed

Only the binaries built by this project's own
[GitHub Actions workflow](.github/workflows/build.yml) from this repository's
source code are submitted for signing:

| Artifact | Description |
|---|---|
| `betop-battery.exe` | Tray application (no console window) |
| `betop-battery-cli.exe` | Command line version |

Both contain no bundled third-party executables; dependencies (Python standard
library, `hidapi`, `pystray`, `Pillow`) are compiled/packaged from their
published open-source releases.

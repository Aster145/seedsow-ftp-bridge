# Seedsow FTP Bridge

通过 **EasyCon ESP32-S3 手柄输入 + OBS 游戏画面**，为 Seedsow Lua Editor 提供电脑端 FTP 文件中转。游戏端使用经过校准的 8 色数据画面，每页携带 1664 字节。

An experimental Windows-hosted FTP bridge for Seedsow Lua Editor. Commands and uploads travel through an EasyCon controller; file contents return through an optical data grid captured by OBS. The console does **not** listen on a network FTP port.

## 工作方式

```text
FTP 客户端 → 电脑本机 FTP 服务 → ESP32-S3 手柄输入 → 游戏内 Lua 脚本
FTP 客户端 ← 电脑解析数据画面 ← OBS 采集卡画面   ← 游戏文件读取结果
```

FTP 服务只监听电脑的 `127.0.0.1:2121`。上传会调用游戏文件写入接口，下载会读取游戏文件并逐页显示为数据方块；电脑校验完整响应的 Adler32 后才返回内容。电脑上的文件副本不会被用来代替游戏读取结果。

这是实验性的小文件工具，**不会给游戏增加系统权限**。仓库不包含游戏存档、游戏资源、原作者安装存档或 ESP32 固件，也不修改原始存档 ZIP。

## 下载与准备

从 [Releases](https://github.com/Aster145/seedsow-ftp-bridge/releases) 下载使用包，或克隆本仓库。两者都需要自行安装 Python 和依赖，不包含 Python 运行时。

需要：

- Windows、Python 3，以及 `pyserial`、`websockets`、`Pillow`。
- 已能运行 [Seedsow Lua Editor](https://github.com/DialingSpoon527/seedsow-lua-editor) 的游戏环境，并已完成编辑器内的 A/B/X/Y/L/R 输入校准。
- 刷好兼容 [EasyCon](https://github.com/EasyConNS/EasyCon) 固件的 ESP32-S3：串口接电脑，原生 USB 接主机，能够实际控制游戏。
- OBS 与采集卡，能提供完整、未裁剪的 16:9 游戏源画面，并开启本机 WebSocket 服务器。

安装和配置：

```powershell
py -3 -m pip install -r requirements.txt
Copy-Item config.example.json config.json
notepad config.json
```

**已有 `config.json` 时保留它，不要再次复制覆盖。** `COM6` 只是示例，请改成自己的串口。`obs_source` 留空时，程序使用当前 OBS 场景中第一个启用的 `dshow_input` 采集源；有多个采集源时请填写准确的源名称。

OBS 连接使用本机安装配置中的端口及认证信息；程序不会把 OBS 密码写入本项目。`config.json` 已被 Git 忽略。使用前断开 EasyCon 软件的串口连接，避免占用同一个 COM 口。

## 首次安装游戏端脚本

1. 在游戏的 Seedsow Lua Editor 中进入 **Receive PC**。
2. 双击电脑上的 `INSTALL.cmd`。它只负责发送约 4.1 KiB 的安装包，当前时序下预计需要约 14 分钟。
3. 必须等游戏显示 `PC FILE SAVED: ftp_console_color.lua`。如果报告传输或校验错误，重新进入 Receive PC 重试，不要运行旧缓冲区。
4. 按 **L** 退出接收、松开，再按 **Y** 运行。出现 `FTP COLOR 0 / 1/1 / 5 bytes / OK` 和顶部 8 个色块表示 READY。

安装包会直接解压并保存完整可运行的 `ftp_console_color.lua`，不依赖旧版脚本。源码只做了注释和空白压缩，本仓库的同名文件保留可读格式。

之后在编辑器的 **File / new** 中打开已保存的 `ftp_console_color.lua`，再按 **Y** 即可运行。`START.cmd` 只启动电脑 FTP 服务，不会自动打开游戏、加载存档或运行游戏脚本。

已经安装 v0.2.0 彩色脚本时，无需重装游戏端，只需更新电脑程序并保留自己的 `config.json`。从旧黑白版迁移时，先用 `STOP.cmd` 停止电脑服务，游戏长按 L 返回编辑器，再按上述步骤直接安装彩色版。

## 8 色下载

彩色模式的每个格子代表 3 位数据。电脑从同一帧的 8 个色块读取实际颜色作为校准值，对每页独立校验，错误页会重新截图。整个文件仍做总校验。翻页时复用相同颜色和尺寸的图形对象，减少重新创建图层的开销；一次文件请求内复用 OBS 连接，避免每页重复认证。

**颜色增加的是下载和响应的容量，上传仍走手柄输入，速度不变。** 它不是标准二维码，普通二维码扫描器不能识别。

## 使用 FTP

游戏端处于数据画面时，双击 `START.cmd`。它会检查画面中的协议头，再开始监听。

| 设置 | 值 |
| --- | --- |
| 主机 | `127.0.0.1` |
| 端口 | `2121` |
| 用户名 | `seedsow` |
| 密码 | `seedsow` |
| 协议 | 普通 FTP，明文；不支持 SFTP / FTPS |
| 传输模式 | 被动模式 PASV / EPSV |
| 客户端超时 | 至少 600 秒；较大文件需要更长或禁用超时 |

客户端和服务必须在同一台电脑上，服务拒绝绑定局域网地址。保持 OBS、采集卡、ESP32-S3 和游戏脚本正常运行；传输期间不要另行操作手柄。一次只处理一个游戏请求，其他请求排队。

### 停止与恢复

| 入口 | 作用 |
| --- | --- |
| `START.cmd` | 前台启动电脑 FTP 服务；保持窗口打开 |
| `STOP.cmd` | 停止电脑服务，等待当前游戏请求完成，不退出游戏脚本 |
| `EXIT_GAME.cmd` | 检查数据画面后长按 L，返回 Lua 编辑器；应先停止电脑服务 |
| `INSTALL.cmd` | 首次发送安装包，需要手动进入 Receive PC 并确认保存成功 |

关闭 START 窗口会终止电脑服务，传输中应优先使用 `STOP.cmd`。关闭浏览器或断开 FTP 客户端不会停止服务。退出游戏脚本也可以在手柄上长按 L 约 1.5 秒；需要再次使用时按 Y 运行。

## 文件范围与限制

- 支持上传、下载、列目录；不提供删除、重命名和子目录操作。
- 文件名最多 64 个 ASCII 字符。首字符为字母、数字或下划线，其余还允许点和短横线。
- 文件操作受原游戏及编辑器接口限制，不提供其他游戏存档或系统文件访问。
- 列表来自游戏中的 `ftp-index.txt`：包含通过本工具上传的文件，以及存在时的 `guodong.lua`。**这不是整个存档目录的枚举。** 未出现在列表中但名称已知且接口允许读取的文件，可以直接 RETR。
- 单个上传或下载最多 64 KiB。上传通道约每秒 5 字节，1 KiB 约需 3–4 分钟，适合小型 Lua；64 KiB 是协议上限，不代表实用传输规模。
- 同名上传会覆盖原文件，游戏端的保护名称规则仍然生效。`ftp-index.txt` 保留作内部索引。
- 游戏不提供真实修改时间；传统 LIST 日期为占位值，MDTM 返回不可用。
- 接收失败、校验失败或超时不会自动重放写入请求。先查看游戏画面和电脑错误信息，再决定是否重试。

## 已验证范围

2026-10-29，在一套 Windows / NS2 / ESP32-S3 EasyCon / OBS 环境中完成：

- 列出游戏内已有的 720 字节 Lua 文件，并经 FTP 下载，与原源码逐字节一致。
- 上传 279 字节测试文件，含全部 256 种字节值和中文 UTF-8；再从游戏下载，逐字节一致。
- 上传后目录返回新文件及正确长度。
- 停止并重启电脑服务，退出并重新运行 Lua 脚本后，索引文件仍可读取。

彩色版另完成 14 字节二进制/中文文件的 FTP 上传、回读和目录长度检查。测速以同一个 16 KiB 文件分别进行三轮真实 FTP 下载，全部逐字节一致。下表为三轮中位数；两种模式使用相同的新版电脑端程序、手柄时序和截图重试策略：

| 项目 | 黑白 SF01 | 8 色 SF02 |
| --- | ---: | ---: |
| 每页有效数据 | 512 字节 | 1664 字节 |
| 16 KiB 所需页数 | 32 | 10 |
| 完整 FTP 下载耗时 | 26.02 秒 | 17.69 秒 |
| 其中：画面响应接收阶段 | 13.59 秒 | 5.17 秒 |

这套设备上，总下载耗时减少约 **32%**，画面响应接收阶段吞吐量约为原来的 **2.63 倍**。每个请求仍有约 12.4 秒的手柄命令发送等前置开销，因此整次下载没有按每页容量等比例提速。测试内容为 0–255 字节序列重复 64 次；每页与整文件都经过校验。可查看[逐轮测速数据及计时口径](benchmarks/color-transfer-2026-10-29.json)。这些结果不代表所有采集卡和文件的固定速度。

尚未测试完整的 64 KiB 真机传输，也未在上述验证中关闭游戏应用后重新启动。其他固件版本、主机环境或输入时序需要自行验证。仓库不附带个人采集截图、运行日志或游戏文件。

## 开发与测试

```powershell
py -3 -m unittest discover -s tests -v
py -3 pack_bootstrap.py ftp_console_color.lua ftp_bootstrap.lua
```

测试覆盖本机模拟 FTP、画面解码、OBS 会话复用、坏页重截、独立彩色安装包及翻页矩形池。它们不会访问串口、OBS 或主机，不等同于硬件验证；Lua 5.1 运行时测试在未安装该解释器时会跳过。

修改 `ftp_console_color.lua` 后，执行上述打包命令重新生成 `ftp_bootstrap.lua`。

主要文件：

| 文件 | 用途 |
| --- | --- |
| `ftp_console_color.lua` | 游戏内文件请求、索引和 8 色光学响应 |
| `ftp_bootstrap.lua` | 可直接安装彩色脚本的独立安装包 |
| `pack_bootstrap.py` | Lua 词法压缩与 LZSS/ASCII85 安装包生成 |
| `transport.py` | EasyCon 串口发送、OBS 截图、光学响应解码 |
| `optical.py` | SF01 黑白和 SF02 校准 8 色解码、逐页校验 |
| `ftp_server.py` | 本机被动 FTP 服务 |

## 致谢与许可

项目依赖 [Seedsow Lua Editor](https://github.com/DialingSpoon527/seedsow-lua-editor) 的运行环境、文件 API 和 SSE2 接收协议，使用 [EasyCon](https://github.com/EasyConNS/EasyCon) 的手柄固件协议，并通过 OBS WebSocket 获取游戏画面。

本仓库代码采用 [BSD Zero Clause License](LICENSE)。相关项目和外部依赖分别遵循其自身许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

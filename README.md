# luci-app-jxnu-srun

OpenWrt 深澜校园网（SRun 4000）自动认证客户端，提供 CLI / LuCI 两种使用方式。

感谢 [@guiguisocute](https://github.com/guiguisocute) 的协助！

## 预览

### LuCI Web 界面
<p align="center">
    <img src="https://github.com/matthewlu070111/luci-app-jxnu-srun/raw/doc/img/README01.jpg">
</p>

## 功能

- 自动校园网认证，自动检测断线并重连，支持有线 / 无线接入
- 多校园网账号、多热点配置管理，一键登录登出切换
- 可配置夜间时段自动切换到热点，恢复后自动切回校园网（适应宿舍定时断网环境）
- 结构化运行日志落盘（`/var/log/jxnu_srun.log`）
- 完整 CLI（`srunnet`）：状态查询、登录登出、配置管理、账号 / 热点 CRUD，功能对齐 LuCI
- 支持多学校配置文件，可扩展适配其他深澜校园网环境

### 未来功能
- 适配 UA3F
- 支持多号多拨负载均衡网络叠加
- 适配更多高校的深澜校园网环境
- 更多账号功能，如账号分组、规则管理
- ……

## 安装包说明

仓库构建产出两个 ipk 包：

| 包名 | 说明 | 依赖 |
|------|------|------|
| `jxnu-srun` | 基础包：守护进程 + CLI | `python3-light` |
| `luci-app-jxnu-srun` | LuCI Web 界面（自动依赖 `jxnu-srun`） | `jxnu-srun`、`luci-base` |

- 只需要 CLI：安装 `jxnu-srun` 即可
- 需要 Web 管理界面：安装 `luci-app-jxnu-srun`（会自动拉取基础包）

## 安装与使用

### 安装

1. 下载最新 ipk 包：[Releases](https://github.com/matthewlu070111/luci-app-jxnu-srun/releases)
2. 上传到路由器并安装：
   ```sh
   # 仅 CLI
   opkg install jxnu-srun_*.ipk

   # 含 LuCI 界面
   opkg install luci-app-jxnu-srun_*.ipk
   ```
3. 启用服务：
   ```sh
   /etc/init.d/jxnu_srun enable
   /etc/init.d/jxnu_srun restart
   ```

### LuCI 使用

在 LuCI 页面进入 **服务 → JXNU Srun**，在「基础设置」标签页中：

- **登录配置**：选择学校（支持多校区）
- **校园网账号**：添加学工号、密码、运营商，支持多账号管理
- **热点配置**：配置个人热点 SSID 和密码，供夜间自动切换使用
- **手动登录 / 登出**：随时触发，带进度反馈弹窗
- **手动切网**：一键切到热点或切回校园网

保存并应用后守护进程自动启动。

### CLI 使用

安装后可直接使用 `srunnet` 命令（无参数等同 `srunnet status`）：

```sh
# 查看当前状态
srunnet
srunnet status

# 登录 / 登出 / 重新登录
srunnet login
srunnet logout
srunnet relogin

# 查看实时日志（Ctrl+C 退出）
srunnet log

# 查看最近 50 行日志
srunnet log -n 50

# 启用 / 禁用守护服务
srunnet enable
srunnet disable

# 手动切换网络
srunnet switch hotspot
srunnet switch campus

# 列出可用学校配置（JSON 输出）
srunnet schools
```

#### 配置管理

```sh
# 查看完整配置
srunnet config
srunnet config show

# 查询 / 设置单个标量值
srunnet config get interval
srunnet config set interval=30 enabled=1

# 从 JSON 文件导入配置
srunnet config set -f my_config.json

# 校园网账号管理
srunnet config account              # 列出所有账号
srunnet config account add          # 交互式添加账号
srunnet config account edit campus-1
srunnet config account rm campus-2
srunnet config account default campus-1

# 热点配置管理
srunnet config hotspot              # 列出所有热点
srunnet config hotspot add          # 交互式添加热点
srunnet config hotspot edit hotspot-1
srunnet config hotspot rm hotspot-2
srunnet config hotspot default hotspot-1
```

## GitHub Actions 一键编译

仓库内置两个工作流：

| 工作流 | 用途 |
|--------|------|
| `pre-release build` | 开发预览构建，可选发布 pre-release |
| `Version Release Build` | 正式版本构建 + 发布 |

在 GitHub 页面进入 **Actions**，选择对应工作流，点击 **Run workflow** 即可构建。产物包含 `jxnu-srun` 和 `luci-app-jxnu-srun` 两个 ipk。



## 开发者指南
### 项目结构

```
root/
├── etc/init.d/jxnu_srun          # procd 服务管理脚本
├── usr/bin/srunnet                # CLI 入口脚本
└── usr/lib/jxnu_srun/
    ├── client.py                  # 入口（thin wrapper）
    ├── daemon.py                  # 守护循环 + CLI 参数解析
    ├── config.py                  # 配置读写 + 状态管理
    ├── srun_auth.py               # SRun 认证协议实现
    ├── crypto.py                  # 加密算法（自定义 Base64、HMAC、BX1）
    ├── network.py                 # HTTP 客户端（urllib/wget/uclient-fetch）
    ├── wireless.py                # WiFi STA 配置管理
    ├── orchestrator.py            # 登录/登出编排逻辑
    ├── snapshot.py                # 运行时快照
    └── schools/
        ├── __init__.py            # 学校配置自动发现
        ├── _base.py               # SchoolProfile 基类
        └── jxnu.py                # 江西师范大学配置
```

### 适配其他学校

在 `root/usr/lib/jxnu_srun/schools/` 下新建 Python 文件，继承 `SchoolProfile` 并填写学校参数：

```python
from _base import SchoolProfile

class Profile(SchoolProfile):
    NAME = "XX大学"
    SHORT_NAME = "xxu"
    DESCRIPTION = "XX大学深澜认证配置"
    CONTRIBUTORS = ("@your_github",)

    ALPHA = "..."           # 深澜自定义 Base64 字母表
    DEFAULT_BASE_URL = "http://x.x.x.x"
    DEFAULT_AC_ID = "1"

    OPERATORS = (
        {"id": "cmcc", "label": "中国移动", "verified": False},
        {"id": "ctcc", "label": "中国电信", "verified": False},
        {"id": "cucc", "label": "中国联通", "verified": False},
    )
    NO_SUFFIX_OPERATORS = ()
```

放入后重启服务即可在 LuCI 中选择。欢迎提交 PR 分享你的学校配置！

## License
WTFPL

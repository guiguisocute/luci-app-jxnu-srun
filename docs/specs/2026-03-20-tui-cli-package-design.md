# TUI 交互界面与独立 CLI 包设计

**日期**: 2026-03-20
**状态**: 已确认

## 背景

现有 `luci-app-jxnu-srun` 的 CLI 仅提供裸 `argparse` + `print()` 输出，无颜色、无格式化、无交互界面。需要：

1. 一个基于 curses 的交互式 TUI 仪表盘，在 OpenWrt 路由器上通过 SSH 直接使用
2. 一个交互式配置向导，替代 LuCI 表单实现全部配置功能
3. 重新划分包结构：CLI 包作为基础包独立发行，LuCI 包作为附加层依赖 CLI 包

## 约束

- 目标环境：OpenWrt 路由器，`python3-light` + `python3-curses`
- 最小终端：80x24
- TUI 与 daemon 通过文件轮询通信（沿用现有 state.json / action.json 机制）
- 不改动现有 Python 模块（daemon.py、config.py、orchestrator.py 等），TUI 是纯新增的视图层

## 模块架构

### 新增文件（`root/usr/lib/jxnu_srun/`）

```
tui_widgets.py    — curses 基础组件库（~250-300 行）
tui_dashboard.py  — 仪表盘主界面（--tui 入口）
tui_config.py     — 配置向导（--config 入口）
```

### 依赖关系

```
tui_widgets.py  (仅依赖 curses, 零项目内依赖)
     ↑
tui_dashboard.py  (依赖 tui_widgets + config.py 的文件路径常量)
tui_config.py     (依赖 tui_widgets + config.py 的 load/save)
```

### daemon.py 改动

仅在 `main()` 的 argparse 中新增两个入口：

```python
parser.add_argument("--tui", action="store_true", help="interactive dashboard")
parser.add_argument("--config", action="store_true", help="interactive config wizard")
```

## TUI 仪表盘（--tui）

### 屏幕布局

```
行1:   ┌─ JXNU SRun v1.2.x ──────────────── 14:32:05 ─┐
行2:   │ 状态  ● 在线           IP     10.168.1.100     │
行3:   │ 模式  校园网(WiFi)     SSID   JXNU             │
行4:   │ 账号  2024xxx@cmcc     连通性 互联网可达        │
行5:   │ 守护  运行中           间隔   30s               │
行6:   ├─ 日志 ─────────────────────────────────────────│
行7-21:│ 14:32:01 [JXNU-SRun] 在线，下一次检测 30 秒   │
       │ 14:31:31 [JXNU-SRun] 在线，下一次检测 30 秒   │
       │ ...（最多 15 行日志，新日志从顶部进入）         │
行22:  │                                                │
行23:  ├────────────────────────────────────────────────│
行24:  │ [L]登录 [O]登出 [H]热点 [C]校园 [R]刷新 [Q]退出│
       └────────────────────────────────────────────────┘
```

### 自适应规则

- 宽度 < 80：状态面板从双列退化为单列
- 高度 < 24：压缩日志区行数
- 宽度 > 100：日志行不截断
- 宽 < 40 或高 < 10：清屏显示 "终端太小" 提示

### 状态面板字段映射

| 显示 | state.json 字段 | 格式化 |
|------|-----------------|--------|
| 状态 | `connectivity_level` | `● 在线`(绿) / `● 认证中`(黄) / `● 离线`(红) |
| 模式 | `mode_label` | 原样 |
| 账号 | `campus_account_label` | 原样 |
| 守护 | `daemon_running` + `enabled` | 运行中/已停止/未启用 |
| IP | `current_ip` | 空则 `--` |
| SSID | `current_ssid` | 空则 `--` |
| 连通性 | `connectivity` | 原样 |
| 间隔 | config.json `interval` | `{n}s` |

### 快捷键

| 按键 | 动作 | 实现 |
|------|------|------|
| `L` | 手动登录 | 写 action.json `{"action": "manual_login"}` |
| `O` | 手动登出 | 写 action.json `{"action": "manual_logout"}` |
| `H` | 切换热点 | 写 action.json `{"action": "switch_hotspot"}` |
| `C` | 切换校园 | 写 action.json `{"action": "switch_campus"}` |
| `R` | 立即刷新 | 重读 state.json + 日志文件 |
| `Q` | 退出 | 退出 curses，恢复终端 |

### 操作反馈流程

1. 用户按操作键 → 底部变为 `确认XXX? [Y/N]`
2. 按 `Y` → 写 action.json，状态栏显示 `⏳ 已提交: XXX`（黄色）
3. 轮询 state.json 发现 `pending_action` 清空 → 显示结果（绿/红）
4. 3 秒后恢复正常显示

### 刷新机制

- `curses.halfdelay(10)` — 1 秒无输入自动刷新
- `stat()` state.json 检查 mtime，变化才重读
- 日志文件：记录 seek 位置，只读增量追加到缓冲区

## 配置向导（--config）

### 主菜单

```
JXNU SRun 配置向导
═══════════════════════════
> 校园网账号管理
  热点配置管理
  基础设置
  高级设置
  退出
```

`↑↓` 移动光标，`Enter` 进入，`Q` 退出。

### 校园网账号管理

```
校园网账号
═══════════════════════════
  [1] 2024xxx@cmcc (WiFi) ★
  [2] 2024xxx@ctcc (有线)

> 添加账号
  返回

[Enter]编辑  [D]删除  [S]设为默认
```

- `★` 标记默认账号
- 只剩一个账号时禁止删除

### 编辑表单

```
编辑账号
═══════════════════════════
学工号      [ 2024xxx        ]
运营商      < cmcc ▸ >
接入方式    < wifi ▸ >
密码        [ ********       ]
认证地址    [ http://172.17.1.2 ]
AC_ID       [ 1              ]
SSID        [ JXNU           ]
BSSID       [                ]
Radio       < auto ▸ >

[↑↓]切换  [Enter]编辑  [Esc]取消  [F2]保存
```

**字段类型**：

| 类型 | 交互方式 | 组件 |
|------|----------|------|
| 文本输入 | Enter 激活，输入，Enter 确认 | `EditField` |
| 密码输入 | 同上，显示 `*` 掩码 | `EditField(masked=True)` |
| 下拉选择 | `←→` 切换选项 | `Dropdown` |

**条件联动**：
- 接入方式 = wired → SSID / BSSID / Radio 灰化
- 运营商选项从 school profile 动态读取

**校验规则**：
- 学工号：非空
- 密码：非空
- 认证地址：非空，`http(s)://` 开头
- 校验失败：字段红色高亮，底部显示错误，禁止保存

### 热点配置管理

同账号管理结构，字段：名称、SSID、加密方式、密码、Radio。

### 基础设置

```
基础设置
═══════════════════════════
学校        < jxnu ▸ >
自动登录    < 开启 ▸ >
夜间停用    < 开启 ▸ >
  停用开始  [ 23:00          ]
  停用结束  [ 06:00          ]
  强制登出  < 开启 ▸ >
SSID故障转移 < 开启 ▸ >
```

条件联动：夜间停用关闭 → 子字段灰化。

### 高级设置

```
高级设置
═══════════════════════════
检测间隔(秒)       [ 30            ]
退避重试           < 开启 ▸ >
  最大重试次数     [ 0             ]
  初始等待(秒)     [ 5             ]
  最大等待(秒)     [ 300           ]
连通性检测方式     < internet ▸ >
切换就绪超时(秒)   [ 15            ]
热点回退           < 开启 ▸ >
```

### 数据持久化

- 读：`load_config()` 加载 config.json
- 写：`F2` 保存时直接写 config.json
- 不经过 UCI，纯 CLI 版直接操作 JSON

## 组件库 tui_widgets.py

### 组件清单

| 组件 | 职责 | 仪表盘 | 配置 |
|------|------|:------:|:----:|
| `BorderBox` | 边框 + 标题 | ✓ | ✓ |
| `StatusPanel` | 键值对网格，自适应列数 | ✓ | |
| `LogPanel` | 滚动日志区 | ✓ | |
| `ActionBar` | 底部快捷键提示 | ✓ | ✓ |
| `MenuList` | ↑↓ 光标选择列表 | | ✓ |
| `EditField` | 单行文本输入，支持掩码 | | ✓ |
| `Dropdown` | ←→ 选项切换 | | ✓ |
| `ConfirmDialog` | 居中 Y/N 弹窗 | ✓ | ✓ |
| `Toast` | 底部临时消息 | ✓ | ✓ |

### 设计原则

**无状态渲染**：组件是纯函数 `draw(win, x, y, w, h, data)`，不持有状态。

**输入组件返回值**：
- `EditField.run()` → 编辑后的字符串，Esc 返回 None
- `MenuList.run()` → 选中项 index，Q/Esc 返回 -1
- `Dropdown.run()` → 选中项 index

**颜色方案**：

```python
COLOR_OK     = 1  # 绿 — 在线、成功
COLOR_WARN   = 2  # 黄 — 等待、处理中
COLOR_ERR    = 3  # 红 — 离线、错误
COLOR_DIM    = 4  # 灰 — 禁用字段
COLOR_ACCENT = 5  # 青 — 标题、边框
COLOR_SELECT = 6  # 反色 — 当前选中项
```

**终端尺寸处理**：
- 每次刷新 `getmaxyx()` 检测变化
- 宽 < 40 或高 < 10：显示 "终端太小"
- `KEY_RESIZE` 触发重绘

## 包结构与发行

### 包关系

```
jxnu-srun (基础包)              luci-app-jxnu-srun (附加包)
├── Depends: python3-light      ├── Depends: jxnu-srun
│            python3-curses     │            luci-base
│                               │
├── /usr/lib/jxnu_srun/         ├── /usr/lib/lua/luci/
│   ├── client.py               │   ├── controller/jxnu_srun.lua
│   ├── daemon.py               │   └── model/cbi/jxnu_srun.lua
│   ├── config.py               │
│   ├── crypto.py               └── (仅 LuCI 视图层)
│   ├── network.py
│   ├── wireless.py
│   ├── srun_auth.py
│   ├── orchestrator.py
│   ├── snapshot.py
│   ├── tui_widgets.py
│   ├── tui_dashboard.py
│   ├── tui_config.py
│   └── schools/
│
├── /etc/init.d/jxnu_srun
└── /etc/uci-defaults/
```

### Makefile

```makefile
define Package/jxnu-srun
  SECTION:=net
  CATEGORY:=Network
  TITLE:=JXNU SRun client (CLI/TUI)
  DEPENDS:=+python3-light +python3-curses
endef

define Package/luci-app-jxnu-srun
  SECTION:=luci
  CATEGORY:=LuCI
  TITLE:=JXNU SRun client (LuCI)
  DEPENDS:=+jxnu-srun +luci-base
endef
```

- `jxnu-srun`：完整功能，独立可用
- `luci-app-jxnu-srun`：仅 Lua 文件，依赖 `jxnu-srun`，装上多 Web 界面
- 两者可共存，不冲突

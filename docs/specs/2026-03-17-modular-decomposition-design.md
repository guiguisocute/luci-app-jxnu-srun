# client.py 模块化拆分 + School Profile 插件体系设计

> 日期: 2026-03-17
> 状态: 待实施
> 分支: feature-1.2.x

---

## 1. 背景与问题

`client.py` 当前是一个 3370+ 行的单文件 Python 守护进程，承担了 7 个完全不同的职责：

| 职责 | 大致行范围 | 行数 |
|---|---|---|
| 配置管理（加载/迁移/解析/保存） | 1-805 | ~800 |
| 网络基础设施（HTTP、IP、UCI/wireless） | 1019-1550 | ~530 |
| WiFi 切换（STA profile、failover） | 1550-2228 | ~680 |
| SRun 协议加密（Base64/MD5/SHA1/BX1） | 2229-2390 | ~160 |
| SRun 认证（challenge/login/logout API） | 2390-2777 | ~390 |
| 手动操作（manual login/logout 编排） | 2778-2967 | ~190 |
| 守护循环（daemon tick、runtime action） | 2968-3370 | ~400 |

### 核心耦合点

1. **`run_once()` 直接调用 `prepare_campus_for_login()`** -- 登录逻辑和 WiFi 网络准备硬绑定。登录动作不应该关心 STA 接口是否连上了正确的 SSID。
2. **`run_manual_login()` 同时编排了 6 件事**：服务开关保护、在线检查、清理旧会话、禁用 STA、认证、终态校验。
3. **`run_once_with_retry()` 在重试循环中调用 `load_config()` 和 `in_quiet_window()`** -- 重试策略和业务策略混在一起。
4. **`build_runtime_snapshot()` 同时查询 WiFi 状态、IP 地址、互联网连通性、SRun 在线状态** -- 一个函数负责构建整个世界的快照。
5. **`_daemon_tick_active()` 直接内联了**在线检测、断线判断、重连、failover 检查四层逻辑。

### 开源扩展需求

SRun 认证协议被多所中国高校使用，不同学校之间存在差异：

- 自定义 Base64 字母表（ALPHA）几乎每校不同
- 运营商后缀（cmcc/ctcc/cucc/xn 等）每校不同且需要独立验证
- 少数学校的加密算法或 API 端点有变化

需要一个插件机制，让其他学校的开发者以最小的代价贡献适配。

---

## 2. 设计目标

1. 将 `client.py` 按职责拆分为 7 个独立模块 + 1 个 school profile 包
2. 依赖图为 DAG（无环），每个模块可独立理解和测试
3. 其他学校开发者只需新建一个 ~40 行的 Python 文件即可完成适配
4. LuCI 界面支持学校选择下拉框，运营商验证状态精确到每个学校的每个运营商
5. 零破坏性升级：入口文件、CLI 接口、配置格式完全向后兼容

---

## 3. 模块架构

### 3.1 文件结构

```
/usr/lib/jxnu_srun/
  client.py            # 入口（~30行）：argparse + from daemon import main
  config.py            # 配置管理（~500行）
  network.py           # 网络基础设施（~350行）
  wireless.py          # 无线管理（~680行）
  crypto.py            # 默认加密工具（~160行，纯函数）
  srun_auth.py         # SRun 认证 API（~400行）
  orchestrator.py      # 操作编排 + 重试（~350行）
  daemon.py            # 守护循环 + runtime state（~400行）
  schools/
    __init__.py        # 注册表：discover() / get_profile()
    _base.py           # SchoolProfile 基类（~200行）
    jxnu.py            # 江西师范大学（~40行）
  defaults.json        # （不变）
  config.json          # （运行时生成，格式兼容）
```

### 3.2 模块职责边界

| 模块 | 做什么 | 不做什么 |
|---|---|---|
| `config.py` | 加载/保存/迁移 JSON 配置；默认值；常量定义；`append_log()` | 不知道 SRun 协议存在 |
| `network.py` | `http_get()`；IP 工具；`run_cmd()`；连通性检测 | 不知道 UCI wireless 结构 |
| `wireless.py` | UCI 无线解析；STA section 管理；SSID 切换；failover | 不知道 SRun 登录是什么 |
| `crypto.py` | Base64（自定义字母表）；MD5；SHA1；BX1 异或编码 | 无状态纯函数，不做 HTTP，不读配置 |
| `srun_auth.py` | challenge -> 加密 -> login/logout API -> 在线查询 | 不管 WiFi 连没连，不管重试 |
| `orchestrator.py` | 手动登录/登出全流程编排；`run_once_with_retry()` | 不管守护循环的 tick 逻辑 |
| `daemon.py` | 守护循环；runtime action 分发；quiet hours 切换 | 不直接调用 SRun API |
| `schools/_base.py` | 默认 SRun 协议参数 + 可覆盖方法；供子类继承 | 不做任何 I/O |
| `schools/xxx.py` | 声明本校的 ALPHA、运营商、URL、覆盖加密方法 | 只声明差异，不重写全部 |

### 3.3 依赖图（DAG，无环）

```
config.py         <- (独立)
crypto.py         <- (独立，纯函数)
schools/_base.py  <- crypto.py
schools/jxnu.py   <- schools/_base.py
network.py        <- config.py
wireless.py       <- network.py, config.py
srun_auth.py      <- schools/, network.py, config.py
orchestrator.py   <- srun_auth.py, wireless.py, config.py
daemon.py         <- orchestrator.py, wireless.py, config.py, srun_auth.py
client.py         <- daemon.py
```

---

## 4. School Profile 接口

### 4.1 基类 `schools/_base.py`

核心设计原则：**Profile 只做数据变换，不做 I/O。** HTTP 请求由 `srun_auth.py` 负责，Profile 只构建参数和解析响应。

```python
import crypto

class SchoolProfile:
    # -- 元数据 --
    NAME = ""                    # "江西师范大学"
    SHORT_NAME = ""              # "jxnu"
    DESCRIPTION = ""             # "深澜 SRun 4000 系列认证"
    CONTRIBUTORS = []            # ["@github_user"]

    # -- 运营商 --
    OPERATORS = []
    # [
    #   {"id": "cucc", "label": "中国联通", "verified": True},
    #   {"id": "xn",   "label": "校园网",   "verified": True},
    # ]
    NO_SUFFIX_OPERATORS = []     # ["xn"] -- 用户名不加 @后缀

    # -- 协议参数 --
    ALPHA = "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"
    DEFAULT_BASE_URL = ""
    DEFAULT_AC_ID = "1"
    DEFAULT_N = "200"
    DEFAULT_TYPE = "1"
    DEFAULT_ENC = "srun_bx1"

    # -- API 路径（极少数学校需要改）--
    API_CHALLENGE = "/cgi-bin/get_challenge"
    API_PORTAL = "/cgi-bin/srun_portal"
    API_RAD_USER_INFO = "/cgi-bin/rad_user_info"
    API_RAD_USER_DM = "/cgi-bin/rad_user_dm"

    # -- 用户名构建 --
    def build_username(self, user_id, operator):
        if operator in self.NO_SUFFIX_OPERATORS:
            return user_id
        return user_id + "@" + operator

    def build_urls(self, base_url):
        return {
            "init_url": base_url,
            "get_challenge_api": base_url + self.API_CHALLENGE,
            "srun_portal_api": base_url + self.API_PORTAL,
            "rad_user_info_api": base_url + self.API_RAD_USER_INFO,
            "rad_user_dm_api": base_url + self.API_RAD_USER_DM,
        }

    # -- 加密方法（大多数学校只需改 ALPHA 类属性，不用碰这些方法）--
    def get_base64(self, value):
        return crypto.get_base64(value, self.ALPHA)

    def get_xencode(self, msg, key):
        return crypto.get_xencode(msg, key)

    def get_md5(self, password, token):
        return crypto.get_md5(password, token)

    def get_sha1(self, value):
        return crypto.get_sha1(value)

    def get_info(self, username, password, ip, ac_id, enc):
        return crypto.get_info(username, password, ip, ac_id, enc)

    # -- 复合加密（可覆盖）--
    def do_complex_work(self, cfg, ip, token):
        """加密编排: 把 challenge token 变成登录所需的三件套"""
        i_value = self.get_info(
            cfg["username"], cfg["password"], ip, cfg["ac_id"], cfg["enc"]
        )
        i_value = "{SRBX1}" + self.get_base64(self.get_xencode(i_value, token))
        hmd5 = self.get_md5(cfg["password"], token)
        chksum = self.get_sha1(
            self._build_chkstr(token, cfg, hmd5, ip, i_value)
        )
        return i_value, hmd5, chksum

    def _build_chkstr(self, token, cfg, hmd5, ip, i_value):
        return (
            token + cfg["username"]
            + token + hmd5
            + token + cfg["ac_id"]
            + token + ip
            + token + cfg["n"]
            + token + cfg["type"]
            + token + i_value
        )

    # -- 请求构建 / 响应解析（不做 HTTP，纯数据变换）--
    def build_login_params(self, cfg, ip, i_value, hmd5, chksum):
        """返回 dict，由 srun_auth.py 传给 http_get()"""
        now = int(__import__("time").time() * 1000)
        return {
            "callback": "jQuery11240645308969735664_" + str(now),
            "action": "login",
            "username": cfg["username"],
            "password": "{MD5}" + hmd5,
            "ac_id": cfg["ac_id"],
            "ip": ip,
            "chksum": chksum,
            "info": i_value,
            "n": cfg["n"],
            "type": cfg["type"],
            "os": "openwrt",
            "name": "openwrt",
            "double_stack": "0",
            "_": now,
        }

    def parse_login_response(self, data):
        """返回 (ok, message)"""
        error = str(data.get("error", "")).lower()
        result = str(data.get("res", "")).lower()
        success = error == "ok" or result == "ok"
        message = (
            data.get("error_msg") or data.get("error") or "unknown response"
        )
        return success, str(message)

    def build_logout_params(self, cfg, ip):
        """返回 dict，由 srun_auth.py 传给 http_get()"""
        now = int(__import__("time").time())
        username = self._get_logout_username(cfg)
        unbind = "1"
        return {
            "callback": "jQuery11240645308969735664_" + str(now),
            "time": str(now),
            "unbind": unbind,
            "ip": ip,
            "username": username,
            "sign": crypto.get_sha1(
                str(now) + username + ip + unbind + str(now)
            ),
        }

    def parse_logout_response(self, data):
        """返回 (ok, message)"""
        error = str(data.get("error", "")).lower()
        result = str(data.get("res", "")).lower()
        success = error == "ok" or result == "ok"
        message = (
            data.get("error_msg")
            or data.get("error")
            or data.get("res")
            or "unknown response"
        )
        return success, str(message)

    def parse_online_status(self, data, expected_username):
        """返回 (online, username, message)"""
        if str(data.get("error", "")).lower() != "ok":
            msg = data.get("error_msg") or data.get("error") or "unknown response"
            return False, "", msg
        online_name = str(data.get("user_name", "")).strip()
        expected_main = expected_username.split("@", 1)[0]
        if online_name and online_name == expected_main:
            return True, online_name, "online"
        if online_name:
            return True, online_name, "online: " + online_name
        return False, "", "offline"

    def _get_logout_username(self, cfg):
        user_id = str(cfg.get("user_id", "")).strip()
        if user_id:
            return user_id
        return str(cfg.get("username", "")).split("@", 1)[0].strip()
```

### 4.2 覆盖成本梯度

| 改什么 | 覆盖几个字段/方法 | 适用场景 |
|---|---|---|
| 只改 ALPHA + URL + 运营商 | 3 个类属性 | 90% 的学校 |
| 加密算法微调 | 覆盖 `get_xencode()` 或 `get_info()` | 少数学校 |
| 请求格式不同 | 覆盖 `build_login_params()` | 极少数学校 |
| 整个认证流程不同 | 覆盖 `do_complex_work()` + 请求/响应 | 几乎不会 |

### 4.3 示例 `schools/jxnu.py`

```python
from ._base import SchoolProfile


class Profile(SchoolProfile):
    NAME = "江西师范大学"
    SHORT_NAME = "jxnu"
    DESCRIPTION = "深澜 SRun 4000 系列认证（瑶湖/青山湖校区）"
    CONTRIBUTORS = ["@zengjiaxuan"]

    ALPHA = "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"
    DEFAULT_BASE_URL = "http://172.17.1.2"
    DEFAULT_AC_ID = "1"

    OPERATORS = [
        {"id": "cucc", "label": "中国联通", "verified": True},
        {"id": "xn",   "label": "校园网",   "verified": True},
        {"id": "cmcc", "label": "中国移动", "verified": False},
        {"id": "ctcc", "label": "中国电信", "verified": False},
    ]
    NO_SUFFIX_OPERATORS = ["xn"]
```

### 4.4 注册表 `schools/__init__.py`

```python
import os
import importlib.util

_PROFILES = {}
_LOADED = False


def _discover():
    global _LOADED
    if _LOADED:
        return
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    for fname in sorted(os.listdir(pkg_dir)):
        if fname.startswith("_") or not fname.endswith(".py"):
            continue
        mod_name = fname[:-3]
        filepath = os.path.join(pkg_dir, fname)
        try:
            spec = importlib.util.spec_from_file_location(
                "schools." + mod_name, filepath,
                submodule_search_locations=[],
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "Profile"):
                profile = mod.Profile()
                _PROFILES[profile.SHORT_NAME] = profile
        except Exception:
            pass
    _LOADED = True


def get_profile(short_name):
    """获取指定学校的 profile，找不到返回 None"""
    _discover()
    return _PROFILES.get(short_name)


def list_schools():
    """返回所有学校元数据列表（给 LuCI / CLI 用）"""
    _discover()
    return [
        {
            "short_name": p.SHORT_NAME,
            "name": p.NAME,
            "description": p.DESCRIPTION,
            "contributors": p.CONTRIBUTORS,
            "operators": p.OPERATORS,
        }
        for p in sorted(_PROFILES.values(), key=lambda p: p.SHORT_NAME)
    ]
```

### 4.5 `srun_auth.py` 调用 Profile

Profile 不碰 HTTP。`srun_auth.py` 做胶水：

```python
from network import http_get, parse_jsonp
from config import append_log, localize_error


def run_once(profile, cfg, bind_ip=None):
    """纯认证流程：challenge -> 加密 -> login API。
    调用前，WiFi 已就绪，cfg 已确定。"""
    urls = profile.build_urls(cfg["base_url"])
    ip = init_getip(urls["init_url"], bind_ip=bind_ip)
    token, ip = get_token(
        urls["get_challenge_api"], cfg["username"], ip, bind_ip=bind_ip
    )
    i_value, hmd5, chksum = profile.do_complex_work(cfg, ip, token)
    params = profile.build_login_params(cfg, ip, i_value, hmd5, chksum)
    data = parse_jsonp(
        http_get(urls["srun_portal_api"], params=params, bind_ip=bind_ip)
    )
    ok, message = profile.parse_login_response(data)

    # challenge 过期重试（协议层面，不算业务重试）
    if not ok and "challenge_expire_error" in message.lower():
        token, ip = get_token(
            urls["get_challenge_api"], cfg["username"], ip, bind_ip=bind_ip
        )
        i_value, hmd5, chksum = profile.do_complex_work(cfg, ip, token)
        params = profile.build_login_params(cfg, ip, i_value, hmd5, chksum)
        data = parse_jsonp(
            http_get(urls["srun_portal_api"], params=params, bind_ip=bind_ip)
        )
        ok, message = profile.parse_login_response(data)

    # no_response_data 兜底：查一次在线状态
    if not ok and "no_response_data_error" in message.lower():
        try:
            online, _, _ = query_online_identity(
                profile, urls["rad_user_info_api"],
                cfg["username"], bind_ip=bind_ip,
            )
            if online:
                return True, "已在线"
        except Exception:
            pass

    if ok:
        return True, "登录成功"
    return False, "登录失败: " + localize_error(message)


def do_logout(profile, cfg, bind_ip=None):
    """纯登出流程。"""
    urls = profile.build_urls(cfg["base_url"])
    ip = init_getip(urls["init_url"], bind_ip=bind_ip)
    params = profile.build_logout_params(cfg, ip)
    data = parse_jsonp(
        http_get(urls["rad_user_dm_api"], params=params, bind_ip=bind_ip)
    )
    return profile.parse_logout_response(data)


def query_online_identity(profile, rad_user_info_api, expected_username, bind_ip=None):
    """查询在线状态，返回 (online, username, message)"""
    now = int(__import__("time").time() * 1000)
    params = {
        "callback": "jQuery112406118340540763985_" + str(now),
        "_": now,
    }
    data = parse_jsonp(
        http_get(rad_user_info_api, params=params, bind_ip=bind_ip)
    )
    return profile.parse_online_status(data, expected_username)
```

依赖方向清晰：`srun_auth.py` -> `SchoolProfile`（数据变换）+ `network.py`（HTTP）。Profile 本身零 I/O。

---

## 5. 数据流

### 5.1 核心数据载体

系统中有三个核心数据载体，拆分后它们的读写权归属如下：

```
config.json --读--> config.py:load_config() --> cfg dict --> 所有模块（只读参数）
                                                    |
                                                    +-> schools/:get_profile(cfg["school"])
                                                    |       -> profile 实例（给 srun_auth 用）
                                                    |
state.json  <-写--  daemon.py:save_runtime_status()
            --读--> daemon.py:load_runtime_state()
            --读--> LuCI controller（只读展示）

action.json <-写--  LuCI controller:action_enqueue()
            --读--> daemon.py:pop_runtime_action()
```

关键规则：`cfg` dict 是被传递的参数，不是全局状态。每个模块通过函数参数接收它，不自己去 load。

### 5.2 关键解耦点

**解耦点 1：`run_once()` 不再管 WiFi 准备**

```
当前：
  run_once(cfg)
    -> apply_default_selection_for_runtime()   <- 配置副作用
    -> in_quiet_window()                       <- 业务策略
    -> prepare_campus_for_login()              <- WiFi 操作
    -> challenge -> crypto -> login API

拆分后：
  orchestrator.py 负责前置条件：
    -> apply_default_selection_for_runtime()
    -> prepare_campus_for_login()
  srun_auth.py:run_once(profile, cfg) 只做：
    -> challenge -> crypto -> login API
    （传进来时 WiFi 已经就绪，cfg 已经确定）
```

**解耦点 2：`run_manual_login()` 拆成编排步骤**

```
当前（一个函数做 6 件事）：
  begin_service_guard -> query_online -> clean_slate
  -> disable_sta -> login -> terminal_check -> restore_guard

拆分后 orchestrator.py：
  步骤1: begin_service_guard()              <- config.py
  步骤2: apply_default_selection()           <- config.py
  步骤3: clean_slate_for_manual_login()      <- 自己编排（调 wireless + srun_auth）
  步骤4: srun_auth.run_once(profile, cfg)    <- srun_auth.py
  步骤5: wait_for_manual_login_ready()       <- 自己编排（调 wireless + srun_auth）
  步骤6: restore_service_guard()             <- config.py
```

**解耦点 3：`_daemon_tick_active()` 拆成组合调用**

```
当前（在线检测 + 断线判断 + 重连 + failover 全部内联）

拆分后 daemon.py：
  -> wireless.ensure_expected_profile()      检查 SSID
  -> srun_auth.query_online_identity()       检查在线（通过 profile）
  -> orchestrator.run_once_with_retry()      断线重连
  每一步都是独立模块的独立函数
```

---

## 6. LuCI 联动

### 6.1 新增 CLI 入口

`client.py --list-schools` 输出 JSON 到 stdout：

```json
[
  {
    "short_name": "jxnu",
    "name": "江西师范大学",
    "description": "深澜 SRun 4000 系列认证（瑶湖/青山湖校区）",
    "operators": [
      {"id": "cucc", "label": "中国联通", "verified": true},
      {"id": "xn",   "label": "校园网",   "verified": true},
      {"id": "cmcc", "label": "中国移动", "verified": false},
      {"id": "ctcc", "label": "中国电信", "verified": false}
    ],
    "contributors": ["@zengjiaxuan"]
  }
]
```

### 6.2 Controller 新增端点

```lua
-- controller/jxnu_srun.lua
function index()
    -- ...existing entries...
    entry({"admin", "services", "jxnu_srun", "schools"},
        call("action_schools")).leaf = true
end

function action_schools()
    local output = sys.exec(
        "python3 -B /usr/lib/jxnu_srun/client.py --list-schools 2>/dev/null"
    )
    http.prepare_content("application/json")
    http.write(output or "[]")
end
```

### 6.3 CBI 页面交互

```
+-------------------------------------+
| 学校    [v 江西师范大学           ]  |
|                                     |
| [checkmark] 深澜 SRun 4000 系列认证|
|   贡献者: @zengjiaxuan              |
|                                     |
| 运营商  [v 中国联通               ]  |
|         [checkmark] 已验证          |
|                                     |
| 学工号  [____________________]      |
| 密码    [____________________]      |
+-------------------------------------+
```

行为：

- 选学校 -> 运营商下拉框选项动态更新
- 选运营商 -> 下方显示 "已验证" 或 "社区贡献，未验证"
- 学校的 `DEFAULT_BASE_URL` / `DEFAULT_AC_ID` 自动填入对应字段（用户可改）

### 6.4 配置变更

`config.json` 新增字段：

```json
{
  "school": "jxnu"
}
```

`config.py` 的 `load_config()` 读取 `school` 字段。`srun_auth.py` 据此通过 `schools.get_profile()` 加载对应 profile。未设置或找不到时 fallback 到 `_base.py` 默认实现（向后兼容）。

---

## 7. 函数归属映射

| 函数 | 当前行号 | 目标模块 |
|---|---|---|
| `_load_defaults()`, `load_json_raw_config()`, `save_json_raw_config()` | 108-212 | `config.py` |
| `load_config()`, `_migrate_legacy_config()`, `_is_legacy_config()` | 519-805 | `config.py` |
| `get_json_scalar_config()`, `set_json_scalar_config()` | 214-225 | `config.py` |
| `resolve_active_items()`, `get_active_campus_account()`, `get_active_hotspot_profile()` | 637-713 | `config.py` |
| `apply_default_selection_for_runtime()`, `_pointer_meta()` | 277-322 | `config.py` |
| `begin/restore/reconcile_manual_login_service_guard()` | 232-275 | `config.py` |
| `append_log()`, `normalize_hhmm()`, `parse_non_negative_*()` | 831-860, 1253-1269 | `config.py` |
| `quiet_hours_enabled()`, `in_quiet_window()`, `failover_enabled()`, `backoff_enabled()` | 846-881 | `config.py` |
| `load_json_file()`, `save_json_file()`, `ensure_parent_dir()` | 325-344, 169-172 | `config.py` |
| `localize_error()` | 808-828 | `config.py` |
| `http_get()` (with fallback chain) | 1101-1155 | `network.py` |
| `parse_jsonp()` | 1158-1161 | `network.py` |
| `run_cmd()`, `parse_uci_value()` | 1272-1286 | `network.py` |
| `pick_valid_ip()`, `extract_ip_from_text()`, `get_local_ip_for_target()` | 1164-1205 | `network.py` |
| `get_ipv4_from_network_interface()`, `wait_for_network_interface_ipv4()` | 1208-1250 | `network.py` |
| `_url_encode_component()`, `_urlencode()`, `extract_host_from_url()` | 1019-1041 | `network.py` |
| `humanize_http_errors()`, `compact_http_error_detail()` | 1044-1079 | `network.py` |
| `resolve_bind_ip()` | 1082-1098 | `network.py` |
| `test_internet_connectivity()`, `_test_portal_reachability()` | 2146-2170 | `network.py` |
| `parse_wireless_iface_data()` | 1289-1313 | `wireless.py` |
| `get_sta_sections()`, `get_sta_section()`, `get_enabled_sta_sections()` | 1331-1355 | `wireless.py` |
| `get_active_sta_section()`, `get_runtime_sta_section()` | 1358-1402 | `wireless.py` |
| `detect_runtime_mode()` | 1405-1418 | `wireless.py` |
| `get_network_interface_from_sta_section()`, `get_sta_profile_from_section()` | 1421-1440 | `wireless.py` |
| `parse_radio_bands()`, `get_available_wifi_radios()`, `band_label()` | 1443-1491 | `wireless.py` |
| `find_sta_on_radio()`, `get_managed_sta_sections()` | 1499-1548+ | `wireless.py` |
| `commit_reload_wireless()` | 1789 | `wireless.py` |
| `build_expected_profile()`, `switch_sta_profile()`, `switch_to_campus()` | 1856-2145 | `wireless.py` |
| `ensure_expected_profile()`, `disable_managed_sta_sections()` | 2172-2439 | `wireless.py` |
| `normalize_wifi_encryption()`, `wifi_key_required()`, `split_network_value()` | 1316-1328 | `wireless.py` |
| `get_base64()`, `get_sha1()`, `get_md5()` | 2244, 2233, 2229 | `crypto.py` |
| `get_xencode()`, `get_info()`, `get_chksum()` | 2323, 2359, 2370 | `crypto.py` |
| `s()` helper | 2237 | `crypto.py` |
| `init_getip()`, `get_token()` | 2643, 2650 | `srun_auth.py` |
| `login()`, `logout()` | 2723, 2751 | `srun_auth.py` (通过 profile 代理) |
| `query_online_status()`, `query_online_identity()` | 2675, 2682 | `srun_auth.py` (通过 profile 代理) |
| `run_once()` | 2778 | `srun_auth.py` (签名变为 `run_once(profile, cfg)`) |
| `do_complex_work()` | 2704 | 移入 `SchoolProfile.do_complex_work()` |
| `build_urls()` | 2381 | 移入 `SchoolProfile.build_urls()` |
| `get_logout_username()`, `get_logout_sign()` | 2391-2401 | 移入 `SchoolProfile` 内部方法 |
| `wait_for_logout_status()` | 2404 | `srun_auth.py` |
| `run_once_safe()`, `run_once_with_retry()`, `run_once_manual()` | 930-998 | `orchestrator.py` |
| `run_manual_login()`, `run_manual_logout()` | 2886, 2824 | `orchestrator.py` |
| `prepare_campus_for_login()`, `clean_slate_for_manual_login()` | 2636, 2540 | `orchestrator.py` |
| `wait_for_manual_login_ready()`, `wait_for_manual_logout_ready()` | 2442, 2497 | `orchestrator.py` |
| `get_manual_terminal_check_*()` | 2523-2537 | `orchestrator.py` |
| `run_status()`, `run_quiet_logout()` | 2948, 2969 | `orchestrator.py` |
| `connectivity_mode_matches()`, `calc_backoff_delay_seconds()` | 902-927 | `orchestrator.py` |
| `run_daemon()`, `_make_daemon_state()` | 3254, 3099 | `daemon.py` |
| `_daemon_tick_quiet()`, `_daemon_tick_active()` | 3120, 3167 | `daemon.py` |
| `handle_runtime_action()`, `run_switch()` | 3012, 2993 | `daemon.py` |
| `build_runtime_snapshot()` | 367 | `daemon.py` |
| `load_runtime_state()`, `save_runtime_state()`, `save_runtime_status()` | 347-364 | `daemon.py` |
| `queue_runtime_action()`, `pop_runtime_action()` | 486-500 | `daemon.py` |
| `main()` | 3308 | `daemon.py` |

---

## 8. 迁移策略

### 8.1 铁律：零破坏性升级

**入口不变：**

```
init 脚本调用: python3 -B /usr/lib/jxnu_srun/client.py --daemon
                                    |
              client.py 变成薄壳:    |
              +---------------------+
              |  from daemon import main
              |  main()
              +- ~30行，仅 argparse + 分发
```

**配置向前兼容：**

| 场景 | 行为 |
|---|---|
| 旧 config.json 无 `school` 字段 | 默认加载 jxnu profile，行为与当前完全一致 |
| 旧 config.json 仍是扁平格式 | `_migrate_legacy_config()` 逻辑保留在 config.py，照常迁移 |
| 新用户首次安装 | LuCI 引导选择学校，写入 school 字段 |

### 8.2 分阶段实施

```
Phase 1: 抽出纯函数模块（零风险）
         crypto.py <- 纯函数搬运，原地可测
         network.py <- http_get / run_cmd / IP 工具搬运

Phase 2: 抽出基础设施模块
         config.py <- 配置加载/保存/迁移
         wireless.py <- UCI / STA / 切换

Phase 3: 建立 school profile 体系
         schools/_base.py <- 基类，调用 crypto.py
         schools/jxnu.py <- 当前硬编码值提取为 profile
         srun_auth.py <- 认证 API，通过 profile 调度

Phase 4: 解耦编排层
         orchestrator.py <- 手动操作/重试（最复杂的拆分）
         daemon.py <- 守护循环

Phase 5: client.py 瘦身为入口
         验证 CLI 全部模式正常
```

Phase 1-2 可以逐步提交，每步都不破坏功能。Phase 3 是核心架构变更。Phase 4-5 完成解耦。

### 8.3 验证清单

每个 Phase 完成后验证：

- [ ] `python3 -B client.py --daemon` 正常启动
- [ ] `python3 -B client.py --once` 单次登录正常
- [ ] `python3 -B client.py --logout` 登出正常
- [ ] `python3 -B client.py --status` 状态查询正常
- [ ] `python3 -B client.py --switch-hotspot` / `--switch-campus` 切换正常
- [ ] `python3 -B client.py --list-schools` 输出学校列表（Phase 3+）
- [ ] LuCI 页面正常加载，配置保存正常
- [ ] 旧版 config.json 自动迁移正常
- [ ] 无 `school` 字段时默认行为不变

---

## 9. 贡献者指南（面向其他学校开发者）

### 最小适配（90% 的学校）

1. Fork 本仓库
2. 创建 `root/usr/lib/jxnu_srun/schools/yourschool.py`
3. 填写以下模板：

```python
from ._base import SchoolProfile


class Profile(SchoolProfile):
    NAME = "你的学校名称"
    SHORT_NAME = "yourschool"        # 英文缩写，唯一标识
    DESCRIPTION = "简短描述认证系统"
    CONTRIBUTORS = ["@your_github"]

    ALPHA = "你的学校的Base64字母表"   # 64个字符
    DEFAULT_BASE_URL = "http://x.x.x.x"
    DEFAULT_AC_ID = "1"

    OPERATORS = [
        {"id": "cmcc", "label": "中国移动", "verified": False},
        {"id": "ctcc", "label": "中国电信", "verified": False},
        {"id": "cucc", "label": "中国联通", "verified": False},
        # 按需添加/删除运营商
    ]
    NO_SUFFIX_OPERATORS = []  # 哪些运营商的用户名不加 @后缀
```

4. 在你的路由器上测试
5. 测试通过后，将运营商的 `verified` 改为 `True`
6. 提交 PR

### 进阶适配（加密算法有差异）

如果你的学校使用了不同的加密方式，覆盖对应方法：

```python
class Profile(SchoolProfile):
    # ...上面的基础字段...

    def get_xencode(self, msg, key):
        """覆盖 BX1 编码"""
        # 你的学校的异或编码实现
        ...

    def get_info(self, username, password, ip, ac_id, enc):
        """覆盖 info 字段构建"""
        # 你的学校的 info 构建逻辑
        ...
```

### 深度适配（API 格式完全不同）

极少数情况下，你可能需要覆盖请求构建和响应解析：

```python
class Profile(SchoolProfile):
    # ...基础字段...

    API_PORTAL = "/custom/login/path"  # 自定义 API 路径

    def build_login_params(self, cfg, ip, i_value, hmd5, chksum):
        """完全自定义登录请求参数"""
        ...

    def parse_login_response(self, data):
        """完全自定义响应解析"""
        ...
```

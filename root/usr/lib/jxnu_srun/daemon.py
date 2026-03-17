"""
守护循环 -- 守护进程主循环、runtime action 分发、状态管理。

依赖 orchestrator（编排）、wireless（WiFi）、srun_auth（认证）、config（配置）。
"""

import ipaddress
import json
import time

from config import (
    CONNECTIVITY_CACHE_SECONDS,
    append_log,
    campus_uses_wired,
    failover_enabled,
    in_quiet_window,
    load_config,
    pop_runtime_action,
    save_runtime_status,
    load_runtime_state,
    reconcile_manual_login_service_guard,
)
from network import (
    HTTP_EXCEPTIONS,
    get_ipv4_from_network_interface,
    test_internet_connectivity,
    _test_portal_reachability,
)
from wireless import (
    build_expected_profile,
    detect_runtime_mode,
    ensure_expected_profile,
    get_network_interface_from_sta_section,
    get_runtime_sta_section,
    get_sta_profile_from_section,
    parse_wireless_iface_data,
    switch_to_campus,
    wifi_key_required,
)
import orchestrator
import srun_auth


# ---------------------------------------------------------------------------
# Runtime snapshot
# ---------------------------------------------------------------------------

def build_runtime_snapshot(cfg, state=None):
    data = parse_wireless_iface_data()
    section = get_runtime_sta_section(cfg, data)
    profile = get_sta_profile_from_section(section, data) if section else {}
    ssid = str(profile.get("ssid", "")).strip()
    bssid = str(profile.get("bssid", "")).strip().lower()
    net = get_network_interface_from_sta_section(section, data) if section else None
    ip = get_ipv4_from_network_interface(net) if net else None
    previous = load_runtime_state()
    wired_mode = campus_uses_wired(cfg)
    wan_ip = get_ipv4_from_network_interface("wan") if wired_mode else None
    wired_online = False

    if wired_mode and wan_ip:
        ssid = "有线接入"
        bssid = ""
        net = "wan"
        ip = wan_ip

    connectivity = "未连接"
    connectivity_level = "offline"
    online_account_label = ""
    if ip:
        now_ts = int(time.time())
        cache_ip = str(previous.get("current_ip", "")).strip()
        cache_level = str(previous.get("connectivity_level", "")).strip()
        cache_text = str(previous.get("connectivity", "")).strip()
        cache_ts = int(previous.get("connectivity_checked_at", 0) or 0)
        cache_valid = (
            cache_ip == ip
            and cache_level
            and cache_text
            and (now_ts - cache_ts) <= CONNECTIVITY_CACHE_SECONDS
        )
        if cache_valid:
            connectivity = cache_text
            connectivity_level = cache_level
        else:
            internet_ok, internet_msg = test_internet_connectivity(timeout=2)
            if internet_ok:
                connectivity = "互联网可达"
                connectivity_level = "online"
            else:
                portal_ok, portal_msg = _test_portal_reachability(cfg, timeout=2)
                if portal_ok:
                    connectivity = "认证网关可达"
                    connectivity_level = "portal"
                else:
                    detail = internet_msg or portal_msg or "连通性未知"
                    connectivity = "已连接但受限: %s" % detail
                    connectivity_level = "limited"
            previous["connectivity_checked_at"] = now_ts
    else:
        previous["connectivity_checked_at"] = int(time.time())

    srun_profile = srun_auth._get_profile(cfg)

    if cfg.get("username") and wired_mode and wan_ip:
        try:
            urls = srun_auth.build_urls(cfg)
            online_now, online_user, _ = srun_auth.query_online_identity(
                srun_profile, urls["rad_user_info_api"], cfg["username"], bind_ip=wan_ip
            )
            if online_now and online_user:
                wired_online = True
                online_account_label = online_user
        except Exception:
            wired_online = False

    if wired_online:
        mode = "campus"
    elif ssid == str(cfg.get("hotspot_ssid", "")).strip() and ssid:
        mode = "hotspot"
    elif ssid == str(cfg.get("campus_ssid", "")).strip() and ssid:
        mode = "campus"
    else:
        mode = "unknown"

    if mode != "hotspot" and cfg.get("username") and not wired_online:
        try:
            urls = srun_auth.build_urls(cfg)
            online_now, online_user, _ = srun_auth.query_online_identity(
                srun_profile, urls["rad_user_info_api"], cfg["username"]
            )
            if online_now and online_user:
                online_account_label = online_user
        except Exception:
            online_account_label = ""

    if mode == "campus":
        mode_label = "校园网模式（有线）" if wired_mode else "校园网模式"
    elif mode == "hotspot":
        mode_label = "热点模式"
    else:
        mode_label = "未知模式"

    current_campus_access_mode = ""
    if mode == "campus":
        current_campus_access_mode = "wired" if wired_mode and net == "wan" else "wifi"

    return {
        "current_mode": mode,
        "mode": mode,
        "mode_label": mode_label,
        "current_ssid": ssid,
        "current_bssid": bssid,
        "current_iface": str(net or ""),
        "current_ip": str(ip or ""),
        "connectivity": connectivity,
        "connectivity_level": connectivity_level,
        "connectivity_checked_at": int(previous.get("connectivity_checked_at", 0) or 0),
        "campus_account_label": str(cfg.get("campus_account_label", "")),
        "campus_access_mode": str(cfg.get("campus_access_mode", "wifi")),
        "current_campus_access_mode": current_campus_access_mode,
        "online_account_label": online_account_label,
        "hotspot_profile_label": str(cfg.get("hotspot_profile_label", "")),
        "campus_ssid": str(cfg.get("campus_ssid", "")),
        "campus_bssid": str(cfg.get("campus_bssid", "")).strip().lower(),
    }


# ---------------------------------------------------------------------------
# Daemon state
# ---------------------------------------------------------------------------

def _make_daemon_state():
    return {
        "was_in_quiet": False,
        "quiet_logout_done": False,
        "current_mode": "campus",
        "was_online": False,
        "last_switch_ts": 0,
    }


def _safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTP_EXCEPTIONS as exc:
        from config import localize_error
        return False, "网络错误: " + localize_error(exc)
    except ValueError as exc:
        from config import localize_error
        return False, "响应解析错误: " + localize_error(exc)
    except Exception as exc:
        from config import localize_error
        return False, "错误: " + localize_error(exc)


# ---------------------------------------------------------------------------
# Switch
# ---------------------------------------------------------------------------

def run_switch(cfg, expect_hotspot):
    from wireless import switch_sta_profile
    target = build_expected_profile(cfg, expect_hotspot)
    if (not expect_hotspot) and campus_uses_wired(cfg):
        switched, message = switch_to_campus(cfg)
        if switched:
            return True, "切换成功: " + (message or "")
        return False, "切换失败: " + (message or "未知错误")

    if not target["ssid"]:
        return False, "%s SSID 未配置" % target["label"]
    if wifi_key_required(target["encryption"]) and not target["key"]:
        return False, "%s 配置缺少密码" % target["label"]

    switched, message = switch_sta_profile(cfg, expect_hotspot)
    if switched:
        return True, "切换成功: " + (message or "")
    return False, "切换失败: " + (message or "未知错误")


# ---------------------------------------------------------------------------
# Handle runtime actions (from LuCI)
# ---------------------------------------------------------------------------

def handle_runtime_action(cfg, state):
    payload = pop_runtime_action()
    action = str(payload.get("action", "")).strip()
    if not action:
        return False, ""

    action_started_at = int(time.time())
    save_runtime_status(
        "正在执行动作: %s" % action,
        state,
        last_action=action,
        last_action_ts=action_started_at,
        action_result="pending",
        pending_action=action,
        action_started_at=action_started_at,
        **build_runtime_snapshot(cfg, state),
    )

    action_map = {
        "switch_hotspot": True,
        "switch_campus": False,
    }

    if action == "manual_login":
        ok, message = orchestrator.run_manual_login(cfg)
        append_log("[JXNU-SRun] 异步动作 %s: %s" % (action, message))
        save_runtime_status(
            message,
            state,
            last_action=action,
            last_action_ts=int(time.time()),
            action_result="ok" if ok else "error",
            action_started_at=0,
            pending_action="",
            **build_runtime_snapshot(cfg, state),
        )
        return True, message

    if action == "manual_logout":
        ok, message = orchestrator.run_manual_logout(cfg)
        append_log("[JXNU-SRun] 异步动作 %s: %s" % (action, message))
        save_runtime_status(
            message,
            state,
            last_action=action,
            last_action_ts=int(time.time()),
            action_result="ok" if ok else "error",
            action_started_at=0,
            pending_action="",
            **build_runtime_snapshot(cfg, state),
        )
        return True, message

    if action not in action_map:
        message = "忽略未知动作: %s" % action
        append_log("[JXNU-SRun] %s" % message)
        save_runtime_status(
            message,
            state,
            last_action=action,
            last_action_ts=int(time.time()),
            action_result="ignored",
            action_started_at=0,
            **build_runtime_snapshot(cfg, state),
        )
        return True, message

    ok, message = run_switch(cfg, expect_hotspot=action_map[action])
    action_result = "ok" if ok else "error"
    target_mode = "hotspot" if action_map[action] else "campus"
    if ok:
        state["current_mode"] = target_mode
        if not action_map[action]:
            state["last_switch_ts"] = 0
    append_log("[JXNU-SRun] 异步动作 %s: %s" % (action, message))
    save_runtime_status(
        message,
        state,
        last_action=action,
        last_action_ts=int(time.time()),
        action_result=action_result,
        action_started_at=0,
        pending_action="",
        **build_runtime_snapshot(cfg, state),
    )
    return True, message


# ---------------------------------------------------------------------------
# Daemon tick
# ---------------------------------------------------------------------------

def _daemon_tick_quiet(cfg, state, interval):
    mode_msg = ""
    runtime_mode = detect_runtime_mode(cfg)

    if not state["was_in_quiet"]:
        state["quiet_logout_done"] = False

    if state["quiet_logout_done"]:
        conn_state = orchestrator.quiet_connection_state(cfg)
        message = "夜间停用（%s）" % conn_state
    else:
        if runtime_mode == "hotspot":
            state["quiet_logout_done"] = True
            message = "夜间停用（热点已连接）"
        else:
            ok, message = _safe_call(orchestrator.run_quiet_logout, cfg)
            state["quiet_logout_done"] = ok

    if failover_enabled(cfg):
        ssid_ok, ssid_msg, state["last_switch_ts"] = ensure_expected_profile(
            cfg,
            expect_hotspot=True,
            last_switch_ts=state["last_switch_ts"],
        )
        if ssid_ok:
            state["current_mode"] = "hotspot"
        if ssid_msg:
            mode_msg = ssid_msg
        if not ssid_ok:
            state["was_in_quiet"] = True
            state["was_online"] = False
            state["current_mode"] = "hotspot"
            wait_message = "夜间停用（未连接）"
            if message:
                wait_message = wait_message + "；" + message
            if mode_msg:
                wait_message = wait_message + "；" + mode_msg
            return wait_message, min(interval, 60)

    if mode_msg:
        message = message + "；" + mode_msg

    state["was_in_quiet"] = True
    state["was_online"] = False
    return message, min(interval, 60)


def _daemon_tick_active(cfg, state, interval):
    from config import localize_error
    online_interval = interval
    mode_msg = ""

    if state["was_in_quiet"]:
        append_log("[JXNU-SRun] 退出夜间时段，准备切回校园网配置")
        state["quiet_logout_done"] = False
        state["was_in_quiet"] = False
        state["was_online"] = False
        state["last_switch_ts"] = 0
        if failover_enabled(cfg):
            switched, sw_msg = switch_to_campus(cfg)
            state["current_mode"] = "campus" if switched else "hotspot"
            if sw_msg:
                mode_msg = sw_msg

    if failover_enabled(cfg):
        ready_ok, ready_msg, state["last_switch_ts"] = ensure_expected_profile(
            cfg,
            expect_hotspot=False,
            last_switch_ts=state["last_switch_ts"],
        )
        if ready_ok:
            state["current_mode"] = "campus"
            if ready_msg:
                mode_msg = (mode_msg + "；" if mode_msg else "") + ready_msg
        else:
            state["current_mode"] = "hotspot"
            state["was_online"] = False
            message = "校园网配置未就绪"
            if ready_msg:
                message = message + "；" + ready_msg
            return message, min(interval, 30)

    if failover_enabled(cfg) and state["current_mode"] == "hotspot":
        state["was_online"] = False
        message = "已切换到热点SSID，校园网SSID恢复后将自动切回"
        if mode_msg:
            message = message + "；" + mode_msg
        return message, interval

    srun_profile = srun_auth._get_profile(cfg)
    next_sleep = interval
    try:
        urls = srun_auth.build_urls(cfg)
        online_now = False
        status_message = ""
        if cfg["username"]:
            online_now, status_message = srun_auth.query_online_status(
                srun_profile, urls["rad_user_info_api"], cfg["username"]
            )

        if online_now:
            message = "在线，下一次检测间隔 %d 秒" % online_interval
            state["was_online"] = True
            next_sleep = online_interval
        else:
            if state["was_online"]:
                append_log("[JXNU-SRun] 检测到断线，立即开始重连")
            state["was_online"] = False
            ok, message = orchestrator.run_once_with_retry(cfg)
            state["was_online"] = bool(ok)
            if not ok and status_message:
                message = "%s；状态检测结果: %s" % (message, status_message)
    except HTTP_EXCEPTIONS as exc:
        append_log("[JXNU-SRun] 状态检测网络异常，尝试重连")
        state["was_online"] = False
        ok, message = orchestrator.run_once_with_retry(cfg)
        if not ok:
            message = "网络异常: %s；重连结果: %s" % (localize_error(exc), message)
    except ValueError as exc:
        append_log("[JXNU-SRun] 状态检测解析异常，尝试重连")
        state["was_online"] = False
        ok, message = orchestrator.run_once_with_retry(cfg)
        if not ok:
            message = "解析异常: %s；重连结果: %s" % (localize_error(exc), message)
    except Exception as exc:
        append_log("[JXNU-SRun] 状态检测异常，尝试重连")
        state["was_online"] = False
        ok, message = orchestrator.run_once_with_retry(cfg)
        if not ok:
            message = "异常: %s；重连结果: %s" % (localize_error(exc), message)

    if mode_msg:
        message = message + "；" + mode_msg
    return message, next_sleep


# ---------------------------------------------------------------------------
# Daemon main loop
# ---------------------------------------------------------------------------

def run_daemon():
    reconcile_manual_login_service_guard()
    state = _make_daemon_state()
    save_runtime_status(
        "守护进程已启动",
        state,
        daemon_running=True,
        enabled=True,
        last_action="",
        last_action_ts=0,
        action_result="",
        action_started_at=0,
        pending_action="",
        **build_runtime_snapshot(load_config(), state),
    )

    while True:
        cfg = load_config()
        interval = max(int(cfg["interval"]), 5)

        action_handled, action_message = handle_runtime_action(cfg, state)
        if action_handled:
            time.sleep(1)
            continue

        if cfg["enabled"] != "1":
            state.update(_make_daemon_state())
            save_runtime_status(
                "自动登录服务未启用",
                state,
                daemon_running=True,
                enabled=False,
                **build_runtime_snapshot(cfg, state),
            )
            time.sleep(interval)
            continue

        if in_quiet_window(cfg):
            message, sleep = _daemon_tick_quiet(cfg, state, interval)
        else:
            message, sleep = _daemon_tick_active(cfg, state, interval)

        append_log(("[JXNU-SRun] " + message).strip())
        save_runtime_status(
            message,
            state,
            daemon_running=True,
            enabled=True,
            in_quiet=in_quiet_window(cfg),
            **build_runtime_snapshot(cfg, state),
        )
        time.sleep(sleep)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="JXNU SRun client for OpenWrt")
    parser.add_argument("--daemon", action="store_true", help="run as daemon loop")
    parser.add_argument("--once", action="store_true", help="run login once")
    parser.add_argument("--logout", action="store_true", help="logout current account")
    parser.add_argument("--relogin", action="store_true", help="logout then login once")
    parser.add_argument("--status", action="store_true", help="query online status")
    parser.add_argument(
        "--switch-hotspot", action="store_true", help="switch STA profile to hotspot"
    )
    parser.add_argument(
        "--switch-campus", action="store_true", help="switch STA profile to campus"
    )
    parser.add_argument(
        "--list-schools", action="store_true", help="list available school profiles"
    )
    args = parser.parse_args()

    if args.list_schools:
        import json as _json
        import schools
        print(_json.dumps(schools.list_schools(), ensure_ascii=False, indent=2))
        return

    cfg = load_config()

    if args.daemon:
        run_daemon()
        return

    if args.switch_hotspot and args.switch_campus:
        print("参数错误：不能同时指定 --switch-hotspot 和 --switch-campus")
        return

    selected = sum(
        1
        for flag in [
            args.once,
            args.logout,
            args.relogin,
            args.status,
            args.switch_hotspot,
            args.switch_campus,
        ]
        if flag
    )
    if selected > 1:
        print("参数错误：一次只能执行一种操作")
        return

    if args.switch_hotspot:
        _, message = run_switch(cfg, expect_hotspot=True)
        append_log("[JXNU-SRun] 手动切换热点结果: " + message)
        print(message)
        return

    if args.switch_campus:
        _, message = run_switch(cfg, expect_hotspot=False)
        append_log("[JXNU-SRun] 手动切换校园网结果: " + message)
        print(message)
        return

    if args.logout:
        ok, message = orchestrator.run_manual_logout(cfg)
        append_log("[JXNU-SRun] 手动登出结果: " + message)
        print(message)
        return

    if args.relogin:
        ok, message = orchestrator.run_manual_login(cfg)
        append_log("[JXNU-SRun] 手动重新登录结果: " + message)
        print(message)
        return

    if args.status:
        ok, message = orchestrator.run_status(cfg)
        print(message)
        return

    if args.once:
        from config import (
            apply_default_selection_for_runtime,
            in_quiet_window,
            quiet_window_label,
        )
        cfg, _, _ = apply_default_selection_for_runtime(False, "登录前")
        if in_quiet_window(cfg):
            print("夜间停用中（北京时间 %s），不执行登录" % quiet_window_label(cfg))
            return
        if not cfg["username"] or not cfg["password"]:
            print("请先在 LuCI 页面填写学工号和密码")
            return
        ok_prep, msg_prep = orchestrator.prepare_campus_for_login(cfg)
        if not ok_prep:
            print(msg_prep)
            return
        ok, message = srun_auth.run_once(cfg)
        append_log("[JXNU-SRun] 单次登录结果: " + message)
        print(message)
        return

    # 无参数：显示帮助
    parser.print_help()

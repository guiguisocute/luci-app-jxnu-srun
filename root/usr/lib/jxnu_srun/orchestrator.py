"""
操作编排 -- 手动登录/登出全流程、重试策略、WiFi 前置准备。

依赖 srun_auth（认证）、wireless（WiFi切换）、config（配置/策略）。
"""

import math
import time

from config import (
    append_log,
    apply_default_selection_for_runtime,
    backoff_enabled,
    begin_manual_login_service_guard,
    campus_uses_wired,
    get_manual_terminal_check_attempts,
    get_manual_terminal_check_interval_seconds,
    get_manual_terminal_check_label,
    get_retry_cooldown_seconds,
    get_retry_max_cooldown_seconds,
    get_switch_ready_timeout_seconds,
    in_quiet_window,
    load_config,
    localize_error,
    quiet_window_label,
    restore_manual_login_service_guard,
)
from network import (
    HTTP_EXCEPTIONS,
    resolve_bind_ip,
    test_internet_connectivity,
)
from wireless import (
    build_expected_profile,
    detect_runtime_mode,
    disable_managed_sta_sections,
    ensure_expected_profile,
    get_active_sta_section,
    get_preferred_profile_radio,
    get_radio_for_section,
    get_sta_profile_from_section,
    parse_wireless_iface_data,
    profiles_match,
    switch_to_campus,
    wait_for_network_interface_ipv4,
)
import srun_auth
from snapshot import build_runtime_snapshot


# ---------------------------------------------------------------------------
# 退避计算
# ---------------------------------------------------------------------------

def connectivity_mode_matches(snapshot, cfg, require_ssid=False):
    mode = str(cfg.get("connectivity_check_mode", "internet")).strip().lower()
    current_ssid = str(snapshot.get("current_ssid", "")).strip()
    target_ssid = str(cfg.get("campus_ssid", "")).strip()
    if campus_uses_wired(cfg):
        require_ssid = False
    ssid_ok = (not require_ssid) or (current_ssid and current_ssid == target_ssid)
    if not ssid_ok:
        return False

    level = str(snapshot.get("connectivity_level", "offline")).strip().lower()
    if mode == "ssid":
        return bool(ssid_ok)
    if mode == "portal":
        return level in ("online", "portal")
    return level == "online"


def calc_backoff_delay_seconds(cfg, failure_index):
    n_val = max(int(failure_index), 1)
    base = get_retry_cooldown_seconds(cfg)
    max_duration = get_retry_max_cooldown_seconds(cfg)
    delay = base * math.pow(2, max(n_val - 1, 0))
    if max_duration > 0:
        delay = min(delay, max_duration)
    return delay


# ---------------------------------------------------------------------------
# 重试包装
# ---------------------------------------------------------------------------

def run_once_with_retry(cfg, ignore_service_disabled=False):
    ok, message = srun_auth.run_once_safe(cfg)
    if ok:
        return True, message

    append_log("[JXNU-SRun] 首次登录失败: %s" % message)

    if not backoff_enabled(cfg):
        append_log(
            "[JXNU-SRun] 已关闭退避重试，%d 秒后执行一次重试"
            % int(get_retry_cooldown_seconds(cfg))
        )
        time.sleep(get_retry_cooldown_seconds(cfg))
        retry_ok, retry_message = srun_auth.run_once_safe(cfg)
        if retry_ok:
            append_log("[JXNU-SRun] 单次重试成功")
            return True, "重试成功"
        append_log("[JXNU-SRun] 单次重试失败: %s" % retry_message)
        return False, retry_message

    retries = 0
    failures = 1

    while True:
        runtime_cfg = load_config()
        max_retries = int(runtime_cfg.get("backoff_max_retries", 0))

        if runtime_cfg.get("enabled") != "1" and not ignore_service_disabled:
            return False, "服务已禁用，停止重试"
        if not backoff_enabled(runtime_cfg):
            return False, message
        if in_quiet_window(runtime_cfg):
            return False, "进入夜间停用时段，停止重试"
        if max_retries > 0 and retries >= max_retries:
            return False, message

        delay = calc_backoff_delay_seconds(runtime_cfg, failures)
        append_log("[JXNU-SRun] 第 %d 次重试将在 %.1f 秒后执行" % (retries + 1, delay))
        if delay > 0:
            time.sleep(delay)

        retry_ok, retry_message = srun_auth.run_once_safe(runtime_cfg)
        retries += 1
        if retry_ok:
            append_log("[JXNU-SRun] 第 %d 次重试成功" % retries)
            return True, "重试成功（第 %d 次）" % retries

        append_log("[JXNU-SRun] 第 %d 次重试失败: %s" % (retries, retry_message))
        message = retry_message
        failures += 1


def run_once_manual(cfg):
    ok, message = srun_auth.run_once_safe(cfg)
    if ok:
        return True, message
    append_log("[JXNU-SRun] 手动登录阶段失败: %s" % message)
    return False, message


# ---------------------------------------------------------------------------
# 安静时段 / 状态查询
# ---------------------------------------------------------------------------

def quiet_connection_state(cfg, urls=None):
    runtime_mode = detect_runtime_mode(cfg)
    if runtime_mode == "hotspot":
        return "热点已连接"

    if not cfg.get("username"):
        return "未连接"

    if urls is None:
        urls = srun_auth.build_urls(cfg)

    profile = srun_auth.get_profile(cfg)
    try:
        online, _ = srun_auth.query_online_status(
            profile, urls["rad_user_info_api"], cfg["username"]
        )
        return "在线" if online else "未连接"
    except Exception:
        return "未连接"


def run_status(cfg):
    mode_hint = ""
    from config import failover_enabled
    if failover_enabled(cfg):
        mode_hint = "（校园网SSID: %s，热点SSID: %s）" % (
            cfg.get("campus_ssid", "jxnu_stu"),
            cfg.get("hotspot_ssid", "未设置"),
        )

    urls = srun_auth.build_urls(cfg)
    profile = srun_auth.get_profile(cfg)

    if in_quiet_window(cfg):
        state = quiet_connection_state(cfg, urls)
        return False, "夜间停用（%s）" % state + mode_hint

    if not cfg["username"]:
        return False, "未配置学工号" + mode_hint

    online, message = srun_auth.query_online_status(
        profile, urls["rad_user_info_api"], cfg["username"]
    )
    return online, localize_error(message) + mode_hint


def run_quiet_logout(cfg):
    urls = srun_auth.build_urls(cfg)
    profile = srun_auth.get_profile(cfg)

    if cfg.get("force_logout_in_quiet") != "1":
        state = quiet_connection_state(cfg, urls)
        return True, "夜间停用（%s）" % state

    if not cfg["username"]:
        return False, "夜间停用下线失败: 未配置学工号"

    ip = srun_auth.init_getip(urls["init_url"])
    ok, message = srun_auth.logout(profile, urls["rad_user_dm_api"], cfg, ip)
    if ok:
        offline, offline_msg = srun_auth.wait_for_logout_status(
            profile, urls["rad_user_info_api"], cfg
        )
        if offline:
            return True, "夜间停用下线成功"
        return (
            False,
            "夜间停用下线失败: 请求已发送，但当前仍在线（%s）"
            % localize_error(offline_msg),
        )
    return False, "夜间停用下线失败: " + localize_error(message)


# ---------------------------------------------------------------------------
# WiFi 前置准备
# ---------------------------------------------------------------------------

def prepare_campus_for_login(cfg):
    ok, msg, _ = ensure_expected_profile(cfg, expect_hotspot=False, last_switch_ts=0)
    if ok:
        return True, ""
    return False, msg


# ---------------------------------------------------------------------------
# 手动登出
# ---------------------------------------------------------------------------

def run_manual_logout(cfg, override_user_id=None):
    if not cfg["username"]:
        return False, "未配置学工号"

    profile = srun_auth.get_profile(cfg)
    urls = srun_auth.build_urls(cfg)
    bip = resolve_bind_ip(urls["init_url"], cfg)

    try:
        online_now, online_user, _ = srun_auth.query_online_identity(
            profile, urls["rad_user_info_api"], cfg["username"], bind_ip=bip
        )
        logout_user = str(override_user_id or online_user or "").strip()
        if not online_now or not logout_user:
            return True, "已离线"

        logout_cfg = dict(cfg)
        logout_cfg["user_id"] = logout_user
        logout_cfg["username"] = logout_user
        ip = srun_auth.init_getip(urls["init_url"], bind_ip=bip)
        append_log(
            "[JXNU-SRun] 正在执行手动登出：发送注销请求，账号=%s，绑定IP=%s。"
            % (logout_user, ip)
        )
        ok, message = srun_auth.logout(
            profile, urls["rad_user_dm_api"], logout_cfg, ip, bind_ip=bip
        )
        if ok:
            append_log(
                "[JXNU-SRun] 手动登出请求已受理：接口返回结果=%s，开始校验离线状态。"
                % message
            )
            max_attempts = get_manual_terminal_check_attempts(cfg)
            interval_seconds = get_manual_terminal_check_interval_seconds(cfg)
            ready_ok, ready_msg = wait_for_manual_logout_ready(
                profile,
                urls["rad_user_info_api"],
                logout_cfg,
                bind_ip=bip,
                attempts=max_attempts,
                delay_seconds=interval_seconds,
            )
            if ready_ok:
                append_log("[JXNU-SRun] 手动登出成功：%s。" % ready_msg)
                return True, "登出成功"
            append_log(
                "[JXNU-SRun] 手动登出校验失败：达到最大检查次数 %d 次，返回结果=%s。"
                % (max_attempts, ready_msg)
            )
            return False, "登出失败：%s" % ready_msg

        localized = localize_error(message)
        append_log("[JXNU-SRun] 手动登出失败：注销接口返回结果=%s。" % localized)
        try:
            online, online_msg = srun_auth.query_online_status(
                profile, urls["rad_user_info_api"], cfg["username"], bind_ip=bip
            )
            if not online:
                return True, "已离线"
            return False, "登出失败: " + localize_error(online_msg)
        except Exception:
            return False, "登出失败: " + localized
    except Exception as exc:
        return False, "登出失败: " + localize_error(exc)


def wait_for_manual_logout_ready(
    profile, rad_user_info_api, cfg, bind_ip=None, attempts=5, delay_seconds=2
):
    attempts = max(int(attempts), 1)
    last_message = ""
    for idx in range(attempts):
        append_log(
            "[JXNU-SRun] 正在执行手动登出终态校验：第%d次检查连通性。" % (idx + 1)
        )
        online, offline_msg = srun_auth.query_online_status(
            profile, rad_user_info_api, cfg["username"], bind_ip=bind_ip
        )
        if not online:
            internet_ok, internet_msg = test_internet_connectivity(timeout=2)
            if not internet_ok:
                return True, "已确认离线，互联网连通性检查结果=不可达"
            last_message = "离线后互联网仍可达（%s）" % (internet_msg or "可达")
        else:
            last_message = localize_error(offline_msg)

        if idx + 1 < attempts:
            time.sleep(max(int(delay_seconds), 1))

    return False, last_message or "终态校验超时"


# ---------------------------------------------------------------------------
# 手动登录预清理
# ---------------------------------------------------------------------------

def clean_slate_for_manual_login(cfg, online_user=""):
    if campus_uses_wired(cfg):
        if online_user:
            append_log(
                "[JXNU-SRun] 正在执行手动登录预清理：检测到已有在线账号 %s，开始注销。"
                % online_user
            )
            ok, message = run_manual_logout(cfg, override_user_id=online_user)
            if not ok:
                append_log(
                    "[JXNU-SRun] 手动登录预清理失败：注销在线账号失败，返回结果：%s"
                    % message
                )
                return False, message
            append_log("[JXNU-SRun] 手动登录预清理成功：历史在线账号已注销。")

        active_data = parse_wireless_iface_data()
        append_log(
            "[JXNU-SRun] 当前校园网账号使用有线接入模式：开始禁用全部受管 STA 接口，确保后续认证流量走 WAN 口。"
        )
        ok, message = disable_managed_sta_sections(cfg, active_data)
        if not ok:
            append_log(
                "[JXNU-SRun] 手动登录预清理失败：禁用受管 STA 接口失败，返回结果：%s"
                % (message or "未知错误")
            )
            return False, message or "禁用历史 STA 接口失败"

        append_log(
            "[JXNU-SRun] 当前校园网账号使用有线接入模式：跳过无线重建，直接使用 WAN 口继续登录。"
        )
        wan_ip = wait_for_network_interface_ipv4(
            "wan", timeout_seconds=get_switch_ready_timeout_seconds(cfg)
        )
        if not wan_ip:
            return False, "有线校园网模式下，WAN 口尚未获取到 IPv4 地址"
        return True, ""

    active_data = parse_wireless_iface_data()
    active_section = get_active_sta_section(cfg, active_data)
    active_profile = (
        get_sta_profile_from_section(active_section, active_data)
        if active_section
        else {}
    )
    target_profile = build_expected_profile(cfg, expect_hotspot=False)
    target_radio = get_preferred_profile_radio(cfg, False, active_data)
    active_radio = get_radio_for_section(active_section, active_data)

    profile_changed = False
    if not profiles_match(active_profile, target_profile):
        profile_changed = True
    elif target_radio and active_radio and target_radio != active_radio:
        profile_changed = True

    if online_user:
        append_log(
            "[JXNU-SRun] 正在执行手动登录预清理：检测到已有在线账号 %s，开始注销。"
            % online_user
        )
        ok, message = run_manual_logout(cfg, override_user_id=online_user)
        if not ok:
            append_log(
                "[JXNU-SRun] 手动登录预清理失败：注销在线账号失败，返回结果：%s"
                % message
            )
            return False, message
        append_log("[JXNU-SRun] 手动登录预清理成功：历史在线账号已注销。")

    append_log(
        "[JXNU-SRun] 正在执行手动登录预清理：开始禁用全部受管 STA 接口，确保不存在历史连接残留。"
    )
    ok, message = disable_managed_sta_sections(cfg, active_data)
    if not ok:
        append_log(
            "[JXNU-SRun] 手动登录预清理失败：禁用受管 STA 接口失败，返回结果：%s"
            % (message or "未知错误")
        )
        return False, message or "禁用历史 STA 接口失败"

    if online_user or profile_changed:
        append_log(
            "[JXNU-SRun] 手动登录预清理成功：受管 STA 接口已全部禁用，开始重建目标校园网连接。"
        )
        ok2, sw_msg = switch_to_campus(cfg)
        if not ok2:
            append_log(
                "[JXNU-SRun] 手动登录预清理失败：重建校园网连接失败，返回结果：%s"
                % (sw_msg or "未知错误")
            )
            return False, sw_msg or "切换校园网失败"
        append_log("[JXNU-SRun] 手动登录预清理成功：目标校园网无线配置已重建。")

    return True, ""


# ---------------------------------------------------------------------------
# 手动登录终态校验
# ---------------------------------------------------------------------------

def wait_for_manual_login_ready(cfg, attempts=5, delay_seconds=2):
    attempts = max(int(attempts), 1)
    last_message = ""
    ready_label = get_manual_terminal_check_label(cfg)
    wired_mode = campus_uses_wired(cfg)
    profile = srun_auth.get_profile(cfg)
    urls = srun_auth.build_urls(cfg)
    bind_ip = resolve_bind_ip(urls["init_url"], cfg)
    for idx in range(attempts):
        append_log(
            "[JXNU-SRun] 正在执行手动登录终态校验：第%d次检查连通性。" % (idx + 1)
        )
        snapshot = build_runtime_snapshot(cfg)
        ssid_ok = wired_mode or snapshot.get("current_ssid") == cfg.get("campus_ssid")
        bssid_expect = str(cfg.get("campus_bssid", "")).strip().lower()
        current_bssid = str(snapshot.get("current_bssid", "")).strip().lower()
        bssid_ok = wired_mode or (
            (not bssid_expect) or (not current_bssid) or current_bssid == bssid_expect
        )
        online_ok = connectivity_mode_matches(snapshot, cfg, require_ssid=True)
        auth_online = False
        auth_message = ""
        try:
            auth_online, auth_message = srun_auth.query_online_status(
                profile, urls["rad_user_info_api"], cfg["username"], bind_ip=bind_ip
            )
        except Exception as exc:
            auth_online = False
            auth_message = localize_error(exc)

        if wired_mode and auth_online:
            return True, "已切到有线校园网并确认认证在线"
        if ssid_ok and bssid_ok and online_ok:
            if wired_mode:
                return True, "已切到有线校园网并确认%s" % ready_label
            return True, "已关联目标校园网并确认%s" % ready_label
        if (not wired_mode) and ssid_ok and bssid_ok and auth_online:
            return True, "已关联目标校园网并确认认证在线"
        if ssid_ok and online_ok and bssid_expect and not current_bssid:
            return (
                True,
                "已关联目标校园网并确认%s（BSSID 暂未上报，忽略本次终态校验阻塞）"
                % ready_label,
            )
        last_message = "当前 SSID=%s BSSID=%s 连通性=%s" % (
            snapshot.get("current_ssid", "") or "-",
            current_bssid or "-",
            snapshot.get("connectivity", "未知") or "未知",
        )
        if auth_message:
            last_message = last_message + "；认证状态=%s" % auth_message
        if idx + 1 < attempts:
            time.sleep(max(int(delay_seconds), 1))
    return False, last_message


# ---------------------------------------------------------------------------
# 手动登录全流程
# ---------------------------------------------------------------------------

def run_manual_login(cfg):
    service_guard_enabled = False

    try:
        service_guard_enabled, _ = begin_manual_login_service_guard()
        if service_guard_enabled:
            cfg["enabled"] = "0"
            append_log(
                "[JXNU-SRun] 手动登录保护已启用：检测到自动服务原本开启，当前流程执行期间将临时停用守护逻辑。"
            )

        cfg, _, _ = apply_default_selection_for_runtime(False, "手动登录前")
        profile = srun_auth.get_profile(cfg)
        urls = srun_auth.build_urls(cfg)

        try:
            online_now, online_user, _ = srun_auth.query_online_identity(
                profile, urls["rad_user_info_api"], cfg["username"]
            )
        except Exception:
            online_now, online_user = False, ""

        clean_ok, clean_msg = clean_slate_for_manual_login(
            cfg, online_user if online_now else ""
        )
        if not clean_ok:
            return False, clean_msg

        append_log(
            "[JXNU-SRun] 正在执行手动登录：开始提交认证请求，目标账号=%s。"
            % srun_auth.get_logout_username(cfg)
        )
        login_ok, login_msg = run_once_manual(cfg)
        if login_ok:
            append_log(
                "[JXNU-SRun] 手动登录请求已成功：登录阶段返回结果=%s，开始校验目标接入配置与认证/连通性。"
                % login_msg
            )
            max_attempts = get_manual_terminal_check_attempts(cfg)
            interval_seconds = get_manual_terminal_check_interval_seconds(cfg)
            ready_ok, ready_msg = wait_for_manual_login_ready(
                cfg, attempts=max_attempts, delay_seconds=interval_seconds
            )
            if ready_ok:
                append_log("[JXNU-SRun] 手动登录成功：%s。" % ready_msg)
                return True, "登录成功"
            append_log(
                "[JXNU-SRun] 手动登录校验失败：达到最大检查次数 %d 次，返回结果=%s。"
                % (max_attempts, ready_msg)
            )
            return False, "登录后校验失败：%s" % ready_msg

        append_log("[JXNU-SRun] 手动登录失败：登录阶段返回结果=%s。" % login_msg)
        return False, login_msg
    finally:
        if service_guard_enabled:
            restored, restored_enabled = restore_manual_login_service_guard()
            if restored and restored_enabled == "1":
                append_log(
                    "[JXNU-SRun] 手动登录收尾完成：已恢复自动服务开关到执行前状态。"
                )

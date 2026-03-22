"""
SRun 认证 API -- challenge、login、logout、在线查询。

通过 SchoolProfile 完成加密和参数构建，通过 network.py 完成 HTTP 请求。
不管 WiFi 连没连，不管重试策略。
"""

import time

from config import append_log, localize_error
from network import (
    HTTP_EXCEPTIONS,
    extract_ip_from_text,
    get_local_ip_for_target,
    http_get,
    parse_jsonp,
    pick_valid_ip,
    resolve_bind_ip,
)


def get_profile(cfg):
    """根据 cfg 中的 school 字段获取 profile 实例，找不到则使用默认"""
    short = str((cfg or {}).get("school", "")).strip()
    try:
        import schools

        if short:
            p = schools.get_profile(short)
            if p:
                return p
            raise LookupError("unknown school runtime: %s" % short)
        return schools.get_default_profile()
    except LookupError:
        raise
    except Exception:
        from schools._base import SchoolProfile

        return SchoolProfile()


def get_logout_username(cfg):
    user_id = str(cfg.get("user_id", "")).strip()
    if user_id:
        return user_id
    return str(cfg.get("username", "")).split("@", 1)[0].strip()


# ---------------------------------------------------------------------------
# 基础 API 调用
# ---------------------------------------------------------------------------
def init_getip(init_url, bind_ip=None):
    text = http_get(init_url, timeout=5, bind_ip=bind_ip)
    ip = extract_ip_from_text(text)
    if not ip:
        target_host = init_url.split("://", 1)[-1].split("/", 1)[0]
        ip = get_local_ip_for_target(target_host)
    if not ip:
        raise RuntimeError("无法获取本机登录 IP")
    return ip


def get_token(get_challenge_api, username, ip, bind_ip=None):
    now = int(time.time() * 1000)
    params = {
        "callback": "jQuery112404953340710317169_" + str(now),
        "username": username,
        "ip": ip,
        "_": now,
    }
    data = parse_jsonp(
        http_get(get_challenge_api, params=params, timeout=5, bind_ip=bind_ip)
    )
    token = data.get("challenge")
    if not token:
        msg = data.get("error_msg") or data.get("error") or "unknown response"
        raise RuntimeError("获取挑战码失败: " + localize_error(msg))
    resolved_ip = pick_valid_ip(data.get("client_ip"), data.get("online_ip"), ip)
    if not resolved_ip:
        raise RuntimeError("获取挑战码失败: 未获得有效客户端 IP")
    return token, resolved_ip


def login(profile, srun_portal_api, cfg, ip, i_value, hmd5, chksum, bind_ip=None):
    params = profile.build_login_params(cfg, ip, i_value, hmd5, chksum)
    data = parse_jsonp(
        http_get(srun_portal_api, params=params, timeout=5, bind_ip=bind_ip)
    )
    return profile.parse_login_response(data)


def logout(profile, rad_user_dm_api, cfg, ip, bind_ip=None):
    params = profile.build_logout_params(cfg, ip)
    data = parse_jsonp(
        http_get(rad_user_dm_api, params=params, timeout=5, bind_ip=bind_ip)
    )
    return profile.parse_logout_response(data)


def query_online_identity(profile, rad_user_info_api, expected_username, bind_ip=None):
    params = profile.build_online_query_params()
    data = parse_jsonp(
        http_get(rad_user_info_api, params=params, timeout=5, bind_ip=bind_ip)
    )
    return profile.parse_online_status(data, expected_username)


def query_online_status(profile, rad_user_info_api, expected_username, bind_ip=None):
    online, _, message = query_online_identity(
        profile, rad_user_info_api, expected_username, bind_ip
    )
    return online, message


def wait_for_logout_status(
    profile, rad_user_info_api, cfg, bind_ip=None, attempts=3, delay_seconds=1
):
    attempts = max(int(attempts), 1)
    last_message = ""
    for idx in range(attempts):
        online, message = query_online_status(
            profile, rad_user_info_api, cfg["username"], bind_ip=bind_ip
        )
        last_message = message
        if not online:
            return True, message
        if idx + 1 < attempts and delay_seconds > 0:
            time.sleep(delay_seconds)
    return False, last_message or "在线"


# ---------------------------------------------------------------------------
# 核心登录流程（纯认证，不管 WiFi）
# ---------------------------------------------------------------------------
def build_urls(cfg):
    profile = get_profile(cfg)
    return profile.build_urls(cfg["base_url"])


def run_once(cfg):
    """纯认证流程：challenge -> 加密 -> login API。
    不管 WiFi、不管 quiet hours、不管重试。"""
    profile = get_profile(cfg)
    urls = profile.build_urls(cfg["base_url"])
    bip = resolve_bind_ip(urls["init_url"], cfg)
    ip = init_getip(urls["init_url"], bind_ip=bip)
    token, ip = get_token(urls["get_challenge_api"], cfg["username"], ip, bind_ip=bip)
    i_value, hmd5, chksum = profile.do_complex_work(cfg, ip, token)
    ok, message = login(
        profile, urls["srun_portal_api"], cfg, ip, i_value, hmd5, chksum, bind_ip=bip
    )

    # challenge 过期重试（协议层面，不算业务重试）
    if (not ok) and ("challenge_expire_error" in message.lower()):
        token, ip = get_token(
            urls["get_challenge_api"], cfg["username"], ip, bind_ip=bip
        )
        i_value, hmd5, chksum = profile.do_complex_work(cfg, ip, token)
        ok, message = login(
            profile,
            urls["srun_portal_api"],
            cfg,
            ip,
            i_value,
            hmd5,
            chksum,
            bind_ip=bip,
        )

    # no_response_data 兜底：查一次在线状态
    if (not ok) and ("no_response_data_error" in message.lower()):
        try:
            online, online_msg = query_online_status(
                profile, urls["rad_user_info_api"], cfg["username"], bind_ip=bip
            )
            if online:
                return True, "已在线"
            return False, online_msg
        except Exception:
            pass

    if ok:
        return True, "登录成功"
    return False, "登录失败: " + localize_error(message)


def run_once_safe(cfg):
    try:
        return run_once(cfg)
    except HTTP_EXCEPTIONS as exc:
        return False, "网络错误: " + localize_error(exc)
    except ValueError as exc:
        return False, "响应解析错误: " + localize_error(exc)
    except Exception as exc:
        return False, "错误: " + localize_error(exc)

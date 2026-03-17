"""
School profile 注册表 -- 自动发现并加载所有学校适配文件。
"""

import importlib.util
import os

_PROFILES = {}
_LOADED = False


def _discover():
    global _LOADED
    if _LOADED:
        return
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    import sys
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)
    for fname in sorted(os.listdir(pkg_dir)):
        if fname.startswith("_") or not fname.endswith(".py"):
            continue
        mod_name = fname[:-3]
        filepath = os.path.join(pkg_dir, fname)
        try:
            spec = importlib.util.spec_from_file_location(
                "schools." + mod_name,
                filepath,
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
    _discover()
    return _PROFILES.get(short_name)


def list_schools():
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


def get_default_profile():
    _discover()
    if _PROFILES:
        if "jxnu" in _PROFILES:
            return _PROFILES["jxnu"]
        return next(iter(_PROFILES.values()))
    from _base import SchoolProfile
    return SchoolProfile()

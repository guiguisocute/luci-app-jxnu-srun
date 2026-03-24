#!/usr/bin/env python3

import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def read_text(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def require_contains(text, needle, label, failures):
    if needle not in text:
        failures.append("missing %s: %s" % (label, needle))


def require_regex(text, pattern, label, failures):
    if not re.search(pattern, text, re.MULTILINE | re.DOTALL):
        failures.append("missing %s: %s" % (label, pattern))


def require_not_contains(text, needle, label, failures):
    if needle in text:
        failures.append("unexpected %s: %s" % (label, needle))


def main():
    failures = []

    lua_source = read_text("root/usr/lib/lua/luci/model/cbi/smart_srun.lua")
    config_source = read_text("root/usr/lib/smart_srun/config.py")
    daemon_source = read_text("root/usr/lib/smart_srun/daemon.py")

    require_contains(
        lua_source,
        'run_client("schools inspect --selected", false)',
        "LuCI runtime inspection command",
        failures,
    )
    require_contains(
        lua_source,
        "local SUPPORTED_SCHOOL_EXTRA_TYPES = {",
        "supported LuCI descriptor type table",
        failures,
    )
    require_contains(
        lua_source,
        'cfg.school_extra = type(parsed.school_extra) == "table" and parsed.school_extra or {}',
        "school_extra load path",
        failures,
    )
    require_contains(
        lua_source,
        'out.school_extra = type(cfg.school_extra) == "table" and cfg.school_extra or {}',
        "school_extra save path",
        failures,
    )
    require_contains(
        lua_source,
        'if type(school_runtime_contract.school_extra) == "table" then',
        "school_extra guarded load path",
        failures,
    )
    require_regex(
        lua_source,
        r"cfg\.school_extra\s*=\s*school_runtime_contract\.school_extra",
        "LuCI consumes normalized contract school_extra",
        failures,
    )
    require_regex(
        lua_source,
        r"local school_runtime_renderable\s*=\s*type\(school_runtime_contract\.field_descriptors\) == \"table\"\s*and\s*type\(school_runtime_contract\.school_extra\) == \"table\"",
        "fail-closed render gate",
        failures,
    )
    require_regex(
        lua_source,
        r"if school_runtime_renderable then\s*for _, descriptor in ipairs\(school_runtime_contract\.field_descriptors\) do",
        "dynamic fields only render behind contract gate",
        failures,
    )
    require_not_contains(
        lua_source,
        "render_school_runtime_diagnostics_html",
        "LuCI runtime diagnostics renderer",
        failures,
    )
    require_not_contains(
        lua_source,
        "_school_runtime_diagnostics",
        "LuCI runtime diagnostics field",
        failures,
    )
    require_not_contains(
        lua_source,
        "runtime diagnostics",
        "runtime diagnostics marker",
        failures,
    )

    require_contains(
        config_source,
        "def build_school_runtime_luci_contract(cfg, inspection=None):",
        "Python LuCI contract builder",
        failures,
    )
    require_regex(
        config_source,
        r'result\["field_descriptors"\]\s*=\s*descriptors if descriptors is not None else None',
        "stable field_descriptors contract",
        failures,
    )
    require_regex(
        config_source,
        r'result\["school_extra"\]\s*=\s*school_extra if school_extra is not None else None',
        "stable school_extra contract",
        failures,
    )

    require_contains(
        daemon_source,
        "from config import (",
        "config import block",
        failures,
    )
    require_contains(
        daemon_source,
        "build_school_runtime_luci_contract,",
        "daemon uses contract builder",
        failures,
    )
    require_regex(
        daemon_source,
        r"build_school_runtime_luci_contract\s*\(\s*cfg,\s*school_runtime\.inspect_runtime\(cfg\)\s*\)",
        "schools inspect selected output path",
        failures,
    )

    if failures:
        for failure in failures:
            print("FAIL:", failure)
        return 1

    print("OK: school runtime LuCI source contracts present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

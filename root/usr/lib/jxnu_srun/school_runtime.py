"""
School runtime loader and compatibility adapters.
"""

import inspect

import crypto
import schools

from schools._base import SchoolProfile


RUNTIME_API_VERSION = 1


def build_core_api():
    return {
        "runtime_api_version": RUNTIME_API_VERSION,
        "get_base64": crypto.get_base64,
        "get_xencode": crypto.get_xencode,
        "get_md5": crypto.get_md5,
        "get_sha1": crypto.get_sha1,
        "get_info": crypto.get_info,
        "get_chksum": crypto.get_chksum,
    }


def _apply_legacy_profile_metadata(runtime, metadata):
    runtime.SHORT_NAME = metadata.get("short_name", "")
    runtime.NAME = metadata.get("name", "")
    runtime.DESCRIPTION = metadata.get("description", "")
    runtime.CONTRIBUTORS = tuple(metadata.get("contributors", ()))
    runtime.OPERATORS = tuple(metadata.get("operators", ()))
    runtime.NO_SUFFIX_OPERATORS = tuple(metadata.get("no_suffix_operators", ()))
    return runtime


class LegacyProfileRuntimeAdapter(object):
    def __init__(self, profile, source_file=None, metadata=None):
        self._profile = profile
        self.runtime_type = "legacy_profile"
        self.runtime_api_version = RUNTIME_API_VERSION
        self.source_file = source_file or getattr(profile.__class__, "__file__", "")
        self.declared_capabilities = tuple((metadata or {}).get("capabilities", ()))
        _apply_legacy_profile_metadata(self, metadata or {})

    def __getattr__(self, name):
        return getattr(self._profile, name)


class DefaultRuntime(LegacyProfileRuntimeAdapter):
    def __init__(self):
        profile = SchoolProfile()
        LegacyProfileRuntimeAdapter.__init__(
            self,
            profile,
            source_file=inspect.getsourcefile(SchoolProfile) or "",
            metadata=schools.get_default_school_metadata(),
        )
        self.runtime_type = "default"


def _get_runtime_metadata(short_name):
    if short_name == "default":
        return schools.get_default_school_metadata()
    metadata = schools.get_school_metadata(short_name)
    if metadata:
        return metadata
    return schools.get_default_school_metadata()


def _finalize_runtime(runtime, metadata, runtime_type, source_file):
    _apply_legacy_profile_metadata(runtime, metadata)
    runtime.runtime_type = getattr(runtime, "runtime_type", runtime_type)
    runtime.runtime_api_version = getattr(
        runtime, "runtime_api_version", RUNTIME_API_VERSION
    )
    runtime.source_file = getattr(runtime, "source_file", source_file)
    runtime.declared_capabilities = tuple(
        getattr(runtime, "declared_capabilities", metadata.get("capabilities", ()))
    )
    return runtime


def resolve_runtime(cfg):
    cfg = cfg or {}
    short_name = str(cfg.get("school", "")).strip()
    if not short_name or short_name == "default":
        return DefaultRuntime()

    entry = schools.get_school_entry(short_name)
    if not entry:
        raise LookupError("unknown school runtime: %s" % short_name)

    module = entry["module"]
    metadata = entry["metadata"]
    core_api = build_core_api()

    if callable(getattr(module, "build_runtime", None)):
        runtime = module.build_runtime(core_api, cfg)
        return _finalize_runtime(
            runtime, metadata, "build_runtime", entry["source_file"]
        )

    runtime_class = getattr(module, "Runtime", None)
    if runtime_class:
        runtime = runtime_class(core_api, cfg)
        return _finalize_runtime(
            runtime, metadata, "runtime_class", entry["source_file"]
        )

    profile_class = getattr(module, "Profile", None)
    if profile_class:
        return LegacyProfileRuntimeAdapter(
            profile_class(),
            source_file=entry["source_file"],
            metadata=metadata,
        )

    raise LookupError("school runtime has no supported entrypoint: %s" % short_name)


def build_app_context(cfg, runtime=None):
    cfg = cfg or {}
    runtime = runtime or resolve_runtime(cfg)
    short_name = str(cfg.get("school", "")).strip() or getattr(
        runtime, "SHORT_NAME", "default"
    )
    return {
        "cfg": cfg,
        "runtime": runtime,
        "core_api": build_core_api(),
        "runtime_api_version": getattr(
            runtime, "runtime_api_version", RUNTIME_API_VERSION
        ),
        "school_metadata": _get_runtime_metadata(short_name),
    }


def inspect_runtime(cfg):
    runtime = resolve_runtime(cfg)
    short_name = getattr(
        runtime, "SHORT_NAME", str((cfg or {}).get("school", "")).strip() or "default"
    )
    metadata = _get_runtime_metadata(short_name)
    result = dict(metadata)
    result["runtime_type"] = getattr(runtime, "runtime_type", "unknown")
    result["runtime_api_version"] = getattr(
        runtime, "runtime_api_version", RUNTIME_API_VERSION
    )
    result["source_file"] = getattr(runtime, "source_file", "")
    result["declared_capabilities"] = list(
        getattr(runtime, "declared_capabilities", ())
    )
    return result

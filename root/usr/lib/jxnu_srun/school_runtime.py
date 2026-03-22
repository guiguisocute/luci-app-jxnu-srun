"""
School runtime loader and compatibility adapters.
"""

import inspect
import types

import crypto
import schools

from schools._base import SchoolProfile


RUNTIME_API_VERSION = 1


def build_core_api():
    import orchestrator
    import srun_auth

    return {
        "runtime_api_version": RUNTIME_API_VERSION,
        "get_base64": crypto.get_base64,
        "get_xencode": crypto.get_xencode,
        "get_md5": crypto.get_md5,
        "get_sha1": crypto.get_sha1,
        "get_info": crypto.get_info,
        "get_chksum": crypto.get_chksum,
        "default_login_once": srun_auth.default_login_once,
        "default_logout_once": srun_auth.default_logout_once,
        "default_query_online_identity": srun_auth.default_query_online_identity,
        "default_query_online_status": srun_auth.default_query_online_status,
        "default_run_status": orchestrator.default_run_status,
        "default_run_quiet_logout": orchestrator.default_run_quiet_logout,
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

    def login_once(self, app_ctx):
        return app_ctx["core_api"]["default_login_once"](app_ctx)

    def logout_once(self, app_ctx, override_user_id=None, bind_ip=None):
        return app_ctx["core_api"]["default_logout_once"](
            app_ctx, override_user_id=override_user_id, bind_ip=bind_ip
        )

    def query_online_identity(self, app_ctx, expected_username=None, bind_ip=None):
        return app_ctx["core_api"]["default_query_online_identity"](
            app_ctx, expected_username=expected_username, bind_ip=bind_ip
        )

    def query_online_status(self, app_ctx, expected_username=None, bind_ip=None):
        return app_ctx["core_api"]["default_query_online_status"](
            app_ctx, expected_username=expected_username, bind_ip=bind_ip
        )

    def status(self, app_ctx):
        return app_ctx["core_api"]["default_run_status"](app_ctx)

    def quiet_logout(self, app_ctx):
        return app_ctx["core_api"]["default_run_quiet_logout"](app_ctx)


_BOUNDARY_METHODS = (
    "login_once",
    "logout_once",
    "query_online_identity",
    "query_online_status",
    "status",
    "quiet_logout",
)


def _attach_default_boundary_methods(runtime):
    for name in _BOUNDARY_METHODS:
        if callable(getattr(runtime, name, None)):
            continue
        method = getattr(LegacyProfileRuntimeAdapter, name)
        setattr(runtime, name, types.MethodType(method, runtime))
    return runtime


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
    _attach_default_boundary_methods(runtime)
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

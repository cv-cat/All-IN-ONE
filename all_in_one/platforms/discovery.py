from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


UPSTREAM_LAYOUT = {
    "xhs": (
        "Spider_XHS",
        "apis",
        {
            "xhs_pc_apis.py",
            "xhs_pc_login_apis.py",
            "xhs_creator_apis.py",
            "xhs_creator_login_apis.py",
            "xhs_pugongying_apis.py",
            "xhs_qianfan_apis.py",
        },
    ),
    "weibo": (
        "WeiboApis",
        "apis",
        {
            "weibo_apis.py",
            "weibo_creator_apis.py",
            "weibo_mobile_apis.py",
        },
    ),
    "douyin": (
        "DouYin_Spider",
        "dy_apis",
        {
            "douyin_api.py",
        },
    ),
}

VERBS = {
    "check",
    "choose",
    "collect",
    "create",
    "digg",
    "encryption",
    "extract",
    "generate",
    "get",
    "login",
    "move",
    "post",
    "publish",
    "query",
    "remove",
    "search",
    "send",
    "upload",
    "video",
}

MODULE_DOMAINS: Dict[Tuple[str, str], str] = {
    ("xhs", "xhs_pc_login_apis"): "pc-login",
    ("xhs", "xhs_creator_login_apis"): "creator-login",
    ("xhs", "xhs_pugongying_apis"): "pugongying",
    ("xhs", "xhs_qianfan_apis"): "qianfan",
    ("weibo", "weibo_mobile_apis"): "mobile",
}


@dataclass(frozen=True)
class Endpoint:
    platform: str
    source_file: Path
    module_name: str
    class_name: Optional[str]
    function_name: str
    target_id: str
    resource: str
    action: str
    parameters: Tuple[str, ...]
    auth_profile: str
    docstring: Optional[str] = None


def scoped_source_files(platform: str, upstream_root: Path = Path("upstreams")) -> List[Path]:
    repo_name, package_dir, filenames = UPSTREAM_LAYOUT[platform]
    source_dir = Path(upstream_root) / repo_name / package_dir
    if not source_dir.is_dir():
        return []
    return sorted(path for path in source_dir.glob("*.py") if path.name in filenames)


def discover_endpoints(
    platform: str, upstream_root: Path = Path("upstreams")
) -> List[Endpoint]:
    endpoints: List[Endpoint] = []
    for source_file in scoped_source_files(platform, upstream_root):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        module_name = source_file.stem
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _is_public_function(node.name):
                    endpoints.append(
                        _build_endpoint(platform, source_file, module_name, None, node)
                    )
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if _is_public_method(child.name):
                            endpoints.append(
                                _build_endpoint(
                                    platform, source_file, module_name, node.name, child
                                )
                            )
    return endpoints


def command_for(function_name: str) -> Tuple[str, str]:
    overrides = {
        "search_note": ("note", "search"),
        "getUserInfo": ("user", "info"),
        "get_work_info": ("work", "info"),
    }
    if function_name in overrides:
        return overrides[function_name]

    tokens = split_identifier(function_name)
    if not tokens:
        return "api", function_name.replace("_", "-")

    if tokens[0] == "search":
        return _search_command(tokens)

    if tokens[0] == "get":
        return _get_command(tokens)

    if tokens[0] in VERBS:
        resource_tokens = tokens[1:] or [tokens[0]]
        resource = _first_resource(resource_tokens)
        action = tokens[0]
        remaining = [token for token in resource_tokens if token != resource]
        if remaining:
            action = "-".join([action] + remaining)
        return resource, action

    return _first_resource(tokens), "-".join(tokens[1:] or ["run"])


def command_for_endpoint(
    platform: str,
    module_name: str,
    class_name: Optional[str],
    function_name: str,
) -> Tuple[str, str]:
    endpoint_overrides = {
        ("xhs", "xhs_creator_apis", "XHS_Creator_Apis", "post_note"): (
            "creator",
            "post-note",
        ),
        ("weibo", "weibo_apis", "WeiboApis", "searchSome"): ("post", "search"),
        ("weibo", "weibo_mobile_apis", "WeiboMobileApis", "searchSome"): (
            "mobile",
            "search",
        ),
    }
    override = endpoint_overrides.get(
        (platform, module_name, class_name, function_name)
    )
    if override is not None:
        return override

    domain = MODULE_DOMAINS.get((platform, module_name))
    if domain is not None:
        return domain, _action_for_domain_function(function_name)

    return command_for(function_name)


def _action_for_domain_function(function_name: str) -> str:
    tokens = split_identifier(function_name)
    if tokens and tokens[0] in {"get", "search"}:
        tokens = tokens[1:]
    return "-".join(tokens) if tokens else function_name.replace("_", "-")


def auth_profile_for_endpoint(
    platform: str,
    module_name: str,
    class_name: Optional[str],
    function_name: str,
) -> str:
    if platform == "xhs":
        if module_name.startswith("xhs_creator"):
            return "creator"
        if module_name == "xhs_pugongying_apis":
            return "pugongying"
        if module_name == "xhs_qianfan_apis":
            return "qianfan"
        return "pc"
    if platform == "weibo":
        if module_name == "weibo_creator_apis":
            return "creator"
        if module_name == "weibo_mobile_apis":
            return "mobile"
        return "web"
    if platform == "douyin":
        live_names = {
            "search_live",
            "search_some_live",
            "get_live_info",
            "get_live_production",
            "get_all_live_production",
            "get_live_production_detail",
            "get_rank_list",
            "get_webcast_detail",
            "diggLiveRoom",
            "sendMsgInRoom",
        }
        return "live" if function_name in live_names else "web"
    return "default"


def split_identifier(name: str) -> List[str]:
    with_boundaries = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [part.lower() for part in re.split(r"[_\W]+", with_boundaries) if part]


def _build_endpoint(
    platform: str,
    source_file: Path,
    module_name: str,
    class_name: Optional[str],
    function_node: ast.FunctionDef,
) -> Endpoint:
    resource, action = command_for_endpoint(
        platform, module_name, class_name, function_node.name
    )
    target = (
        f"{source_file.name}:{class_name}.{function_node.name}"
        if class_name
        else f"{source_file.name}:{function_node.name}"
    )
    return Endpoint(
        platform=platform,
        source_file=source_file,
        module_name=module_name,
        class_name=class_name,
        function_name=function_node.name,
        target_id=target,
        resource=resource,
        action=action,
        parameters=tuple(_parameters(function_node)),
        auth_profile=auth_profile_for_endpoint(
            platform, module_name, class_name, function_node.name
        ),
        docstring=_extract_docstring(function_node),
    )


def _extract_docstring(node: ast.FunctionDef) -> Optional[str]:
    if (
        node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    ):
        raw = node.body[0].value.value
        if isinstance(raw, str):
            return raw.strip().split("\n")[0].strip()
    return None


def _parameters(function_node: ast.FunctionDef) -> Iterable[str]:
    args = function_node.args
    names = [arg.arg for arg in args.posonlyargs + args.args + args.kwonlyargs]
    return [name for name in names if name not in {"self", "cls"}]


def _is_public_function(name: str) -> bool:
    return not name.startswith("_")


def _is_public_method(name: str) -> bool:
    return name != "__init__" and not name.startswith("_")


def _search_command(tokens: Sequence[str]) -> Tuple[str, str]:
    remaining = list(tokens[1:])
    is_some = bool(remaining and remaining[0] == "some")
    if is_some:
        remaining = remaining[1:]
    resource = _last_resource(remaining) if remaining else "search"
    descriptors = [token for token in remaining if token != resource]
    action_parts = ["search"]
    if is_some:
        action_parts.append("some")
    action_parts.extend(descriptors)
    return resource, "-".join(action_parts)


def _get_command(tokens: Sequence[str]) -> Tuple[str, str]:
    remaining = list(tokens[1:])
    if remaining[:1] == ["my"]:
        remaining = ["self"] + remaining[1:]
    resource = _first_resource(remaining) if remaining else "api"
    action_tokens = [token for token in remaining if token != resource]
    if action_tokens and action_tokens[-1] == "info":
        action = "info" if len(action_tokens) == 1 else "-".join(action_tokens)
    elif action_tokens:
        action = "-".join(action_tokens)
    else:
        action = "get"
    return resource, action


def _first_resource(tokens: Sequence[str]) -> str:
    for token in tokens:
        if token not in {"all", "some", "by", "self", "my"}:
            return token
    return tokens[0] if tokens else "api"


def _last_resource(tokens: Sequence[str]) -> str:
    for token in reversed(tokens):
        if token not in {"all", "some", "by", "general", "video"}:
            return token
    return tokens[-1] if tokens else "api"

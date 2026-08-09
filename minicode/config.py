from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


MINI_CODE_DIR = Path.home() / ".mini-code"
MINI_CODE_SETTINGS_PATH = MINI_CODE_DIR / "settings.json"
MINI_CODE_HISTORY_PATH = MINI_CODE_DIR / "history.json"
MINI_CODE_PERMISSIONS_PATH = MINI_CODE_DIR / "permissions.json"
MINI_CODE_MCP_PATH = MINI_CODE_DIR / "mcp.json"
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# 已知的合法模型名称（用于拼写检查提示）
KNOWN_MODELS = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-haiku-3-20240307",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
]

KNOWN_PROVIDERS = {"anthropic", "openai"}


def _suggest_model_name(typed: str) -> str:
    """根据输入建议最接近的合法模型名称"""
    if not typed:
        return ""
    
    # 简单的前缀匹配
    for model in KNOWN_MODELS:
        if model.startswith(typed.lower()):
            return model
    
    # 模糊匹配：包含输入字符的模型
    for model in KNOWN_MODELS:
        if typed.lower() in model:
            return model
    
    return ""


def project_mcp_path(cwd: str | Path | None = None) -> Path:
    return Path(cwd or Path.cwd()) / ".mcp.json"


def _read_json_file(file_path: Path) -> dict[str, Any]:
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def read_settings_file(file_path: Path) -> dict[str, Any]:
    return _read_json_file(file_path)


def read_mcp_config_file(file_path: Path) -> dict[str, Any]:
    parsed = _read_json_file(file_path)
    if not isinstance(parsed, dict):
        return {}
    mcp_servers = parsed.get("mcpServers", {})
    return mcp_servers if isinstance(mcp_servers, dict) else {}


def merge_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged_mcp = dict(base.get("mcpServers", {}))
    for name, server in override.get("mcpServers", {}).items():
        current = dict(merged_mcp.get(name, {}))
        next_server = dict(server)
        current.update(next_server)
        current["env"] = {
            **dict(merged_mcp.get(name, {}).get("env", {})),
            **dict(next_server.get("env", {})),
        }
        merged_mcp[name] = current

    return {
        **base,
        **override,
        "env": {
            **dict(base.get("env", {})),
            **dict(override.get("env", {})),
        },
        "mcpServers": merged_mcp,
    }


def load_effective_settings(cwd: str | Path | None = None) -> dict[str, Any]:
    claude_settings = read_settings_file(CLAUDE_SETTINGS_PATH)
    global_mcp = read_mcp_config_file(MINI_CODE_MCP_PATH)
    project_mcp = read_mcp_config_file(project_mcp_path(cwd))
    mini_code_settings = read_settings_file(MINI_CODE_SETTINGS_PATH)

    return merge_settings(
        merge_settings(
            merge_settings(claude_settings, {"mcpServers": global_mcp}),
            {"mcpServers": project_mcp},
        ),
        mini_code_settings,
    )


def save_mini_code_settings(updates: dict[str, Any]) -> None:
    MINI_CODE_DIR.mkdir(parents=True, exist_ok=True)
    existing = read_settings_file(MINI_CODE_SETTINGS_PATH)
    next_settings = merge_settings(existing, updates)
    MINI_CODE_SETTINGS_PATH.write_text(
        json.dumps(next_settings, indent=2) + "\n",
        encoding="utf-8",
    )


def load_runtime_config(cwd: str | Path | None = None) -> dict[str, Any]:
    effective = load_effective_settings(cwd)
    env = {**dict(effective.get("env", {})), **os.environ}
    anthropic_auth_token = str(env.get("ANTHROPIC_AUTH_TOKEN", "")).strip() or None
    anthropic_api_key = str(env.get("ANTHROPIC_API_KEY", "")).strip() or None
    openai_api_key = str(env.get("OPENAI_API_KEY", "")).strip() or None
    configured_provider = str(
        os.environ.get("MINI_CODE_PROVIDER")
        or effective.get("provider")
        or env.get("MINI_CODE_PROVIDER", "")
    ).strip().lower()
    if configured_provider:
        provider = configured_provider
    elif openai_api_key and not anthropic_auth_token and not anthropic_api_key:
        provider = "openai"
    else:
        provider = "anthropic"
    if provider not in KNOWN_PROVIDERS:
        raise RuntimeError(
            f"Unsupported provider '{provider}'. Choose one of: {', '.join(sorted(KNOWN_PROVIDERS))}."
        )

    provider_model_env = "OPENAI_MODEL" if provider == "openai" else "ANTHROPIC_MODEL"
    model = (
        os.environ.get("MINI_CODE_MODEL")
        or os.environ.get(provider_model_env)
        or effective.get("model")
        or str(env.get(provider_model_env, "")).strip()
    )
    if provider == "openai":
        base_url = str(env.get("OPENAI_BASE_URL", "")).strip() or "https://api.openai.com"
        auth_token = None
        api_key = openai_api_key
    else:
        base_url = str(env.get("ANTHROPIC_BASE_URL", "")).strip() or "https://api.anthropic.com"
        auth_token = anthropic_auth_token
        api_key = anthropic_api_key
    raw_max_output_tokens = (
        os.environ.get("MINI_CODE_MAX_OUTPUT_TOKENS")
        or effective.get("maxOutputTokens")
        or env.get("MINI_CODE_MAX_OUTPUT_TOKENS")
    )
    max_output_tokens = None
    if raw_max_output_tokens is not None:
        try:
            parsed = int(raw_max_output_tokens)
            if parsed > 0:
                max_output_tokens = parsed
        except (TypeError, ValueError):
            max_output_tokens = None

    if not model:
        raise RuntimeError(
            f"No model configured. Set ~/.mini-code/settings.json or {provider_model_env}."
        )
    if not auth_token and not api_key:
        expected_auth = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY"
        raise RuntimeError(f"No auth configured for {provider}. Set {expected_auth}.")

    is_deepseek = "deepseek" in base_url.lower() or str(model).lower().startswith("deepseek-")
    default_openai_tokens_param = "max_tokens" if is_deepseek else "max_completion_tokens"
    openai_max_tokens_param = str(
        env.get("OPENAI_MAX_TOKENS_PARAM", default_openai_tokens_param)
    ).strip()
    if openai_max_tokens_param not in {"max_completion_tokens", "max_tokens"}:
        raise RuntimeError(
            "OPENAI_MAX_TOKENS_PARAM must be 'max_completion_tokens' or 'max_tokens'."
        )
    thinking_mode = str(env.get("MINI_CODE_THINKING", "")).strip().lower() or None
    if thinking_mode not in {None, "enabled", "disabled"}:
        raise RuntimeError("MINI_CODE_THINKING must be 'enabled' or 'disabled'.")
    reasoning_effort = str(env.get("MINI_CODE_REASONING_EFFORT", "")).strip().lower() or None

    return {
        "provider": provider,
        "model": model,
        "baseUrl": base_url,
        "authToken": auth_token,
        "apiKey": api_key,
        "maxOutputTokens": max_output_tokens,
        "openaiMaxTokensParam": openai_max_tokens_param,
        "openaiOrganization": str(env.get("OPENAI_ORGANIZATION", "")).strip() or None,
        "openaiProject": str(env.get("OPENAI_PROJECT", "")).strip() or None,
        "thinkingMode": thinking_mode,
        "reasoningEffort": reasoning_effort,
        "authSource": (
            "OPENAI_API_KEY"
            if provider == "openai"
            else ("ANTHROPIC_AUTH_TOKEN" if auth_token else "ANTHROPIC_API_KEY")
        ),
        "mcpServers": effective.get("mcpServers", {}),
        "sourceSummary": f"process.env > {MINI_CODE_SETTINGS_PATH} > {CLAUDE_SETTINGS_PATH}",
    }


def get_mcp_config_path(scope: str, cwd: str | Path | None = None) -> Path:
    return project_mcp_path(cwd) if scope == "project" else MINI_CODE_MCP_PATH


def load_scoped_mcp_servers(scope: str, cwd: str | Path | None = None) -> dict[str, Any]:
    return read_mcp_config_file(get_mcp_config_path(scope, cwd))


def save_scoped_mcp_servers(scope: str, servers: dict[str, Any], cwd: str | Path | None = None) -> None:
    target = get_mcp_config_path(scope, cwd)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n", encoding="utf-8")


def validate_config(cwd: str | Path | None = None) -> tuple[bool, list[str]]:
    """验证配置完整性，返回 (是否有效，错误列表)
    
    检查项：
    1. 模型名称是否配置
    2. API key 是否配置
    3. 模型名称拼写是否正确
    4. MCP 配置文件是否合法
    """
    errors: list[str] = []
    warnings: list[str] = []
    
    try:
        config = load_runtime_config(cwd)
        
        # 检查模型名称拼写
        model = config.get("model", "")
        if model and not any(model.lower() == km.lower() for km in KNOWN_MODELS):
            suggestion = _suggest_model_name(model)
            if suggestion:
                warnings.append(
                    f"Unknown model '{model}'. Did you mean '{suggestion}'?"
                )
            else:
                warnings.append(
                    f"Unknown model '{model}'. Known models: {', '.join(KNOWN_MODELS[:3])}..."
                )
        
        # 检查 MCP 配置
        mcp_servers = config.get("mcpServers", {})
        for name, server in mcp_servers.items():
            if not server.get("command"):
                errors.append(f"MCP server '{name}' has no command configured")
        
        return len(errors) == 0, errors + warnings
        
    except RuntimeError as e:
        error_msg = str(e)
        
        # 提供友好的错误消息
        if "No model configured" in error_msg:
            suggestion = _suggest_model_name(os.environ.get("MINI_CODE_MODEL", ""))
            help_msg = (
                f"Error: {error_msg}\n\n"
                "How to fix:\n"
                "  1. Set MINI_CODE_PROVIDER to anthropic or openai.\n"
                "  2. Set ANTHROPIC_MODEL or OPENAI_MODEL for that provider.\n"
                "  3. Or edit ~/.mini-code/settings.json, for example:\n"
                f'     {{"provider": "openai", "model": "deepseek-v4-pro"}}\n'
            )
            if suggestion:
                help_msg += f"\n  Did you mean: {suggestion}?\n"
            help_msg += f"\n  Known models: {', '.join(KNOWN_MODELS[:3])}..."
            errors.append(help_msg)
            
        elif "No auth configured" in error_msg:
            is_openai = "for openai" in error_msg
            auth_name = "OPENAI_API_KEY" if is_openai else "ANTHROPIC_API_KEY"
            auth_example = "your-openai-compatible-key" if is_openai else "sk-ant-..."
            help_msg = (
                f"Error: {error_msg}\n\n"
                "How to fix:\n"
                f"  1. Set API key: export {auth_name}={auth_example}\n"
                "  2. Or edit ~/.mini-code/settings.json:\n"
                f'     {{"env": {{"{auth_name}": "{auth_example}"}}}}\n'
            )
            errors.append(help_msg)
        else:
            errors.append(str(e))
        
        return False, errors
    except Exception as e:
        return False, [f"Unexpected error: {e}"]


def format_config_diagnostic(cwd: str | Path | None = None) -> str:
    """格式化配置诊断信息"""
    is_valid, messages = validate_config(cwd)
    
    lines = ["Configuration Diagnostics", "=" * 40, ""]
    
    if is_valid:
        lines.append("Status: OK")
        if messages:
            lines.append("")
            lines.append("Warnings:")
            for msg in messages:
                lines.append(f"  ⚠️  {msg}")
    else:
        lines.append("Status: ERRORS")
        lines.append("")
        lines.append("Errors:")
        for msg in messages:
            lines.append(f"  ❌ {msg}")
    
    # 显示当前配置摘要
    try:
        config = load_runtime_config(cwd)
        lines.append("")
        lines.append("Current Configuration")
        lines.append("-" * 40)
        lines.append(f"  Model: {config.get('model', 'not set')}")
        lines.append(f"  Base URL: {config.get('baseUrl', 'not set')}")
        lines.append(f"  Provider: {config.get('provider', 'anthropic')}")
        auth_method = config.get("authSource", "not set")
        lines.append(f"  Auth: {auth_method}")
        lines.append(f"  MCP Servers: {len(config.get('mcpServers', {}))}")
    except Exception:
        pass
    
    return "\n".join(lines)

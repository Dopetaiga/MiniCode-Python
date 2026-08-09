from minicode.config import load_runtime_config, merge_settings


def test_merge_settings_merges_env_and_mcp_servers() -> None:
    merged = merge_settings(
        {
            "env": {"A": "1"},
            "mcpServers": {
                "fs": {"command": "npx", "args": ["a"], "env": {"X": "1"}}
            },
        },
        {
            "env": {"B": "2"},
            "mcpServers": {
                "fs": {"command": "uvx", "env": {"Y": "2"}},
                "search": {"command": "python"},
            },
        },
    )

    assert merged["env"] == {"A": "1", "B": "2"}
    assert merged["mcpServers"]["fs"]["command"] == "uvx"
    assert merged["mcpServers"]["fs"]["args"] == ["a"]
    assert merged["mcpServers"]["fs"]["env"] == {"X": "1", "Y": "2"}
    assert merged["mcpServers"]["search"]["command"] == "python"


def test_load_runtime_config_supports_explicit_openai_provider(monkeypatch) -> None:
    monkeypatch.setattr("minicode.config.load_effective_settings", lambda _cwd=None: {})
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
        "MINI_CODE_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MINI_CODE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")

    runtime = load_runtime_config()

    assert runtime["provider"] == "openai"
    assert runtime["model"] == "test-model"
    assert runtime["baseUrl"] == "https://gateway.example/v1"
    assert runtime["apiKey"] == "test-key"
    assert runtime["authSource"] == "OPENAI_API_KEY"


def test_load_runtime_config_infers_openai_from_only_openai_key(monkeypatch) -> None:
    monkeypatch.setattr("minicode.config.load_effective_settings", lambda _cwd=None: {})
    for name in (
        "MINI_CODE_PROVIDER",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
        "MINI_CODE_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    runtime = load_runtime_config()

    assert runtime["provider"] == "openai"
    assert runtime["baseUrl"] == "https://api.openai.com"


def test_load_runtime_config_keeps_anthropic_as_default(monkeypatch) -> None:
    monkeypatch.setattr("minicode.config.load_effective_settings", lambda _cwd=None: {})
    for name in ("MINI_CODE_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL", "MINI_CODE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test")

    runtime = load_runtime_config()

    assert runtime["provider"] == "anthropic"
    assert runtime["baseUrl"] == "https://api.anthropic.com"


def test_load_runtime_config_uses_deepseek_compatible_token_parameter(monkeypatch) -> None:
    monkeypatch.setattr("minicode.config.load_effective_settings", lambda _cwd=None: {})
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "MINI_CODE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MINI_CODE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.delenv("OPENAI_MAX_TOKENS_PARAM", raising=False)

    runtime = load_runtime_config()

    assert runtime["openaiMaxTokensParam"] == "max_tokens"


def test_provider_model_environment_overrides_saved_model(monkeypatch) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda _cwd=None: {
            "provider": "openai",
            "model": "saved-model",
            "env": {"OPENAI_API_KEY": "saved-key"},
        },
    )
    monkeypatch.delenv("MINI_CODE_MODEL", raising=False)
    monkeypatch.delenv("MINI_CODE_PROVIDER", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-pro")

    runtime = load_runtime_config()

    assert runtime["model"] == "deepseek-v4-pro"


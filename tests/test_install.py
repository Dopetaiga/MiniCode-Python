from __future__ import annotations

from minicode import install


def test_installer_saves_openai_compatible_provider(monkeypatch) -> None:
    answers = iter(
        [
            "openai",
            "deepseek-v4-pro",
            "https://api.deepseek.com",
            "test-key",
        ]
    )
    saved: dict = {}

    monkeypatch.setattr(install, "load_effective_settings", lambda: {})
    monkeypatch.setattr(
        install,
        "_read_input",
        lambda _prompt, _default=None: next(answers),
    )
    monkeypatch.setattr(install, "save_mini_code_settings", lambda value: saved.update(value))
    monkeypatch.setattr(install, "_install_launcher_script", lambda: None)

    install.main()

    assert saved == {
        "provider": "openai",
        "model": "deepseek-v4-pro",
        "env": {
            "OPENAI_BASE_URL": "https://api.deepseek.com",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_MODEL": "deepseek-v4-pro",
        },
    }

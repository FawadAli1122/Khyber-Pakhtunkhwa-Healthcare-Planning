from server import chat_ui, keystore


def test_provider_options_match_keystore_providers():
    option_values = [value for value, _label in chat_ui.PROVIDER_OPTIONS]
    assert set(option_values) == set(keystore.PROVIDERS)


def test_render_widget_contains_key_hooks():
    widget = chat_ui.render_widget()
    for hook in (
        'id="ai-chat-toggle"',
        'id="ai-chat-panel"',
        'id="ai-chat-form"',
        'id="ai-chat-provider"',
        'id="ai-chat-input"',
        'id="ai-chat-log"',
        "/api/ask",
    ):
        assert hook in widget, f"missing hook: {hook}"


def test_render_widget_lists_all_providers():
    widget = chat_ui.render_widget()
    for value, _label in chat_ui.PROVIDER_OPTIONS:
        assert f'value="{value}"' in widget


def test_chat_js_braces_and_parens_balance():
    js = chat_ui.CHAT_JS
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")
    assert js.count("[") == js.count("]")

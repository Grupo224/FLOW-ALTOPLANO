# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt
"""
Multi-provider LLM abstraction layer.

Presents a single client interface to every AI node, dispatching to the
appropriate provider adapter. The public API mirrors the Anthropic SDK
surface (messages.create + response.content blocks) so existing node
executors keep working unchanged while gaining multi-provider support.

Supported providers:
  - Anthropic  (native SDK)
  - OpenAI     (OpenAI-compatible REST)
  - DeepSeek   (OpenAI-compatible REST)
  - OpenRouter (OpenAI-compatible REST)
  - Ollama     (OpenAI-compatible REST, local)

API keys are resolved from FlowAgent Settings (encrypted Password fields)
and are NEVER logged or returned to the client.
"""

from __future__ import annotations

import json

import frappe


# Provider registry ---------------------------------------------------------
PROVIDERS = ["Anthropic", "OpenAI", "DeepSeek", "OpenRouter", "Ollama"]

# Providers that speak the OpenAI /chat/completions wire format.
OPENAI_COMPATIBLE = {"OpenAI", "DeepSeek", "OpenRouter", "Ollama"}

DEFAULT_BASE_URLS = {
    "OpenAI": "https://api.openai.com/v1",
    "DeepSeek": "https://api.deepseek.com/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
    "Ollama": "http://localhost:11434/v1",
}

DEFAULT_MODELS = {
    "Anthropic": "claude-sonnet-4-5",
    "OpenAI": "gpt-4o",
    "DeepSeek": "deepseek-chat",
    "OpenRouter": "openai/gpt-4o",
    "Ollama": "llama3.1",
}


def get_settings():
    return frappe.get_single("FlowAgent Settings")


def _provider_row(provider: str):
    """Return the FlowAgent Provider child-table row for a provider, or None."""
    settings = get_settings()
    for row in getattr(settings, "providers", None) or []:
        if row.provider == provider and getattr(row, "enabled", 1):
            return row
    return None


def _resolve_api_key(provider: str, row) -> str:
    """Resolve the API key for a provider without exposing it."""
    # 1) child-table encrypted Password field
    if row is not None:
        try:
            key = row.get_password("api_key", raise_exception=False)
            if key:
                return key
        except Exception:
            pass

    # 2) legacy Anthropic path (keep old installs working)
    if provider == "Anthropic":
        settings = get_settings()
        key = settings.get_password("anthropic_api_key", raise_exception=False)
        if key:
            return key
        site_key = frappe.conf.get("anthropic_api_key")
        if site_key:
            return site_key
        import os
        return os.environ.get("ANTHROPIC_API_KEY", "")

    # 3) site_config + env fallback for the rest
    import os
    conf_map = {
        "OpenAI": "openai_api_key",
        "DeepSeek": "deepseek_api_key",
        "OpenRouter": "openrouter_api_key",
        "Ollama": "ollama_api_key",
    }
    site_key = frappe.conf.get(conf_map.get(provider, ""))
    if site_key:
        return site_key
    env_map = {
        "OpenAI": "OPENAI_API_KEY",
        "DeepSeek": "DEEPSEEK_API_KEY",
        "OpenRouter": "OPENROUTER_API_KEY",
        "Ollama": "OLLAMA_API_KEY",
    }
    return os.environ.get(env_map.get(provider, ""), "")


def resolve_provider_config(provider: str | None = None) -> dict:
    """Resolve the effective provider configuration dict.

    Order: explicit provider arg -> Settings default_provider -> Anthropic.
    The resolved API key is included under the private "_key" key for
    internal use by :class:`UnifiedClient`. Treat this dict as secret:
    never log or serialise it wholesale.
    """
    settings = get_settings()
    provider = provider or getattr(settings, "default_provider", None) or "Anthropic"
    if provider not in PROVIDERS:
        frappe.throw("Unknown LLM provider: {0}".format(provider))

    row = _provider_row(provider)
    base_url = ""
    timeout = 90
    temperature = None
    max_tokens = None

    if row:
        base_url = (getattr(row, "base_url", None) or "").strip()
        timeout = float(getattr(row, "timeout", None) or 90)
        temperature = getattr(row, "temperature", None)
        max_tokens = getattr(row, "max_tokens", None) or None

    if not base_url and provider in OPENAI_COMPATIBLE:
        base_url = DEFAULT_BASE_URLS.get(provider, "")

    return {
        "provider": provider,
        "base_url": base_url.rstrip("/"),
        "timeout": timeout,
        "temperature": temperature if temperature is not None else None,
        "max_tokens": int(max_tokens) if max_tokens else None,
        "_key": _resolve_api_key(provider, row),
    }


def get_default_model(provider: str | None = None) -> str:
    """Return the default model for a provider (or the Settings default)."""
    settings = get_settings()
    provider = provider or getattr(settings, "default_provider", None) or "Anthropic"
    row = _provider_row(provider)
    if row and getattr(row, "default_model", None):
        return row.default_model
    if provider == "Anthropic" and getattr(settings, "default_model", None):
        return settings.default_model
    return DEFAULT_MODELS.get(provider, "claude-sonnet-4-5")


# Normalised response wrapper ----------------------------------------------
class _Block:
    """Mimics an Anthropic content block (type/text/name/input/id)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Usage:
    def __init__(self, input_tokens=0, output_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(self, content, usage, stop_reason):
        self.content = content
        self.usage = usage
        self.stop_reason = stop_reason


class UnifiedClient:
    """Provider-agnostic client exposing .messages.create(...)."""

    def __init__(self, provider: str | None = None):
        self.provider = provider or resolve_provider_config()["provider"]
        self.cfg = resolve_provider_config(self.provider)
        if not self.cfg["_key"] and self.provider != "Ollama":
            frappe.throw(
                "No API key configured for provider '{0}'. Set it in FlowAgent "
                "Settings.".format(self.provider)
            )

        self.messages = self

    def create(self, *, model, messages, system=None, tools=None,
                        max_tokens=1024, temperature=None):
        if self.provider == "Anthropic":
            return self._anthropic(model, messages, system, tools, max_tokens, temperature)
        return self._openai_compatible(model, messages, system, tools, max_tokens, temperature)

    # -- Anthropic ---------------------------------------------------------
    def _anthropic(self, model, messages, system, tools, max_tokens, temperature):
        try:
            from anthropic import Anthropic
        except ImportError:
            frappe.throw("The 'anthropic' Python package is not installed.")
        client = Anthropic(api_key=self.cfg["_key"], timeout=self.cfg["timeout"])
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = _to_anthropic_tools(tools)
        if temperature is not None:
            kwargs["temperature"] = temperature
        return client.messages.create(**kwargs)

    # -- OpenAI-compatible -------------------------------------------------
    def _openai_compatible(self, model, messages, system, tools, max_tokens, temperature):
        import requests

        payload = _to_openai_payload(model, messages, system, tools, max_tokens, temperature)
        url = self.cfg["base_url"] + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.cfg["_key"]:
            headers["Authorization"] = "Bearer " + self.cfg["_key"]

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.cfg["timeout"])
        except requests.exceptions.Timeout:
            frappe.throw("LLM request timed out after {0}s (provider: {1}).".format(self.cfg["timeout"], self.provider))
        except requests.exceptions.RequestException as e:
            frappe.throw("LLM request failed ({0}): {1}".format(self.provider, e))

        if resp.status_code != 200:
            _raise_http_error(resp, self.provider, model)

        data = resp.json()
        return _from_openai_response(data)


# -- Anthropic<->OpenAI tool/message translation ---------------------------
def _to_anthropic_tools(tools):
    """Accept tools in either Anthropic (input_schema) or OpenAI (parameters) shape."""
    out = []
    for t in tools or []:
        if "input_schema" in t:
            out.append(t)
        else:
            fn = t.get("function", t)
            out.append({
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
    return out


def _to_openai_tools(tools):
    out = []
    for t in tools or []:
        if "function" in t:
            out.append(t)
        else:
            out.append({
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            })
    return out


def _to_openai_payload(model, messages, system, tools, max_tokens, temperature):
    """Translate Anthropic-style messages into an OpenAI chat payload."""
    oai_messages = []
    if system:
        oai_messages.append({"role": "system", "content": system})

    for m in messages or []:
        role = m.get("role")
        content = m.get("content")

        if isinstance(content, str):
            oai_messages.append({"role": role, "content": content})
            continue

        text_parts = []
        tool_calls = []
        tool_results = []
        for block in content or []:
            btype = getattr(block, "type", None)
            if btype is None and isinstance(block, dict):
                btype = block.get("type")
            if btype == "text":
                text_parts.append(block.text if hasattr(block, "text") else block.get("text", ""))
            elif btype == "image":
                text_parts.append(_image_to_openai_part(block))
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.id if hasattr(block, "id") else block.get("id"),
                    "type": "function",
                    "function": {
                        "name": block.name if hasattr(block, "name") else block.get("name"),
                        "arguments": json.dumps(block.input if hasattr(block, "input") else block.get("input", {})),
                    },
                })
            elif btype == "tool_result":
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": (block.tool_use_id if hasattr(block, "tool_use_id") else block.get("tool_use_id")),
                    "content": (block.content if hasattr(block, "content") else block.get("content", "")),
                })

        if tool_calls:
            oai_messages.append({
                "role": "assistant",
                "content": "".join(text_parts) or None,
                "tool_calls": tool_calls,
            })
        elif tool_results:
            for tr in tool_results:
                oai_messages.append(tr)
        else:
            oai_messages.append({"role": role, "content": text_parts if text_parts else None})

    payload = {"model": model, "messages": oai_messages}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = _to_openai_tools(tools)
    if temperature is not None:
        payload["temperature"] = temperature
    return payload


def _image_to_openai_part(block):
    """Convert an Anthropic image block to an OpenAI image_url content part."""
    source = block.source if hasattr(block, "source") else block.get("source", {})
    stype = source.get("type")
    if stype == "base64":
        mt = source.get("media_type", "image/jpeg")
        return {"type": "image_url", "image_url": {"url": "data:{0};base64,{1}".format(mt, source.get("data"))}}
    if stype == "url":
        return {"type": "image_url", "image_url": {"url": source.get("url")}}
    if isinstance(source, str):
        return {"type": "image_url", "image_url": {"url": source}}
    return {"type": "text", "text": "[image]"}


def _from_openai_response(data):
    """Normalise an OpenAI chat completion into the Anthropic response shape."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    blocks = []

    text = msg.get("content")
    if text:
        blocks.append(_Block(type="text", text=text))

    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        blocks.append(_Block(type="tool_use", id=tc.get("id"), name=fn.get("name"), input=args))

    usage = data.get("usage") or {}
    finish = choice.get("finish_reason")
    stop_reason = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}.get(finish, "end_turn")

    return _Response(
        content=blocks,
        usage=_Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
        stop_reason=stop_reason,
    )


def _raise_http_error(resp, provider, model):
    body = ""
    try:
        body = resp.text[:500]
    except Exception:
        pass
    code = resp.status_code
    if code == 401:
        frappe.throw("Authentication failed for provider '{0}'. Check the API key in FlowAgent Settings.".format(provider))
    if code == 404 and "model" in body.lower():
        frappe.throw("Provider '{0}' does not recognise model '{1}'.".format(provider, model))
    frappe.throw("LLM API error {0} from {1}: {2}".format(code, provider, body))


def test_connection(provider: str, model: str | None = None) -> dict:
    """Fire a tiny completion to verify credentials/connectivity."""
    client = UnifiedClient(provider)
    model = model or get_default_model(provider)
    resp = client.messages.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        max_tokens=8,
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    return {"success": True, "model": model, "reply": text}

# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import json



class FlowAgentSettings(Document):
    """Singleton holding API keys, model defaults, and run policy."""

    def validate(self):
        if not self.webhook_secret:
            self.webhook_secret = frappe.generate_hash(length=32)
        if self.max_steps_per_run and self.max_steps_per_run < 1:
            frappe.throw("Max steps per run must be at least 1")
        if self.max_agent_iterations and self.max_agent_iterations < 1:
            frappe.throw("Max agent iterations must be at least 1")


@frappe.whitelist()
def regenerate_webhook_secret():
    """Mint a new webhook secret. Existing webhook URLs will stop working."""
    frappe.only_for("System Manager")
    settings = frappe.get_single("FlowAgent Settings")
    settings.webhook_secret = frappe.generate_hash(length=32)
    settings.save(ignore_permissions=True)
    return settings.webhook_secret



@frappe.whitelist()
def get_providers():
    """List configured providers with masked keys (never the real key)."""
    frappe.only_for("System Manager")
    settings = frappe.get_single("FlowAgent Settings")
    out = []
    for row in settings.providers or []:
        out.append({
            "provider": row.provider,
            "label": row.label or row.provider,
            "enabled": bool(row.enabled),
            "default_model": row.default_model or "",
            "base_url": row.base_url or "",
            "has_key": bool(row.get_password("api_key", raise_exception=False)),
        })
    out.append({"provider": "default_provider", "value": settings.default_provider or "Anthropic"})
    return out


@frappe.whitelist()
def test_provider_connection(provider, model=None):
    """Verify credentials/connectivity for a provider. Never returns the key."""
    frappe.only_for("System Manager")
    from flowagent.engine.llm import test_connection
    try:
        return test_connection(provider, model)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "FlowAgent Provider Test")
        return {"success": False, "error": str(e)}


def get_anthropic_key() -> str:
    """Resolve the Anthropic API key, falling back to site_config / env."""
    settings = frappe.get_single("FlowAgent Settings")
    key = settings.get_password("anthropic_api_key", raise_exception=False)
    if key:
        return key
    # Allow site_config override for ops who don't want it in the DB
    site_key = frappe.conf.get("anthropic_api_key")
    if site_key:
        return site_key
    import os
    return os.environ.get("ANTHROPIC_API_KEY", "")


def get_default_model() -> str:
    settings = frappe.get_single("FlowAgent Settings")
    return settings.default_model or "claude-sonnet-4-5"

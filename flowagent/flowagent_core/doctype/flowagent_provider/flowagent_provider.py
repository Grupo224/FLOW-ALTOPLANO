# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class FlowAgentProvider(Document):
    """One row in the FlowAgent Settings providers child table."""

    def validate(self):
        if self.timeout and int(self.timeout) < 1:
            frappe.throw("Timeout must be at least 1 second")
        if self.temperature is not None and not (0 <= float(self.temperature) <= 2):
            frappe.throw("Temperature must be between 0 and 2")

# Azure Operations Agent — TODO

## 1. Send Email via Logic Apps or Azure Monitor Alerts

**Current state:** Email tools (`mcp_server/email_tools.py`) simulate sending — they log the payload and return a confirmation but don't actually deliver email.

**Goal:** Integrate a real email delivery mechanism.

### Option A: Azure Logic Apps
- [ ] Create a Logic App with an HTTP trigger that accepts a JSON payload (recipient, subject, body, attachments)
- [ ] Configure the Logic App to send email via Office 365 Connector or SendGrid connector
- [ ] Add `LOGIC_APP_WEBHOOK_URL` environment variable to MCP server config
- [ ] Update `send_resource_email` and `send_custom_email` in `email_tools.py` to POST to the Logic App webhook
- [ ] Handle Logic App response status and relay success/failure to the agent
- [ ] Add retry logic with exponential backoff for transient failures

### Option B: Azure Monitor Action Groups
- [ ] Create an Action Group with email notification actions
- [ ] Create or use existing alert rules that trigger the Action Group
- [ ] Add MCP tool `create_alert_action_group` to programmatically create action groups
- [ ] Add MCP tool `trigger_alert_notification` to send one-off notifications via action group
- [ ] Support both subscription-owner and custom-recipient email targets

### Option C: Microsoft Graph Send Mail API
- [ ] Add `Mail.Send` delegated permission to the Entra ID App Registration
- [ ] Update `send_resource_email` to call `POST /me/sendMail` via Microsoft Graph
- [ ] Format email body as HTML with resource tables and findings
- [ ] Handle token scope — may need a separate Graph token (`https://graph.microsoft.com/.default`)

### Shared tasks
- [ ] Add email delivery status tracking (sent, failed, pending)
- [ ] Add unit tests for email tools with mocked HTTP endpoints
- [ ] Update README with email configuration instructions

---

## 2. Actionable Policy Agent

**Current state:** The Policy Agent can list assignments, check compliance, and generate policy definition JSON + CLI commands, but it does not create/assign/delete policies directly.

**Goal:** Make the Policy Agent capable of performing write operations on Azure Policy.

### Policy creation & assignment
- [ ] Add MCP tool `create_policy_definition` — calls `PUT /providers/Microsoft.Authorization/policyDefinitions/{name}` with the generated JSON
- [ ] Add MCP tool `create_policy_assignment` — calls `PUT /providers/Microsoft.Authorization/policyAssignments/{name}` at a given scope
- [ ] Add MCP tool `delete_policy_assignment` — removes a policy assignment by name/scope
- [ ] Add MCP tool `update_policy_assignment` — modify parameters or enforcement mode of an existing assignment

### Remediation
- [ ] Add MCP tool `create_remediation_task` — triggers remediation for non-compliant resources under a policy assignment
- [ ] Add MCP tool `get_remediation_status` — check progress of a remediation task
- [ ] Add MCP tool `list_non_compliant_resources` — return resources that are non-compliant for a given policy

### Safety & confirmation
- [ ] Implement a confirmation flow — agent asks user to confirm before executing any write operation (create/assign/delete)
- [ ] Add `--dry-run` mode that generates the ARM request but doesn't execute it
- [ ] Log all policy write operations with user OID, timestamp, and scope for audit trail

### Agent instructions
- [ ] Update `POLICY_AGENT_INSTRUCTIONS` in `azure_ops_orchestrator.py` to describe the new actionable tools
- [ ] Add examples for "assign this policy to my subscription" and "remediate non-compliant resources"

---

## 3. Improve UI for Displaying Policy with JSON Formatting

**Current state:** Policy definitions and compliance data are returned as plain text in chat bubbles. JSON is not syntax-highlighted or collapsible.

**Goal:** Render policy JSON with proper formatting, syntax highlighting, and copy-to-clipboard.

### JSON rendering component
- [ ] Create a `JsonViewer` React component with collapsible tree view
- [ ] Add syntax highlighting for JSON (use a lightweight library like `react-json-view-lite` or custom CSS)
- [ ] Add copy-to-clipboard button for the full JSON blob
- [ ] Add copy button for individual CLI commands in code blocks

### Markdown code block handling
- [ ] Detect fenced code blocks (` ```json ` / ` ```bash `) in agent responses
- [ ] Render JSON blocks with the `JsonViewer` component
- [ ] Render bash/shell blocks with syntax highlighting and a copy button
- [ ] Support collapsible sections for large policy definitions (>20 lines)

### Policy-specific UI
- [ ] Display policy compliance as a colored badge (compliant = green, non-compliant = red, exempt = gray)
- [ ] Show policy effect (Deny, Audit, DeployIfNotExists, etc.) as a styled tag
- [ ] Add a "Deploy this Policy" button next to generated policy definitions (triggers the actionable policy agent from TODO #2)
- [ ] Render policy assignment list as a sortable table instead of plain text

### General chat improvements
- [ ] Add a "Copy" button to each assistant message bubble
- [ ] Improve rendering of markdown tables in chat responses
- [ ] Support expandable/collapsible long messages

---

## 4. Add Actions for Handling Unused and Orphaned Resources

**Current state:** The agent can *detect* orphaned/unused/idle resources via `find_orphaned_resources` and `check_idle_resources`, but cannot take action on them. Users must go to the Azure Portal to clean up.

**Goal:** Let the agent recommend and execute cleanup actions for unused resources.

### Cleanup action tools (MCP server)
- [ ] Add MCP tool `delete_resource` — delete a resource by resource ID (with confirmation)
- [ ] Add MCP tool `deallocate_vm` — already exists as `vm_power_operation(action="deallocate")`, verify it works for this flow
- [ ] Add MCP tool `delete_unattached_disk` — delete an unattached managed disk
- [ ] Add MCP tool `delete_orphaned_nic` — delete a NIC not attached to any VM
- [ ] Add MCP tool `delete_orphaned_public_ip` — release and delete an unassociated public IP
- [ ] Add MCP tool `delete_orphaned_nsg` — delete an NSG not attached to any subnet or NIC
- [ ] Add MCP tool `resize_resource` — resize a VM or App Service Plan to a lower SKU
- [ ] Add MCP tool `stop_app_service` — stop an idle App Service
- [ ] Add MCP tool `delete_empty_resource_group` — delete a resource group with zero resources

### Batch operations
- [ ] Add MCP tool `bulk_delete_resources` — accept a list of resource IDs and delete them sequentially with status reporting
- [ ] Add progress notifications via MCP `notifications/progress` for batch operations
- [ ] Generate a cleanup summary report after batch operations complete

### Safety & confirmation
- [ ] Require explicit user confirmation before any delete operation — the agent must list what will be deleted and get a "yes"
- [ ] Add a `--dry-run` mode that shows what WOULD be deleted without executing
- [ ] Tag resources with `ScheduledForDeletion: <date>` before actual deletion (grace period)
- [ ] Skip resources with `DoNotDelete` or `ProtectedResource` tags
- [ ] Log all delete operations with user OID, resource ID, and timestamp

### Agent workflow integration
- [ ] Update `AZURE_OPS_INSTRUCTIONS` to describe cleanup actions and confirmation flow
- [ ] Add recommended action suggestions: when reporting unused resources, include "Would you like me to clean these up?"
- [ ] After cleanup, re-run `find_orphaned_resources` and generate an updated report showing savings
- [ ] Calculate estimated monthly cost savings from cleanup actions

### Report enhancements
- [ ] Add an "Actions" column to the orphaned resources report with Delete/Resize/Stop buttons
- [ ] Add a "Select All" checkbox for batch cleanup from the report UI
- [ ] Show before/after cost comparison in the cleanup summary report

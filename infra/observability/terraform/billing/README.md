# M11 financial source binding contract

This isolated provider-free module has no backend, resource, data source, IAM,
export toggle or scheduler. fmt/validate need no cloud provider/state. It renders
reviewable source-controlled SQL, following the existing sidecar contract pattern.
No source project/account/destination is selected. Applying this module is not a
billing integration or authorization to create objects. Supported BigQuery resource
promotion must be added against the actual owner/state only after exact operation
review; no guessed production dataset is created in this milestone.

Future activation: inspect existing destinations and actual schemas/partitioning;
review account/project/location identity/query byte caps; propose exact dataset/views,
IAM, migrations and worker deployment individually under AGENTS.md's ten-field gate.
Billing-export bootstrap may require a separate approved Cloud Billing action.
Rollback disables source activation/uses compatible views while retaining billing
history, generation manifests and source state. No destructive retention here.

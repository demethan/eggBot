# Production Fry capability report

Audit time: 2026-08-01 (UTC)

The non-mutating audit authenticated with credentials from the production `data.json`. No endpoint, password, or token is included here.

| Server | Authentication | Metadata | Whitelist read route | Whitelist write role |
| --- | --- | --- | --- | --- |
| BACON | Passed | Supported | Supported; probe user absent | Supported |
| BAGEL | Passed | Supported | Supported; probe user absent | Supported |
| DONUT | Passed | Supported | Supported; probe user absent | Supported |
| GRAVY | Passed | Supported | Supported; probe user absent | Supported |
| PIZZA | Passed | Supported | Supported; probe user absent | Supported |

Whitelist write capability was verified by submitting an intentionally invalid empty form. Every server returned Fry's validation error after authorization and before whitelist mutation. No whitelist entry was added, removed, or reloaded.

The protected raw report and production archive are stored outside the repository under `/home/demethan/backups/eggbot/20260801T201949Z/` with owner-only permissions.

Service-manager status and the production virtual environment cannot be inspected from the development sandbox. Those checks remain required from the host during staging and rollout.

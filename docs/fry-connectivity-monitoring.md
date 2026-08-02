# Fry connectivity monitoring

EggBot treats each five-minute Fry metadata request as a health check. Requests have a
15-second total timeout. A single failed request is recorded but does not notify
admins. Two consecutive failures for a server open a durable incident and notify the
configured admin channel while pinging `applicationReviewerRoleID`.

The alert is classified as a **possible WireGuard connection outage** only when every
enabled Fry endpoint has crossed the failure threshold together. A partial failure is
reported as a Fry API connectivity problem because EggBot cannot distinguish a single
server failure from a routing or service problem without additional network access.

One Discord alert is used for the incident. EggBot edits it when the affected server
set changes instead of posting every five minutes. When all Fry endpoints recover,
EggBot sends one recovery message with the approximate outage duration. Failed alert
or recovery delivery is retried during later polls, and SQLite incident state prevents
restarts from duplicating notifications.

Discord timestamps use `<t:...:F>` and `<t:...:R>`, so every admin sees the event in
their own configured timezone along with a relative time. SQLite and logs remain UTC.

The durable tables are `fry_health_state` and `fry_connectivity_incidents`. The
collector stores categorized HTTP, timeout, connection, authentication, and response
errors without storing credentials or bearer tokens in health records.

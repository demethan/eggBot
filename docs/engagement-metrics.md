# Engagement and pack refresh metrics

The admin reports retain raw activity totals and add a participation score that gives
diminishing credit to additional hours from the same player:

```text
participation score = Σ √(each player's hours in the period)
```

This is deliberately not called player-hours. Four players who each play one hour
produce four person-hours and a participation score of `4.0`. One player who plays
four hours also produces four person-hours, but a participation score of `2.0`. Raw
person-hours remain visible so the score never hides actual playtime.

For every current server/pack installation, EggBot reports:

- unique active players over 7, 14, and 28 days;
- total person-hours over each window;
- participation score over each window;
- last recorded activity using a Discord-localized timestamp;
- pack refresh status.

Refresh status uses current-pack sessions and the duration for which EggBot has
observed that installation:

- **Active:** at least one player in the last 14 days.
- **Refresh watch:** no players in 14 days, but activity exists within 28 days.
- **Refresh candidate:** no players in 28 days and at least 28 days of tracking data.
- **Insufficient history:** no qualifying activity, but less than 28 days of tracking
  exists for the current installation.
- **No current pack history:** Fry has not supplied a current installation.

The status is advisory. It does not automatically remove or replace a pack.

`!serverstats` and `!packstats` include a compact seven-day engagement summary in the
global admin-channel view. Supplying a server sends the complete 7/14/28-day breakdown
and refresh state to the requesting admin by DM. Existing `week`, `month`, `year`, and
`all` arguments still control the raw totals shown alongside engagement metrics.

Statistics begin when EggBot starts tracking Fry metadata; missing historical data is
never inferred.

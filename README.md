# Daily Activity for Home Assistant

Daily Activity is a custom Home Assistant integration that keeps daily transition counts and state durations for dashboards and heatmaps. Each instance creates one sensor. Its value is today's transition count, and its `data` attribute contains retained daily records.

Two source modes use the same sensor and dashboard format:

| Mode | Input | Duration tracking |
| --- | --- | --- |
| `binary_sensors` | One or more Home Assistant binary sensors | Optional; disabled by default |
| `reported_intervals` | Timestamped intervals submitted by an external producer | Always enabled for reported observations |

Existing entries without `source_mode` continue in `binary_sensors` mode. The existing configuration key is **`transition_direction`**, with values **`off_to_on`** (default) and **`on_to_off`**. Source modes cannot be switched through options; create a separate instance for a different mode. No counter helper, counting automation, midnight-reset automation, external collector, or MQTT integration is included or required by this integration.

## Contents

- [Installation](#installation)
- [Configure binary-sensor activity](#configure-binary-sensor-activity)
- [Configure a reported-interval source](#configure-a-reported-interval-source)
- [Sensor data and latest activity](#sensor-data-and-latest-activity)
- [Counts and duration accounting](#counts-and-duration-accounting)
- [HTTP producer contract](#http-producer-contract)
- [Storage, retention, and restart behavior](#storage-retention-and-restart-behavior)
- [Dashboards and heatmaps](#dashboards-and-heatmaps)
- [Internet outage example](#internet-outage-example)
- [Troubleshooting](#troubleshooting)
- [Development and tests](#development-and-tests)

## Installation

### Manual installation

Copy `custom_components/daily_activity/` into `/config/custom_components/daily_activity/` on the Home Assistant system, then restart Home Assistant to load the Python code. Preserve existing integration storage and configuration when updating.

Go to **Settings → Devices & services → Add integration → Daily Activity**. Select the source mode, then complete its settings. Use the resulting sensor's actual entity ID in dashboard examples; display names and entity IDs are separate.

The integration registers its HTTP endpoint and its entity-settings editor when loaded. The HTTP endpoint stays closed to requests that do not provide a valid source-specific credential, even though it uses custom authentication rather than HA's general bearer-token mechanism.

### HACS

Add `https://github.com/jmikeschultz/ha_daily_activity` as a HACS custom repository with category **Integration**, install it, and restart Home Assistant. The repository includes `hacs.json` and the custom-component layout. HACS installs the code available in the remote repository; uncommitted local changes are not available through HACS.

Zen UI and Zen UI Extensions are separate dashboard cards with their own frontend resources. Installing Daily Activity does not install either card.

## Configure binary-sensor activity

Select **Home Assistant binary sensors** and configure:

| Setting | Meaning |
| --- | --- |
| Name | Display name of this Daily Activity sensor |
| Binary sensors | One or more source `binary_sensor` entities |
| Transition direction | OFF → ON (`off_to_on`) or ON → OFF (`on_to_off`) |
| Time zone | Valid IANA name, such as `America/New_York` |
| Track daily state durations | Optional, initially disabled |
| Days to retain | Whole number from 30 to 2000; default 400 |

Counts track each source independently. Only the exact configured `off`/`on` transition counts. Initial states, restored placeholders, `unknown`, `unavailable`, removed entities, and same-state updates do not count. Recovery from an unknown state establishes the next observed state without inventing a transition.

Edit these settings through the integration's **Options** form. Binary-sensor instances also support **Reconfigure**. Both keep the same configuration entry, sensor unique ID, and stored history. Binary-mode changes apply to future observations; changing the transition direction does not recalculate old counts or clear the last matching activity.

The integration's entity-settings editor adds **Edit monitored sensors** to a Daily Activity entity dialog. It opens the same options flow. If a Home Assistant frontend change prevents that button working, use the integration's options page directly.

### Add another binary source by action

The `daily_activity.add_source_entity` action appends a source to a binary-sensor instance and reloads that same entry. Re-adding an existing source does nothing. It preserves entry options and history.

```yaml
action: daily_activity.add_source_entity
data:
  daily_activity: sensor.door_activity
  source_entity: binary_sensor.back_door
```

This action does not apply to reported-interval instances. Remove or replace sources through options. Duration tracking uses the newly configured group from that point onward; changing sources or their polarity does not automatically reinterpret historical duration totals.

## Configure a reported-interval source

Select **Reported intervals** and configure:

- A display name, such as `Example activity`.
- A stable `source_id`, such as `example_source`.
- An IANA time zone.
- The transition direction.
- Retention days, using the same 30–2000 range and 400-day default.

A source ID is case-sensitive and must be unique across configured Daily Activity instances, including disabled ones. It contains 1–128 ASCII letters, digits, underscores, dots, colons, or hyphens, starting with a letter or digit. Producers use this ID in their requests. Display-name changes do not change it. Options cannot rename the ID or change the source mode.

After submitting settings, the flow generates a cryptographically random 256-bit ingestion token and shows it on a **Copy the ingestion token** screen. Copy it into the producer's secret storage, then submit to finish creating the entry. The token becomes usable when the source loads successfully. Cancelling initial creation leaves no active source.

### Issue, rotate, and revoke credentials

Open the reported source's **Options** form. It can edit the name, time zone, transition direction, and retention days, and offers these token actions:

| Action | Effect |
| --- | --- |
| Keep current token | Keeps the credential unchanged and does not display it |
| Issue / rotate token | Durably replaces the credential, immediately invalidates the previous token, and displays the new token once in the issuance flow |
| Revoke token | Durably disables ingestion until a new token is issued |

Rotation and revocation take effect when their settings are submitted, before the final settings-confirmation screen. Cancelling after rotation does not restore the previous token. If the token was not copied, rotate again; existing plaintext tokens cannot be recovered or displayed later. Token management is available through Home Assistant's administrative configuration/options UI, not through the producer API.

Only SHA-256 hashes of the random tokens are persisted. Verification uses a constant-time hash comparison. The initial configuration's bootstrap hash is removed after the authoritative reported ledger has been created. Revocation is retained across restarts; a missing or corrupt initialized ledger fails closed instead of recreating the bootstrap credential. Tokens are never placed in activity entity attributes, ingestion responses, or integration logs.

Changing the time zone or transition direction in reported mode **recalculates retained reported history** from its timestamped ledger. Expanding retention cannot recover events already pruned. Reconfiguration routes reported sources to their options flow. The entity-settings editor displays the stable source ID and an **Edit source settings / token** button for these instances.

## Sensor data and latest activity

The sensor value is today's count in the instance's configured time zone. It resets at local midnight without adding an event. Daily records are sorted by date. A duration-only day has `count: 0`.

```yaml
data:
  - date: "2026-10-10"
    count: 4
  - date: "2026-10-11"
    count: 1
    on_seconds: 85500.0
    off_seconds: 900.0
    unknown_seconds: 0.0
```

Count-only records remain count-only. Seconds can be fractional. A missing duration field means **not recorded**, not zero. Do not assume that a missing day means a fully observed day without activity.

Binary-sensor attributes retain their existing contract:

```yaml
source_entities:
  - binary_sensor.front_door
  - binary_sensor.back_door
transition_direction: off_to_on
last_activity_entity: binary_sensor.front_door
last_activity_name: Front door
last_activity: "2026-10-12T07:23:07.294544-04:00"
last_activity_display: "Front door — Oct 12, 7:23 AM"
time_zone: America/New_York
```

Reported sources expose the same `data`, `transition_direction`, `time_zone`, and latest-activity attributes, plus:

```yaml
source_mode: reported_intervals
source_id: example_source
source_entities: []
last_activity_entity: null
last_activity_name: Example activity
last_activity: "2026-10-12T11:30:00-04:00"
last_activity_display: "Example activity — Oct 12, 11:30 AM"
```

For reported sources, `last_activity` is the newest proven matching transition's observation time, not the time its batch arrived. `last_activity_entity` is null because there is no HA source entity; `last_activity_name` uses the configured display name. Older backfills do not supersede a newer retained transition. If pruning removes all matching observations, the previous last-activity timestamp remains available. The name follows later display-name edits.

With no matching activity, the timestamp, entity, and name are null and `last_activity_display` is `No activity recorded yet`. Latest-activity information is persisted.

## Counts and duration accounting

### Binary sources

When duration tracking is enabled, the combined state is:

- **ON:** at least one configured source is ON.
- **OFF:** every configured source is OFF.
- **UNKNOWN:** no source is ON, and at least one source is missing, restored, unknown, unavailable, or otherwise cannot establish a known OFF state.

Overlapping ON periods are measured once. If one door opens for 30 seconds and another opens during its final 10 seconds, the combined ON duration is 30 seconds; the count can still be two opening transitions.

Ongoing intervals update in the entity attributes every minute and on source changes. Enabling tracking begins with current observations and does not backfill old records. Disabling it stops accumulation while preserving recorded durations.

### Reported sources

Each event describes a single observed state over `[start, end)`. The start is included; the end is excluded. Events must not overlap, even if they report the same state under different IDs. Exactly touching intervals are permitted.

A transition is counted only when two accepted intervals are adjacent in chronological order, the earlier interval's end equals the later interval's start exactly, and their states establish the configured direction. The count belongs to the later interval's start date in the configured time zone.

For example, with `off_to_on`:

```text
OFF [09:00, 10:00) + ON [10:00, 11:00) = one transition at 10:00
OFF [09:00, 10:00) + ON [10:01, 11:00) = no proven transition
UNKNOWN [09:00, 10:00) + ON [10:00, 11:00) = no proven transition
An isolated ON interval = duration, without a transition count
```

The same rule applies in reverse for `on_to_off`. Adjacent intervals in the same state do not count. Event delivery order does not matter: a late interval can establish a previously unproven transition, and retained aggregates are recalculated without adding duplicate counts. The API does not accept an explicit-transition flag in this version.

Only closed, reported intervals contribute time. Gaps are omitted; they are not silently classified as unknown. To measure a known indeterminate observation period, explicitly submit state `unknown`. No time is extrapolated past `end` or inferred before `start`. A producer may report intervals observed while Home Assistant was offline after it restarts.

### Calendar boundaries and partial days

Both modes use elapsed UTC seconds and split intervals at midnight in the configured IANA time zone. Daylight saving days can contain 23 or 25 hours. An interval spanning midnight contributes duration to each day, while a proven transition counts only on the day it occurred.

A day can have zero transitions and many hours of ON or OFF time if that state continued from yesterday or tracking began while it was already in that state. Daily durations need not total 24 hours: partial observation, gaps, disabled tracking, restarts, retention clipping, and DST all affect coverage. Duration totals describe a day, not the length of each counted event.

## HTTP producer contract

### Endpoint and authentication

```text
POST /api/daily_activity/events
Content-Type: application/json
Authorization: Bearer <source-specific-ingestion-token>
```

The view is registered through Home Assistant's HTTP mechanisms. It performs mandatory custom bearer authentication before parsing the body or revealing source information. A general-purpose HA long-lived access token does not authorize this endpoint. Each ingestion token authorizes exactly one loaded reported source.

The API is write-only. Producers cannot retrieve history, configuration, other source IDs, tokens, or sensor attributes. GET is unsupported. A valid token cannot submit to another source. Mixed-source requests are rejected as a whole with 403 before anything is saved; separate sources require separate requests and credentials. Missing, invalid, rotated, revoked, disabled, or unloaded credentials receive the same generic 401 response.

### Schema and limits

The top-level object contains exactly `events`, a nonempty array. Every event contains exactly these required fields:

| Field | Contract |
| --- | --- |
| `source` | Registered, stable `source_id`, matching the bearer credential |
| `event_id` | Case-sensitive string unique within that source; same 1–128-character syntax as source IDs |
| `state` | Exactly `on`, `off`, or `unknown` |
| `start` | ISO 8601 timestamp with `T`, hours/minutes/seconds, optional 1–6 fractional digits, and explicit `Z` or `±HH:MM` offset |
| `end` | Same timestamp format, strictly later than `start` |

Additional fields, naive timestamps, invalid dates, duplicate JSON keys, and non-JSON numeric values are rejected. Interval timestamps are normalized to UTC for identity and comparison; equivalent offset representations count as identical content.

| Limit | Value |
| --- | --- |
| Body size | 65,536 decoded bytes, enforced for fixed-length and streamed requests |
| Batch size | 100 events |
| Body read timeout | 10 seconds |
| Interval duration | Greater than zero and at most 31 × 86,400 elapsed seconds |
| Future intervals | `end` cannot be later than the server's current time |
| Retained ledger capacity | 10,000 interval records per source, including any retention-boundary witness |

A new interval ending at or before the current retention cutoff is rejected as expired. An interval crossing the cutoff may be accepted, with only retained-day portions included in daily totals. Split longer observations into adjacent intervals with distinct IDs. Splitting a sustained state does not create extra transitions.

### Example request

Copy the issued token into the environment without putting it directly into shell history:

```bash
read -r -s -p 'Ingestion token: ' INGESTION_TOKEN
printf '\n'
curl --fail-with-body --request POST \
  'https://homeassistant.example.internal:8123/api/daily_activity/events' \
  --header "Authorization: Bearer ${INGESTION_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{
  "events": [
    {
      "source": "example_source",
      "event_id": "142",
      "state": "off",
      "start": "2026-10-12T10:00:00-04:00",
      "end": "2026-10-12T11:30:00-04:00"
    }
  ]
}
JSON
unset INGESTION_TOKEN
```

Use the address reachable from the producer and your own observation timestamps and registered source ID. For an isolated interval, this records 5,400 OFF seconds on its observation date without assuming a transition occurred at its start.

### Acknowledgements and partial failures

Successful or duplicate-only batches return HTTP **200**:

```json
{"results":[{"index":0,"event_id":"142","status":"accepted","http_status":201}]}
```

Retransmitting the same normalized content under the same `(source_id, event_id)`:

```json
{"results":[{"index":0,"event_id":"142","status":"duplicate","http_status":200}]}
```

`accepted` means the interval, event ID, and recalculated aggregates were saved together before acknowledgement. `duplicate` means this exact content was already stored; it adds no count or duration. Responses do not return activity history.

If any event fails validation or conflicts, the batch returns HTTP **207 Multi-Status** with one indexed result per event. Valid events are still committed together. Reusing an ID with different content produces a per-event 409 conflict:

```json
{
  "results": [
    {"index":0,"event_id":"143","status":"accepted","http_status":201},
    {"index":1,"event_id":"142","status":"rejected","http_status":409,"error":"event_id_conflict"},
    {"index":2,"event_id":"144","status":"rejected","http_status":422,"error":"invalid_state"}
  ]
}
```

A batch containing only conflicts also returns 207, with 409 results; the top-level status is not 409. Overlapping intervals produce `interval_overlap` with per-event 409, even if the states agree. Validity is evaluated in request order against already stored intervals and earlier accepted members of that batch. Invalid event IDs are acknowledged as null rather than echoed as arbitrary objects.

Producers must inspect every result in a 207 response. Do not retry a conflict unchanged indefinitely: fix the producer's interval/identity model. Correct rejected invalid events before retrying them. A repeated delivery after a dropped connection is safe while its ID is retained. Across sources, the same event ID is independent.

### Whole-request errors

| HTTP status | Error | Meaning |
| --- | --- | --- |
| 400 | `invalid_json`, `invalid_batch`, `invalid_event` | Malformed JSON/envelope; no events processed |
| 401 | `invalid_credentials` | Missing or invalid scoped credential; no source details disclosed |
| 403 | `unauthorized_source` | Credential/source mismatch, missing source, or mixed-source batch; none processed |
| 408 | `request_timeout` | Body did not finish in time |
| 413 | `payload_too_large`, `batch_too_large` | Request limit exceeded |
| 415 | `content_type_must_be_json` | Wrong content type |
| 503 | `storage_error` | Persistence could not be confirmed; retry the entire batch |

Examples:

```json
{"error":"invalid_credentials"}
```

```json
{"error":"storage_error","retry":"entire_batch"}
```

Per-event 422 errors include `invalid_event_fields`, `invalid_event_id`, `invalid_state`, `invalid_timestamp`, `invalid_interval_order`, `interval_too_long`, `future_interval`, and `interval_expired`. Per-event 429 `source_capacity` means the bounded ledger is full. No token or exception text appears in these errors.

A failed write verification or a lost response may leave an atomic snapshot on disk even though no success reached the producer. Retry the whole batch using the same IDs and content; idempotency resolves that uncertainty. Concurrent requests, token rotation, revocation, and unload are serialized per source; authorization is checked again under the source lock before committing queued requests.

### Private-network deployment

Use a trusted private network, such as a Tailscale tailnet, between the producer and Home Assistant. Restrict reachability to the intended producers using network/firewall or tailnet policy. No public router port forwarding is required or recommended for ingestion.

Use HTTPS with a valid certificate when traffic is not already protected end-to-end by the private network. Match the certificate hostname to the URL. Do not disable certificate checks in production. Keep bearer tokens in producer secret storage, redact Authorization headers in proxy/debug logs, and rotate tokens when a producer is retired or a credential is exposed. The integration does not set up Tailscale, certificates, routers, or an external producer.

## Storage, retention, and restart behavior

Binary-mode history retains its existing `.storage/daily_activity.<entry_id>` file and format. Configuration entries and sensor unique IDs stay the same. No migration discards old counts or fabricates historical durations. Binary counts and last-activity data are persisted when matching transitions occur. Duration checkpoints save each minute, on source changes, and at midnight; clean unload/shutdown saves the final observed duration. An abrupt crash may lose time since the last successful checkpoint, normally up to one minute. Unobserved HA downtime is omitted rather than assigned to ON, OFF, or UNKNOWN.

Reported mode uses a separate private, versioned `.storage/daily_activity.<entry_id>.reported` Store snapshot. It contains normalized intervals indexed by event ID, the active token hash or revoked null credential, daily counts/durations, and the last-activity timestamp. The ledger is authoritative; aggregates are rebuilt at startup and after late events or relevant options changes. Writes are atomic, verified against disk, and fsynced before acknowledgement. Disk I/O and aggregation work run outside HA's event loop. A cancelled HTTP request cannot release a source's write lock before an in-progress commit finishes.

Retention is based on the instance's configured local date: the cutoff is midnight on **today minus retention days**. The cutoff date itself is retained, matching legacy behavior; this can include retention days plus the current date. Reported ledgers prune at startup, local midnight, and ingestion. Binary mode prunes at startup and activity updates; with durations enabled, checkpoints also prune it. Count-only binary helpers do not periodically rewrite storage solely to prune idle history.

Expired reported intervals and their deduplication metadata are removed together. An interval crossing the cutoff remains as a full ledger record while its earlier duration is excluded from aggregates. An interval ending exactly at the cutoff can be kept as a witness for a transition at the first retained midnight; it contributes no expired duration. Once those records are pruned, deduplication of their IDs cannot be guaranteed indefinitely. Replays entirely outside retention are rejected; reusing a pruned ID for a new retained interval can be accepted. Producers should keep event IDs globally unique within a source rather than recycling them.

Back up configuration and `.storage` together. Do not edit an active ledger manually. Missing or corrupt initialized reported storage prevents source setup; restore its backup rather than deleting files to make the error disappear. A source whose storage is full or unwritable cannot confirm new accepted events or new credentials. A failed token persistence confirmation does not display a new secret; resolve storage and issue/rotate again if necessary.

The 10,000-record cap bounds memory, disk, and recalculation work; high-frequency producers may reach it before the retention window ends. Aggregate rebuilds scan the retained ledger, so submission cost grows with retained records. Use appropriately sized contiguous observations, shorter retention, or adjust this implementation deliberately if a future workload needs more capacity. There is no indefinite deduplication archive, interval-update/delete API, read API, multi-source credential, or producer-side collector in this version.

## Dashboards and heatmaps

Inspect the sensor under **Developer Tools → States** to see its current value and `data`. The integration intentionally does not set `state_class`; Zen UI consumes the attribute directly rather than relying on HA statistics.

### Standard Zen UI card

```yaml
type: custom:zen-ui
card: heatmap
entity: sensor.door_activity
title: Door activity
days: 90
weekStartDay: sunday
weekdayLabels: letter
levelCount: 8
maxValue: 20
levelThresholds:
  - 0
  - 1
  - 3
  - 5
  - 8
  - 12
  - 16
```

The same configuration works with a reported-source sensor. Standard Zen UI shows counts; it does not automatically show the extra duration fields in its date details.

### Count and duration popup with Zen UI Extensions

Install and register the separate **Zen UI Extensions** frontend resource, keeping Zen UI installed. Change the card type to:

```yaml
type: custom:zen-ui-extended
card: heatmap
entity: sensor.example_activity
title: Example activity
days: 90
```

Its popup selects `off_seconds` for `on_to_off`, or `on_seconds` for `off_to_on`, and distinguishes missing duration data from zero. Heatmap colors remain based on count. An optional `duration_field: off_seconds` or `duration_field: on_seconds` overrides that selection. This wrapper is not bundled with Daily Activity; refer to the separate `zen-ui-extensions` repository for installation and its Zen UI version-specific date adapter.

### Heatmap with last activity and today's duration

```yaml
type: vertical-stack
cards:
  - type: custom:zen-ui
    card: heatmap
    entity: sensor.example_activity
    title: Example activity
    days: 90
  - type: markdown
    content: >-
      **Last:** {{ state_attr('sensor.example_activity', 'last_activity_display') }}

      {% set entity = 'sensor.example_activity' %}
      {% set today = now().date().isoformat() %}
      {% set rows = state_attr(entity, 'data') or [] %}
      {% set matches = rows | selectattr('date', 'eq', today) | list %}
      {% set field = 'off_seconds' if state_attr(entity, 'transition_direction') == 'on_to_off' else 'on_seconds' %}
      {% if matches and field in matches[0] %}
        {% set seconds = matches[0][field] | int %}
        **Time {{ 'OFF' if field == 'off_seconds' else 'ON' }} today:**
        {{ seconds // 3600 }}h {{ (seconds % 3600) // 60 }}m {{ seconds % 60 }}s
      {% else %}
        **Duration today:** Not recorded
      {% endif %}
```

This Markdown example uses Home Assistant's local date. Ensure its time zone matches the activity instance for a correct ‘today’ label. If they differ, select an explicit record date or use the heatmap's selected-day popup; HA's `now().date()` may refer to a different calendar day. The Markdown template shows a daily total, not an individual event duration. Choose the field that reflects your source's actual meaning. Inverted binary sensors reverse the ON/OFF meaning; prefer the original sensor with the appropriate transition direction when possible.

## Internet outage example

This is an example use of binary mode, not special behavior in the integration. Use a Ping connectivity binary sensor that is ON when its target is reachable and OFF when unreachable. Configure **ON → OFF**, enable durations, and use your local time zone. Keep the Ping **Host** as its IP address or resolvable hostname; change the entity's display name separately.

Each observed reachable-to-unreachable transition counts once. An outage lasting across midnight has a count on its starting day and OFF duration on both days. `off_seconds` measures observed failed reachability, `on_seconds` measures reachable time, and `unknown_seconds` measures indeterminate observations. Failed reachability may reflect the target or a network path rather than a complete Internet outage. Binary-mode downtime cannot reconstruct missed observations.

A fully observed ordinary day with one 15-minute outage could expose:

```yaml
data:
  - date: "2026-10-11"
    count: 1
    on_seconds: 85500
    off_seconds: 900
    unknown_seconds: 0
```

Use `sensor.internet_outages` (or your actual entity ID) with either heatmap example. The reported API stays generic and has no Ping, location, connectivity, MQTT, or monitoring-service assumptions.

## Troubleshooting

- **Only date and count appear:** binary duration tracking is disabled, or this is a historical record from before tracking started. Enable it in options; history is not backfilled.
- **Duration with zero count:** a state can continue across midnight or be present at initial observation. Counts measure transitions; durations measure occupancy.
- **Wrong apparent outage duration:** inspect the source's actual ON/OFF meaning, inversion, and `transition_direction`. Choose the matching duration field; changing sources does not relabel already accumulated binary-mode history.
- **Today's row is missing:** no retained observations exist for that day, or your template uses a different time zone. Reported sources do not extrapolate an ongoing state beyond their latest interval.
- **HTTP 401:** use the issued source-specific token, not an HA token. Check rotation/revocation and whether the source loaded successfully.
- **HTTP 403:** every event must name the credential's exact source ID. Do not mix sources in one request.
- **HTTP 207:** inspect individual results. Successful members were saved; only fix/retry rejected members, or safely retry identical accepted members while their IDs are retained.
- **No reported transition count:** an isolated interval, a gap, an unknown boundary, or same-state adjacency cannot establish the configured transition. Deliver its adjacent observation if one was actually recorded.
- **Storage error or source fails to load:** check available disk space, permissions, and backups. Do not erase the ledger or repeatedly replace IDs to bypass failures.
- **Custom element does not exist:** install the relevant frontend card and register its JavaScript module resource, then hard-refresh the whole browser page. An HA restart does not necessarily refresh an already-open browser's resource list.
- **Entity-dialog edit button fails:** use the integration's options flow; the frontend adapter depends on HA's entity-settings UI.

## Development and tests

Work in this repository. Deploy Python changes by copying the integration directory to Home Assistant and restarting only when deployment is intended. Preserve `.storage` and config entries. Frontend resources need browser refreshes after changes. No external producer or monitoring service is part of this repository.

Run the tests in a virtual environment:

```bash
python3 -m venv /tmp/daily-activity-tests-venv
/tmp/daily-activity-tests-venv/bin/pip install -r requirements-test.txt
/tmp/daily-activity-tests-venv/bin/python -m unittest discover -s tests -v
```

Tests execute the integration's Python code with mocked HA services/selectors/storage boundaries, real Voluptuous schemas, real temporary files, and real aiohttp HTTP requests. They cover legacy entries, both source modes and directions, options, stable/unique IDs, token issuance/rotation/revocation, authorization isolation, malformed and oversized requests, duplicates/conflicts/overlaps, partial batches, concurrent/cancelled submissions, durable-write failures, late aggregation, multiple sources, unknown periods, midnight/DST, retention, restarts, setup/unload, and the sensor attribute contract.

They do not run a full Home Assistant instance, its production HTTP middleware, or a dashboard browser. Home Assistant's supported HTTP view, options-flow, atomic Store, asynchronous static-path, and executor APIs are used; compatibility with older HA releases has not been validated. API behavior should be verified in a development HA instance before production deployment.

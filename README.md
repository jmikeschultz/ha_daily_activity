# Daily Activity for Home Assistant

A small custom Home Assistant integration for daily event heatmaps.

It counts **OFF → ON** transitions across one or more `binary_sensor` entities, groups them by calendar day in a configurable IANA time zone, and exposes the results in a form suitable for Zen UI heatmaps.

The integration also records the **most recent activity**, including which source sensor triggered and when it occurred.

No counter helper, counting automation, midnight reset automation, or template/Jinja history sensor is required.

## Sensor data

A Daily Activity sensor exposes a `data` attribute containing the retained daily counts:

```yaml
data:
  - date: "2026-09-23"
    count: 17
  - date: "2026-09-24"
    count: 6
```

It also exposes the configured source entities and most recent OFF → ON transition:

```yaml
source_entities:
  - binary_sensor.ambler_back_door
  - binary_sensor.ambler_sunroom_door
  - binary_sensor.ambler_front_door

last_activity_entity: binary_sensor.ambler_front_door
last_activity_name: Ambler front door
last_activity: "2026-10-05T07:24:33.652434-04:00"
last_activity_display: "Ambler front door — 7:24 AM"

time_zone: America/New_York
```

If no activity has occurred since last-activity tracking was introduced, the display attribute is:

```yaml
last_activity_display: "No activity recorded yet"
```

The last-activity information is persisted and survives Home Assistant restarts.

## Manual installation

Copy:

```text
custom_components/daily_activity/
```

to:

```text
/config/custom_components/daily_activity/
```

on the Home Assistant system.

Restart Home Assistant.

Then go to:

**Settings → Devices & services → Add integration → Daily Activity**

For each Daily Activity sensor, configure:

- a name
- one or more binary sensors
- an IANA time zone such as `America/New_York` or `America/Los_Angeles`
- retention period (default 400 days)

Each transition from a state other than `on` to `on` counts as one activity event.

## Development and deployment

The development source is maintained in this Git repository.

The deployed Home Assistant integration lives at:

```text
/config/custom_components/daily_activity/
```

During development, copy or `rsync`:

```text
custom_components/daily_activity/
```

to the Home Assistant installation:

```text
/config/custom_components/daily_activity/
```

Then restart Home Assistant so Python code changes are loaded.

Home Assistant should be treated as the deployment target; make source changes in this repository and commit them here.

## Zen UI heatmaps

Point Zen UI at the sensor created by this integration:

```yaml
type: custom:zen-ui
card: heatmap
entity: sensor.ambler_door_activity
title: Ambler Doors
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

The integration intentionally does **not** set `state_class`. Zen UI can therefore consume the live `data` attribute rather than waiting for Home Assistant statistics updates.

## Heatmap with latest activity

A Zen heatmap and its latest-activity display can be combined into a single Home Assistant `vertical-stack`.

This keeps the two cards together when arranging the dashboard.

Example:

```yaml
type: vertical-stack
cards:
  - type: custom:zen-ui
    card: heatmap
    entity: sensor.ambler_door_activity
    title: Ambler Doors
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

  - type: markdown
    content: >-
      **Last activity:** {{
      state_attr('sensor.ambler_door_activity',
      'last_activity_display') }}
```

The result is conceptually:

```text
┌─────────────────────────────┐
│        Ambler Doors         │
│                             │
│        Zen heatmap          │
│                             │
├─────────────────────────────┤
│ Last activity:              │
│ Ambler front door — 7:24 AM │
└─────────────────────────────┘
```

The Markdown card deliberately contains almost no presentation logic. Formatting, null handling, source identification, and timestamp formatting are handled by the `daily_activity` integration and exposed through `last_activity_display`.

This same vertical-stack pattern can be used for door activity, motion activity, doorbell dings, purifier outages, or any other Daily Activity sensor.

## HACS

The repository layout is HACS-ready.

Put the repository in GitHub, then add it to HACS as a custom **Integration** repository.

For active development, direct deployment from the development machine to Home Assistant is generally faster than performing a HACS release for every code change.

## Compatibility

Written conservatively for Home Assistant 2024.4+ APIs used by the target installation.

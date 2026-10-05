# Daily Activity for Home Assistant

A small custom integration for daily event heatmaps.

It counts **OFF → ON** transitions across one or more `binary_sensor` entities, groups them by calendar day in a configurable IANA time zone, and exposes a sensor with a Zen UI-compatible `data` attribute:

```yaml
data:
  - date: "2026-09-23"
    count: 17
  - date: "2026-09-24"
    count: 6
```

No counter helper, counting automation, midnight reset automation, or template/Jinja history sensor is required.

## Manual installation

Copy `custom_components/daily_activity` into `/config/custom_components/daily_activity` and restart Home Assistant.

Then go to **Settings → Devices & services → Add integration → Daily Activity**.

For each heatmap, choose:

- a name
- one or more binary sensors
- an IANA time zone such as `America/New_York` or `America/Los_Angeles`
- retention period (default 400 days)

Each OFF → ON transition counts as one event.

## Zen UI

Point Zen UI at the sensor created by this integration:

```yaml
type: custom:zen-ui
card: heatmap
entity: sensor.ambler_contact_openings
title: Ambler Contact Openings
days: 90
weekStartDay: sunday
weekdayLabels: letter
levelCount: 8
maxValue: 100
```

The integration intentionally does **not** set `state_class`; Zen UI can therefore consume the live `data` attribute rather than waiting for Home Assistant statistics updates.

## HACS

This repository layout is HACS-ready. Put it in a GitHub repository, then add that repository to HACS as a custom **Integration** repository.

## Compatibility

Written conservatively for Home Assistant 2024.4+ APIs used by the target installation.

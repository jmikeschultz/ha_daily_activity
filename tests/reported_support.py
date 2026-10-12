"""Shared reported-source fixtures, with real filesystem persistence."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from tests.support import integration, make_hass

NOW = datetime(2026, 10, 12, 20, tzinfo=timezone.utc)


class ReportClock(datetime):
    instant = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)


def reported_entry(source='example_source', direction='off_to_on', **changes):
    token, hashed = integration.reported.new_token()
    data = {'source_mode': 'reported_intervals', 'source_id': source,
            'ingestion_token_hash': hashed, 'time_zone': 'America/New_York',
            'retention_days': 400, 'transition_direction': direction, **changes}
    return SimpleNamespace(data=data, options={}, entry_id=f'entry-{source}',
                           title='Example activity', unique_id=source, domain='daily_activity'), token


def reported_hass(root):
    hass = make_hass()
    hass.config.path = lambda *parts: str(Path(root).joinpath(*parts))
    return hass


def interval(event_id='142', state='off', start='2026-10-12T10:00:00-04:00',
             end='2026-10-12T11:30:00-04:00', source='example_source'):
    return {'source': source, 'event_id': event_id, 'state': state, 'start': start, 'end': end}

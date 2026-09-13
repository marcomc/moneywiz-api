import os
import time
from datetime import datetime, timezone

import pytest

from moneywiz_api.utils import get_date, get_datetime


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="tzset is Unix-only")
def test_core_data_epoch_uses_utc_and_returns_naive_local_time(monkeypatch) -> None:
    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        assert get_datetime(0) == datetime(2000, 12, 31, 19, 0)
        summer = datetime(2024, 7, 1, 12, 0, tzinfo=timezone.utc)
        core_data_value = get_date(summer)
        assert get_datetime(core_data_value) == datetime(2024, 7, 1, 8, 0)
        assert get_date(get_datetime(core_data_value)) == core_data_value
    finally:
        if original_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original_tz)
        time.tzset()

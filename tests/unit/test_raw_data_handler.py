from moneywiz_api.model.raw_data_handler import RawDataHandler as RDH


def test_get_decimal():
    row = {"ZAMOUNT1": 1.23}
    assert str(RDH.get_decimal(row, "ZAMOUNT1")) == "1.23"


def test_get_nullable_decimal_none():
    row = {"ZORIG": None}
    assert RDH.get_nullable_decimal(row, "ZORIG") is None


def test_get_datetime_epoch():
    row = {"ZDATE1": 0.0}
    dt = RDH.get_datetime(row, "ZDATE1")
    # Apple epoch offset should yield a datetime around 2001-01-01
    assert dt.year == 2001


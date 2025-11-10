from pathlib import Path
from moneywiz_api.writes import WriteSession


def test_insert_deposit_preview(tmp_path: Path):
    # Use a copy of the test DB for apply-mode tests later; here we just dry-run
    db = Path(__file__).resolve().parents[3] / "tests/test_db.sqlite"
    session = WriteSession(db, dry_run=True)
    session.insert_syncobject(
        "DepositTransaction",
        {
            "ZACCOUNT2": 5309,
            "ZAMOUNT1": 12.34,
            "ZDATE1": 700000000,
            "ZDESC2": "Unit test deposit",
        },
    )
    plan = session.planned
    assert len(plan) == 1
    assert plan[0].sql.startswith("INSERT INTO ZSYNCOBJECT")
    assert "Z_ENT" in plan[0].sql and "ZGID" in plan[0].sql and "Z_OPT" in plan[0].sql


from conftest import moneywizApi


def test_loaded_managers_expose_accounted_source_rows() -> None:
    completeness = moneywizApi.completeness()

    for report in completeness.managers.values():
        assert report.source_count == report.parsed_count + len(report.skipped)
        assert len(report.source_ids) == report.source_count
        assert len(report.parsed_ids) == report.parsed_count

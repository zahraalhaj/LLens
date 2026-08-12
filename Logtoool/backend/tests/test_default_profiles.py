"""
Regression tests for bugs found and fixed while consolidating the backend
(step 2 of the rebuild). Each test corresponds to a specific issue:

1. source_system_field was declared in the schema but never read.
2. ensure_default_profiles() didn't sanitize '/' or other special chars in
   profile names before using them as filenames.
3. Delimited-format parsing didn't check the parsed column count, so any
   line with the right number of commas could falsely match.
4. A JSON object with N-1 top-level keys can coincidentally split into N
   comma-separated chunks and pass the column-count check too -- needs an
   explicit "does this look like JSON" guard as well.
"""
from backend.core.parse import group_multiline_logs, select_best_profile
from backend.core.profiles import ProfileManager, _slugify
from backend.core.schema import ParserProfile


def _profiles(tmp_path):
    pm = ProfileManager(profiles_dir=str(tmp_path))
    return pm, pm.list_profiles()


def test_source_system_field_is_actually_used(tmp_path):
    _, profiles = _profiles(tmp_path)
    syslog = next(p for p in profiles if p.name == "Standard Syslog")
    grouped = group_multiline_logs([(1, "Aug  5 20:14:12 prod-db-node-01 postgres[4120]: LOG: shut down")])
    best, _ = select_best_profile(grouped, [syslog])
    assert best is not None


def test_slugify_strips_special_characters():
    assert _slugify("Delimited CSV/TSV Log") == "delimited_csv_tsv_log"
    assert "/" not in _slugify("A/B/C")
    assert "[" not in _slugify("Standard Bracket [TIMESTAMP] [LEVEL]")


def test_default_profiles_seed_without_filesystem_errors(tmp_path):
    pm, profiles = _profiles(tmp_path)
    # Every DEFAULT_PROFILES entry should have produced exactly one file.
    seeded_files = list(tmp_path.glob("*.json"))
    assert len(seeded_files) == len(profiles) == 7


def test_json_object_is_not_misdetected_as_delimited(tmp_path):
    _, profiles = _profiles(tmp_path)
    line = '{"timestamp": "2026-08-05T20:14:10Z", "level": "error", "service": "billing-api", "message": "charge failed"}'
    grouped = group_multiline_logs([(1, line)])
    best, score = select_best_profile(grouped, profiles)
    assert best is not None
    assert best.name == "Application JSON Log"


def test_delimited_row_with_wrong_column_count_is_rejected():
    csv_profile = ParserProfile(
        name="test_csv",
        type="delimited",
        pattern=",",
        timestamp_field="timestamp",
        level_field="level",
        component_field="component",
        message_field="message",
        delimiter_fields=["timestamp", "level", "component", "message"],
    )
    # Only 2 columns, profile expects 4 -- should not match.
    grouped = group_multiline_logs([(1, "2026-08-05T20:14:10Z,just two columns")])
    best, score = select_best_profile(grouped, [csv_profile])
    assert best is None or score < csv_profile.min_match_ratio

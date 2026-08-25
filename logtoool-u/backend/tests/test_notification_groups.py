import pytest

from backend.alerts.notification_groups import GroupNameTakenError, GroupNotFoundError, NotificationGroupManager


@pytest.fixture
def manager(tmp_path):
    return NotificationGroupManager(db_path=str(tmp_path / "test.db"))


def test_create_and_list_group(manager):
    group = manager.create_group("Payments Team", "a@example.com,b@example.com")
    assert group["name"] == "Payments Team"
    assert group["emails"] == "a@example.com,b@example.com"
    assert group["group_id"]
    assert group["created_at"] == group["updated_at"]

    groups = manager.list_groups()
    assert len(groups) == 1
    assert groups[0]["group_id"] == group["group_id"]


def test_get_group(manager):
    created = manager.create_group("On-Call", "oncall@example.com")
    fetched = manager.get_group(created["group_id"])
    assert fetched == created


def test_get_missing_group_raises(manager):
    with pytest.raises(GroupNotFoundError):
        manager.get_group("does-not-exist")


def test_create_duplicate_name_raises(manager):
    manager.create_group("Payments Team", "a@example.com")
    with pytest.raises(GroupNameTakenError):
        manager.create_group("Payments Team", "b@example.com")


def test_update_group_name_and_emails(manager):
    created = manager.create_group("On-Call", "oncall@example.com")
    updated = manager.update_group(created["group_id"], name="On-Call Rotation", emails="a@example.com,b@example.com")
    assert updated["name"] == "On-Call Rotation"
    assert updated["emails"] == "a@example.com,b@example.com"
    assert updated["updated_at"] >= created["updated_at"]


def test_update_missing_group_raises(manager):
    with pytest.raises(GroupNotFoundError):
        manager.update_group("does-not-exist", name="x")


def test_update_to_taken_name_raises(manager):
    manager.create_group("Group A", "a@example.com")
    group_b = manager.create_group("Group B", "b@example.com")
    with pytest.raises(GroupNameTakenError):
        manager.update_group(group_b["group_id"], name="Group A")


def test_delete_group(manager):
    created = manager.create_group("Temp Group", "temp@example.com")
    manager.delete_group(created["group_id"])
    assert manager.list_groups() == []


def test_delete_nonexistent_group_is_a_no_op(manager):
    manager.delete_group("does-not-exist")  # must not raise

import sqlite3

import pytest

import db


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "test_wikiege.db"
    db.init_db(path)
    return path


def add_material(path, title="Тестовый материал", url="https://example.test/article"):
    return db.add_material(
        "Информатика", 1, title, url, "Описание", "статья", path=path
    )


def test_init_db_creates_valid_database_and_is_idempotent(database_path):
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 3

    assert add_material(database_path)[0]
    db.init_db(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 4


def test_init_db_does_not_reseed_after_all_records_are_deleted(database_path):
    with sqlite3.connect(database_path) as connection:
        material_ids = [
            row[0] for row in connection.execute("SELECT id FROM materials")
        ]
    for material_id in material_ids:
        db.delete_material(material_id, path=database_path)

    db.init_db(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("subject", "task_no", "title", "url", "description", "kind"),
    [
        ("Неизвестный предмет", 1, "Заголовок", "https://example.test", "", "статья"),
        ("Информатика", 0, "Заголовок", "https://example.test", "", "статья"),
        ("Информатика", 28, "Заголовок", "https://example.test", "", "статья"),
        ("Информатика", 1, " ", "https://example.test", "", "статья"),
        ("Информатика", 1, "Заголовок", "", "", "статья"),
        ("Информатика", 1, "Заголовок", "javascript:alert(1)", "", "статья"),
        ("Информатика", 1, "Заголовок", "https://user:pass@example.test", "", "статья"),
        ("Информатика", 1, "Заголовок", "https://[", "", "статья"),
        ("Информатика", 1, "Заголовок", "https://example.test:bad", "", "статья"),
        ("Информатика", 1, "Заголовок", "https://example.test:65536", "", "статья"),
        ("Информатика", 1, "Заголовок", "https://example.test", "", "книга"),
    ],
)
def test_add_material_rejects_invalid_data(
    database_path, subject, task_no, title, url, description, kind
):
    success, message = db.add_material(
        subject, task_no, title, url, description, kind, path=database_path
    )

    assert not success
    assert message


def test_add_material_rejects_duplicate_normalized_urls(database_path):
    first = add_material(database_path, url="https://EXAMPLE.test/article/")
    duplicate = add_material(database_path, url="https://example.test/article")

    assert first[0]
    assert not duplicate[0]
    assert "уже есть" in duplicate[1]


def test_update_material_updates_fields_and_rejects_duplicate(database_path):
    assert add_material(database_path, title="Первый", url="https://example.test/one")[0]
    assert add_material(database_path, title="Второй", url="https://example.test/two")[0]
    materials = db.get_pending(path=database_path)
    first_id, second_id = materials[0]["id"], materials[1]["id"]

    success, _ = db.update_material(
        first_id,
        "Физика",
        2,
        "Обновлённый",
        "https://example.test/updated",
        "Новое описание",
        "видео",
        path=database_path,
    )
    duplicate, _ = db.update_material(
        first_id,
        "Информатика",
        1,
        "Обновлённый",
        "https://example.test/two",
        "",
        "статья",
        path=database_path,
    )
    missing, _ = db.update_material(
        -1,
        "Информатика",
        1,
        "Не найден",
        "https://example.test/missing",
        "",
        "статья",
        path=database_path,
    )

    assert success
    assert not duplicate
    assert not missing
    updated = next(
        row for row in db.get_pending(path=database_path) if row["id"] == first_id
    )
    assert (updated["subject"], updated["task_no"], updated["title"], updated["kind"]) == (
        "Физика",
        2,
        "Обновлённый",
        "видео",
    )
    assert second_id != first_id


def test_delete_material_removes_record(database_path):
    assert add_material(database_path)[0]
    material_id = db.get_pending(path=database_path)[0]["id"]

    db.delete_material(material_id, path=database_path)

    assert all(row["id"] != material_id for row in db.get_pending(path=database_path))


def test_likes_and_reports_are_moderated(database_path):
    material_id = db.get_materials("Информатика", 1, path=database_path)[0]["id"]

    db.like(material_id, path=database_path)
    db.like(material_id, path=database_path)
    for _ in range(db.REPORTS_LIMIT):
        db.report(material_id, path=database_path)

    pending = next(
        row for row in db.get_pending(path=database_path) if row["id"] == material_id
    )
    assert pending["likes"] == 2
    assert pending["reports"] == db.REPORTS_LIMIT

    db.approve(material_id, path=database_path)
    approved = next(
        row
        for row in db.get_materials("Информатика", 1, path=database_path)
        if row["id"] == material_id
    )
    assert approved["reports"] == 0

    db.report(material_id, path=database_path)
    assert any(row["id"] == material_id for row in db.get_reported(path=database_path))
    db.reject(material_id, path=database_path)
    assert all(
        row["id"] != material_id
        for row in db.get_materials("Информатика", 1, path=database_path)
    )


def test_get_materials_filters_status_and_sorts(database_path):
    assert add_material(database_path, "Старый", "https://example.test/old")[0]
    assert add_material(database_path, "Новый", "https://example.test/new")[0]
    pending = db.get_pending(path=database_path)
    old_id, new_id = pending[0]["id"], pending[1]["id"]
    db.approve(old_id, path=database_path)
    db.approve(new_id, path=database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE materials SET likes = 1, added_at = '2025-01-01 00:00' WHERE id = ?",
            (old_id,),
        )
        connection.execute(
            "UPDATE materials SET likes = 5, added_at = '2024-01-01 00:00' WHERE id = ?",
            (new_id,),
        )
        connection.commit()

    popular = db.get_materials("Информатика", 1, order="likes", path=database_path)
    newest = db.get_materials("Информатика", 1, order="date", path=database_path)

    popular_ids = [row["id"] for row in popular]
    newest_ids = [row["id"] for row in newest]
    assert popular_ids.index(new_id) < popular_ids.index(old_id)
    assert newest_ids.index(old_id) < newest_ids.index(new_id)
    assert all(row["status"] == "approved" for row in popular)


def test_count_by_task_counts_only_approved_materials(database_path):
    assert add_material(database_path)[0]
    pending_id = db.get_pending(path=database_path)[0]["id"]

    counts = db.count_by_task("Информатика", path=database_path)
    assert counts[1] == 2

    db.approve(pending_id, path=database_path)
    counts = db.count_by_task("Информатика", path=database_path)
    assert counts[1] == 3
    assert counts[2] == 1


def test_backup_database_copies_a_consistent_snapshot(database_path, tmp_path):
    backup_path = tmp_path / "backups" / "snapshot.db"
    add_material(database_path)

    db.backup_database(backup_path, source_path=database_path)

    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 4


def test_backup_database_rejects_overwriting_source(database_path):
    with pytest.raises(ValueError, match="must differ"):
        db.backup_database(database_path, source_path=database_path)


def test_backup_database_preserves_existing_destination(database_path, tmp_path):
    backup_path = tmp_path / "existing-backup.db"
    backup_path.write_text("keep this file", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        db.backup_database(backup_path, source_path=database_path)

    assert backup_path.read_text(encoding="utf-8") == "keep this file"

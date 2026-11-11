import sqlite3
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

import db


def test_admin_panel_is_disabled_without_configured_password(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    database_path = tmp_path / "admin-test.db"
    monkeypatch.setenv("WIKIEGE_DB_PATH", str(database_path))
    monkeypatch.setattr(db, "DB_PATH", database_path)
    app_path = Path(db.__file__).with_name("app.py")

    app = AppTest.from_file(str(app_path)).run()

    assert not app.exception
    assert any("Панель администратора отключена" in item.value for item in app.error)
    assert not app.session_state["is_admin"]


def _app_with_temp_database(tmp_path, monkeypatch):
    database_path = tmp_path / "app-test.db"
    monkeypatch.setenv("WIKIEGE_DB_PATH", str(database_path))
    monkeypatch.setattr(db, "DB_PATH", database_path)
    st.cache_resource.clear()
    app_path = Path(db.__file__).with_name("app.py")
    return AppTest.from_file(str(app_path)).run(), database_path


def _button_by_label(app, label):
    return next(button for button in app.button if button.label == label)


def test_catalog_actions_update_database_and_hide_admin_actions(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    app, database_path = _app_with_temp_database(tmp_path, monkeypatch)
    material = db.get_materials("Информатика", 1, path=database_path)[0]

    assert not app.exception
    assert f"like_{material['id']}" in {button.key for button in app.button}
    assert f"adel_{material['id']}" not in {button.key for button in app.button}
    assert f"aedit_{material['id']}" not in {button.key for button in app.button}

    app.button(key=f"like_{material['id']}").click().run()
    app.button(key=f"rep_{material['id']}").click().run()

    updated = next(
        row
        for row in db.get_reported(path=database_path)
        if row["id"] == material["id"]
    )
    assert updated["likes"] == material["likes"] + 1
    assert updated["reports"] == material["reports"] + 1
    assert not app.exception


def test_catalog_subject_task_and_sort_controls(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    app, _ = _app_with_temp_database(tmp_path, monkeypatch)

    app.selectbox[0].select("Русский язык").run()
    assert not app.exception
    app.selectbox[1].select(2).run()
    assert any("по предмету «Русский язык»" in item.value for item in app.info)

    app.selectbox[1].select(1).run()
    app.segmented_control[0].set_value("Сначала новые").run()

    assert app.segmented_control[0].value == "Сначала новые"
    assert any("по предмету «Русский язык»" in item.value for item in app.info)
    assert not app.exception


def test_add_form_validates_and_saves_pending_material(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    app, database_path = _app_with_temp_database(tmp_path, monkeypatch)

    next(
        widget
        for widget in app.text_input
        if widget.label == "Название материала *"
    ).set_value("Мой материал")
    next(widget for widget in app.text_input if widget.label == "Ссылка *").set_value(
        "javascript:alert(1)"
    )
    next(
        button
        for button in app.form_submit_button
        if button.label == "Отправить на проверку"
    ).click().run()

    assert any("Ссылка должна начинаться" in item.value for item in app.error)
    assert db.get_pending(path=database_path) == []

    next(widget for widget in app.text_input if widget.label == "Ссылка *").set_value(
        "https://example.test/material"
    )
    next(
        button
        for button in app.form_submit_button
        if button.label == "Отправить на проверку"
    ).click().run()

    pending = db.get_pending(path=database_path)
    assert len(pending) == 1
    assert pending[0]["title"] == "Мой материал"
    assert pending[0]["url"] == "https://example.test/material"
    assert not app.exception


def test_admin_can_approve_edit_and_confirm_deletion(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADMIN_PASSWORD", "test-only-password")
    app, database_path = _app_with_temp_database(tmp_path, monkeypatch)

    next(
        widget
        for widget in app.text_input
        if widget.label == "Название материала *"
    ).set_value("Материал для модерации")
    next(widget for widget in app.text_input if widget.label == "Ссылка *").set_value(
        "https://example.test/moderated"
    )
    next(
        button
        for button in app.form_submit_button
        if button.label == "Отправить на проверку"
    ).click().run()
    material_id = db.get_pending(path=database_path)[0]["id"]

    assert f"ok_{material_id}" not in {button.key for button in app.button}
    password = next(
        widget for widget in app.text_input if widget.label == "Пароль администратора"
    )
    password.set_value("test-only-password")
    _button_by_label(app, "Войти").click().run()

    assert not app.exception
    assert f"ok_{material_id}" in {button.key for button in app.button}

    app.button(key=f"ok_{material_id}").click().run()
    approved = next(
        row
        for row in db.get_materials("Информатика", 1, path=database_path)
        if row["id"] == material_id
    )
    assert approved["status"] == "approved"

    app.button(key=f"aedit_{material_id}").click().run()
    app.text_input(key=f"e_title_{material_id}").set_value("Исправленный заголовок")
    app.form_submit_button(key=f"save_{material_id}").click().run()
    assert not app.exception
    approved = next(
        row
        for row in db.get_materials("Информатика", 1, path=database_path)
        if row["id"] == material_id
    )
    assert approved["title"] == "Исправленный заголовок"

    app.button(key=f"adel_{material_id}").click().run()
    assert any("без возможности восстановления" in item.value for item in app.warning)
    assert any(
        row["id"] == material_id
        for row in db.get_materials("Информатика", 1, path=database_path)
    )

    app.button(key=f"confirm_delete_{material_id}").click().run()
    assert all(
        row["id"] != material_id
        for row in db.get_materials("Информатика", 1, path=database_path)
    )

    next(
        widget
        for widget in app.text_input
        if widget.label == "Название материала *"
    ).set_value("Материал для отклонения")
    next(widget for widget in app.text_input if widget.label == "Ссылка *").set_value(
        "https://example.test/rejected"
    )
    next(
        button
        for button in app.form_submit_button
        if button.label == "Отправить на проверку"
    ).click().run()
    rejected_id = next(
        row["id"]
        for row in db.get_pending(path=database_path)
        if row["title"] == "Материал для отклонения"
    )

    app.button(key=f"no_{rejected_id}").click().run()
    with sqlite3.connect(database_path) as connection:
        rejected_status = connection.execute(
            "SELECT status FROM materials WHERE id = ?", (rejected_id,)
        ).fetchone()[0]
    assert rejected_status == "rejected"
    assert not app.exception

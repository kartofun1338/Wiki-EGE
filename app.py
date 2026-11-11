"""Вики-ЕГЭ: каталог учебных материалов, который пополняют сами пользователи.

Запуск:  streamlit run app.py
"""
import hmac
import os
import re

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

import db

SUBJECTS = db.SUBJECTS


def _read_admin_password():
    password = os.environ.get("ADMIN_PASSWORD")
    if password is None:
        try:
            password = st.secrets["ADMIN_PASSWORD"]
        except (KeyError, StreamlitSecretNotFoundError):
            return None
    if not isinstance(password, str) or not password.strip():
        raise ValueError("ADMIN_PASSWORD must be a non-empty string.")
    return password


ADMIN_PASSWORD = _read_admin_password()
KIND_ICONS = {"видео": "🎬", "статья": "📄"}

st.set_page_config(page_title="Вики-ЕГЭ", page_icon="📚")


@st.cache_resource
def setup():
    """Создаёт базу данных один раз при запуске сайта."""
    db.init_db()
    return True


setup()

if ADMIN_PASSWORD is None:
    st.session_state["is_admin"] = False


def esc(text):
    """Экранирует символы Markdown, чтобы чужой текст не ломал вёрстку."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~<$])", r"\\\1", text)


# ---------- действия кнопок ----------
def on_like(material_id):
    voted = st.session_state.setdefault("voted", set())
    if material_id in voted:
        st.toast("Вы уже оценили этот материал.")
        return
    db.like(material_id)
    voted.add(material_id)


def on_report(material_id):
    reported = st.session_state.setdefault("reported", set())
    if material_id in reported:
        st.toast("Жалоба уже отправлена.")
        return
    db.report(material_id)
    reported.add(material_id)
    st.toast("Спасибо! Мы проверим ссылку.")


# ---------- редактирование в очереди админа ----------
EDIT_FIELDS = ("e_subj", "e_task", "e_kind", "e_title", "e_url", "e_desc")


def _clear_edit_fields(material_id):
    """Убирает сохранённые значения полей формы, чтобы при следующем открытии
    она заполнялась свежими данными из базы, а не старыми из session_state."""
    for suffix in EDIT_FIELDS:
        st.session_state.pop(f"{suffix}_{material_id}", None)


def start_edit(material_id):
    """Открывает форму редактирования материала."""
    _clear_edit_fields(material_id)
    st.session_state["editing_id"] = material_id


def on_delete(material_id):
    """Удаляет материал по команде администратора."""
    db.delete_material(material_id)
    if st.session_state.get("editing_id") == material_id:
        _clear_edit_fields(material_id)
        st.session_state["editing_id"] = None
    st.session_state["confirm_delete_id"] = None
    st.toast("Материал удалён.")


def request_delete(material_id):
    st.session_state["confirm_delete_id"] = material_id


def cancel_delete():
    st.session_state["confirm_delete_id"] = None


def material_edit_form(m):
    """Форма правки материала администратором. m — строка из базы (sqlite3.Row)."""
    if st.session_state.get("editing_id") != m["id"]:
        return
    with st.form(f"edit_form_{m['id']}"):
        e_subj = st.selectbox(
            "Предмет", list(SUBJECTS), key=f"e_subj_{m['id']}",
            index=list(SUBJECTS).index(m["subject"]) if m["subject"] in SUBJECTS else 0)
        e_kind = st.radio(
            "Тип материала", list(db.KINDS), horizontal=True,
            key=f"e_kind_{m['id']}",
            index=list(db.KINDS).index(m["kind"]) if m["kind"] in db.KINDS else 0)
        e_task = st.number_input(
            "Номер задания", min_value=1, max_value=SUBJECTS[e_subj],
            value=int(m["task_no"]), step=1, key=f"e_task_{m['id']}")
        e_title = st.text_input("Название", value=m["title"], max_chars=120,
                                key=f"e_title_{m['id']}")
        e_url = st.text_input("Ссылка", value=m["url"], key=f"e_url_{m['id']}")
        e_desc = st.text_area("Короткое описание (необязательно)",
                              value=m["description"] or "", max_chars=300,
                              key=f"e_desc_{m['id']}")
        saved = st.form_submit_button("💾 Сохранить изменения", key=f"save_{m['id']}")
    if saved:
        ok, message = db.update_material(m["id"], e_subj, int(e_task),
                                         e_title, e_url, e_desc, e_kind)
        if ok:
            st.session_state["editing_id"] = None
            _clear_edit_fields(m["id"])
        st.session_state["edit_msg"] = (ok, message)
        st.rerun()
    if st.button("Отмена", key=f"cancel_{m['id']}"):
        _clear_edit_fields(m["id"])
        st.session_state["editing_id"] = None
        st.rerun()


# ---------- вкладка «Каталог» (ФТ-1, ФТ-2, ФТ-6, ФТ-7, ФТ-8) ----------
def catalog_tab():
    col1, col2 = st.columns(2)
    subject = col1.selectbox("Предмет", list(SUBJECTS))
    counts = db.count_by_task(subject)
    task_no = col2.selectbox(
        "Номер задания",
        range(1, SUBJECTS[subject] + 1),
        format_func=lambda n: f"Задание {n} ({counts.get(n, 0)})",
    )
    sort_label = st.segmented_control(
        "Сортировка",
        ["По оценке", "Сначала новые"],
        default="По оценке",
    )
    order = "likes" if sort_label == "По оценке" else "date"

    materials = db.get_materials(subject, task_no, order)
    if not materials:
        st.info(
            f"В задании {task_no} по предмету «{subject}» пока нет материалов. "
            "Вы можете предложить первый на вкладке «Добавить материал»."
        )
    for m in materials:
        with st.container(border=True):
            st.markdown(f"**{KIND_ICONS.get(m['kind'], '')} {esc(m['title'])}**")
            if m["description"]:
                st.markdown(esc(m["description"]))
            st.caption(f"{m['subject']} · Задание {m['task_no']} · Добавлено: {m['added_at']}")
            st.divider()
            with st.container(horizontal=True, horizontal_alignment="distribute"):
                st.link_button(
                    "Открыть",
                    m["url"],
                    icon=":material/open_in_new:",
                    width="stretch",
                )
                st.button(
                    f"Полезно · {m['likes']}",
                    key=f"like_{m['id']}",
                    icon=":material/thumb_up:",
                    on_click=on_like,
                    args=(m["id"],),
                    width="stretch",
                )
                st.button(
                    "Пожаловаться",
                    key=f"rep_{m['id']}",
                    icon=":material/flag:",
                    help="Сообщить о неработающей или некорректной ссылке",
                    on_click=on_report,
                    args=(m["id"],),
                    width="stretch",
                )
            if st.session_state.get("is_admin"):
                with st.container(horizontal=True, horizontal_alignment="distribute"):
                    st.button(
                        "Редактировать",
                        key=f"aedit_{m['id']}",
                        icon=":material/edit:",
                        on_click=start_edit,
                        args=(m["id"],),
                        width="stretch",
                    )
                    st.button(
                        "Удалить",
                        key=f"adel_{m['id']}",
                        icon=":material/delete:",
                        on_click=request_delete,
                        args=(m["id"],),
                        width="stretch",
                    )
                if st.session_state.get("confirm_delete_id") == m["id"]:
                    st.warning("Удалить материал без возможности восстановления?")
                    with st.container(horizontal=True):
                        st.button(
                            "Подтвердить удаление",
                            key=f"confirm_delete_{m['id']}",
                            on_click=on_delete,
                            args=(m["id"],),
                        )
                        st.button(
                            "Отмена",
                            key=f"cancel_delete_{m['id']}",
                            on_click=cancel_delete,
                        )
                material_edit_form(m)


# ---------- вкладка «Добавить материал» (ФТ-3, ФТ-4) ----------
def add_tab():
    st.write("Нашли полезное видео или статью? Добавьте ссылку, и после проверки её увидят все.")
    with st.form("add_form"):
        subject = st.selectbox("Предмет", list(SUBJECTS))
        task_no = st.number_input("Номер задания", min_value=1, max_value=SUBJECTS[subject],
                                  value=1, step=1)
        kind = st.radio("Тип материала", list(db.KINDS), horizontal=True)
        title = st.text_input("Название материала *", max_chars=120)
        url = st.text_input("Ссылка *", placeholder="https://...")
        description = st.text_area("Короткое описание (необязательно)", max_chars=300)
        submitted = st.form_submit_button(
            "Отправить на проверку",
            type="primary",
            width="stretch",
        )

    if submitted:
        ok, message = db.add_material(subject, int(task_no), title, url, description, kind)
        (st.success if ok else st.error)(message)


# ---------- вкладка «Администратор» (ФТ-5) ----------
def admin_tab():
    if ADMIN_PASSWORD is None:
        st.error(
            "Панель администратора отключена. Настройте ADMIN_PASSWORD "
            "в переменных окружения или в .streamlit/secrets.toml."
        )
        return

    if not st.session_state.get("is_admin"):
        password = st.text_input("Пароль администратора", type="password")
        if st.button("Войти"):
            if hmac.compare_digest(
                password.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")
            ):
                st.session_state["is_admin"] = True
                st.rerun()
            else:
                st.error("Неверный пароль.")
        return

    if st.button("Выйти"):
        st.session_state["is_admin"] = False
        st.rerun()

    msg = st.session_state.pop("edit_msg", None)
    if msg:
        (st.success if msg[0] else st.error)(msg[1])

    st.subheader("Очередь проверки")
    pending = db.get_pending()
    if not pending:
        st.info("Новых материалов нет.")
    for m in pending:
        with st.container(border=True):
            st.markdown(f"**{esc(m['title'])}**  \n{m['subject']}, задание {m['task_no']} · {m['kind']}")
            if m["description"]:
                st.markdown(esc(m["description"]))
            if m["reports"]:
                st.caption(f"Жалоб: {m['reports']}")
            st.link_button("Проверить ссылку", m["url"])
            c1, c2, c3 = st.columns(3)
            c1.button("✅ Одобрить", key=f"ok_{m['id']}", on_click=db.approve, args=(m["id"],))
            c2.button("❌ Отклонить", key=f"no_{m['id']}", on_click=db.reject, args=(m["id"],))
            c3.button("✏️ Редактировать", key=f"edit_{m['id']}", on_click=start_edit, args=(m["id"],))

            material_edit_form(m)

    st.subheader("Жалобы на опубликованные материалы")
    reported = db.get_reported()
    if not reported:
        st.info("Жалоб нет.")
    for m in reported:
        with st.container(border=True):
            st.markdown(f"**{esc(m['title'])}**  \n{m['subject']}, задание {m['task_no']} · жалоб: {m['reports']}")
            st.link_button("Проверить ссылку", m["url"])
            c1, c2 = st.columns(2)
            c1.button("Оставить", key=f"keep_{m['id']}", on_click=db.approve, args=(m["id"],))
            c2.button("Скрыть", key=f"hide_{m['id']}", on_click=db.reject, args=(m["id"],))


# ---------- страница ----------
st.title("📚 Вики-ЕГЭ")
st.caption("Каталог материалов для подготовки к ЕГЭ. Его пополняют сами пользователи.")

tab_catalog, tab_add, tab_admin = st.tabs(["Каталог", "Добавить материал", "Администратор"])
with tab_catalog:
    catalog_tab()
with tab_add:
    add_tab()
with tab_admin:
    admin_tab()

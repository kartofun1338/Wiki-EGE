"""Работа с базой данных SQLite для сервиса «Вики-ЕГЭ».

Здесь нет ничего, что относится к Streamlit: только sqlite3 из стандартной
библиотеки Python. Благодаря этому логику можно проверять отдельно от сайта.
"""
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = Path(
    os.environ.get(
        "WIKIEGE_DB_PATH",
        Path(__file__).resolve().with_name("wiki_ege.db"),
    )
).expanduser()
REPORTS_LIMIT = 3          # сколько жалоб скрывают материал до проверки админом
KINDS = ("видео", "статья")
SUBJECTS = {
    "Информатика": 27,
    "Русский язык": 27,
    "Профильная математика": 19,
    "Физика": 26,
}


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def backup_database(destination: str | Path, source_path: str | Path | None = None) -> None:
    """Creates a consistent SQLite backup without copying a live database file."""
    source = Path(source_path or DB_PATH).resolve()
    target = Path(destination).expanduser().resolve()
    if source == target:
        raise ValueError("Backup destination must differ from the database path.")
    if target.exists():
        raise FileExistsError(f"Backup destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(source)) as source_conn, closing(
        sqlite3.connect(target)
    ) as target_conn:
        source_conn.backup(target_conn)


# ---------- создание базы (ФТ-9) ----------
def init_db(path=None):
    """Создаёт таблицу, если её нет, и добавляет несколько стартовых материалов."""
    with closing(_connect(path)) as conn, conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'materials'"
        ).fetchone() is not None
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS materials (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                subject     TEXT    NOT NULL,
                task_no     INTEGER NOT NULL,
                title       TEXT    NOT NULL,
                url         TEXT    NOT NULL,
                description TEXT    NOT NULL DEFAULT '',
                kind        TEXT    NOT NULL,
                added_at    TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'pending',
                likes       INTEGER NOT NULL DEFAULT 0,
                reports     INTEGER NOT NULL DEFAULT 0,
                UNIQUE (subject, task_no, url)
            )
            """
        )
        if not table_exists:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            seed = [
                ("Информатика", 1, "Открытый банк заданий ЕГЭ (ФИПИ)",
                 "https://ege.fipi.ru/bank/", "Официальный банк заданий для тренировки.", "статья"),
                ("Информатика", 1, "Демоверсии, спецификации и кодификаторы (ФИПИ)",
                 "https://fipi.ru/ege/demoversii-specifikacii-kodifikatory",
                 "Что именно проверяют на экзамене в этом году.", "статья"),
                ("Информатика", 2, "Решу ЕГЭ: информатика",
                 "https://inf-ege.sdamgia.ru/", "Каталог заданий с проверкой ответов.", "статья"),
            ]
            conn.executemany(
                "INSERT INTO materials (subject, task_no, title, url, description, kind, added_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'approved')",
                [(s, t, ti, u, d, k, now) for s, t, ti, u, d, k in seed],
            )


# ---------- проверка данных (ФТ-4) ----------
def normalize_url(url):
    """Приводит ссылку к единому виду, чтобы находить повторы."""
    url = url.strip()
    parts = urlparse(url)
    return parts._replace(scheme=parts.scheme.lower(), netloc=parts.netloc.lower()).geturl().rstrip("/")


def validate_material(subject, task_no, title, url, description, kind):
    """Возвращает список ошибок. Пустой список означает, что данные верны."""
    errors = []
    if not isinstance(subject, str) or subject not in SUBJECTS:
        errors.append("Выберите допустимый предмет.")
    elif (
        not isinstance(task_no, int)
        or isinstance(task_no, bool)
        or not 1 <= task_no <= SUBJECTS[subject]
    ):
        errors.append("Укажите допустимый номер задания для выбранного предмета.")

    if not isinstance(title, str) or not title.strip():
        errors.append("Укажите название материала.")
    elif len(title) > 120:
        errors.append("Название слишком длинное (максимум 120 символов).")
    if not isinstance(url, str) or not url.strip():
        errors.append("Укажите ссылку.")
    elif len(url.strip()) > 500:
        errors.append("Ссылка слишком длинная.")
    else:
        try:
            parts = urlparse(url.strip())
            port = parts.port
            valid_url = (
                parts.scheme.lower() in ("http", "https")
                and bool(parts.hostname)
                and parts.username is None
                and parts.password is None
                and (port is None or 1 <= port <= 65535)
                and not any(character.isspace() for character in url)
            )
        except ValueError:
            valid_url = False
        if not valid_url:
            errors.append("Ссылка должна начинаться с http:// или https:// и не содержать пробелов.")
    if not isinstance(description, str):
        errors.append("Описание должно быть текстом.")
    elif len(description.strip()) > 300:
        errors.append("Описание слишком длинное (максимум 300 символов).")
    if kind not in KINDS:
        errors.append("Выберите тип материала.")
    return errors


# ---------- добавление материала (ФТ-3, ФТ-5) ----------
def add_material(subject, task_no, title, url, description, kind, path=None):
    """Сохраняет материал со статусом «на проверке». Возвращает (успех, сообщение)."""
    errors = validate_material(subject, task_no, title, url, description, kind)
    if errors:
        return False, " ".join(errors)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        with closing(_connect(path)) as conn, conn:
            conn.execute(
                "INSERT INTO materials (subject, task_no, title, url, description, kind, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (subject, task_no, title.strip(), normalize_url(url), description.strip(), kind, now),
            )
    except sqlite3.IntegrityError:
        return False, "Такая ссылка уже есть в этом задании (возможно, она ждёт проверки)."
    return True, "Спасибо! Материал отправлен на проверку и скоро появится в каталоге."


# ---------- редактирование (правки администратора) ----------
def update_material(material_id, subject, task_no, title, url, description, kind, path=None):
    """Меняет поля материала из очереди проверки. Возвращает (успех, сообщение)."""
    errors = validate_material(subject, task_no, title, url, description, kind)
    if errors:
        return False, " ".join(errors)
    try:
        with closing(_connect(path)) as conn, conn:
            cursor = conn.execute(
                "UPDATE materials SET subject = ?, task_no = ?, title = ?, url = ?, "
                "description = ?, kind = ? WHERE id = ?",
                (subject, task_no, title.strip(), normalize_url(url), description.strip(), kind, material_id),
            )
    except sqlite3.IntegrityError:
        return False, "Такая ссылка уже есть в этом задании (возможно, она ждёт проверки)."
    if cursor.rowcount == 0:
        return False, "Материал не найден."
    return True, "Материал обновлён."


# ---------- чтение (ФТ-2, ФТ-8) ----------
def get_materials(subject, task_no, order="likes", path=None):
    """Одобренные материалы задания. order: 'likes' (по оценке) или 'date' (новые сверху)."""
    order_sql = "likes DESC, added_at DESC" if order == "likes" else "added_at DESC, id DESC"
    with closing(_connect(path)) as conn:
        return conn.execute(
            f"SELECT * FROM materials WHERE status = 'approved' AND subject = ? AND task_no = ? "
            f"ORDER BY {order_sql}",
            (subject, task_no),
        ).fetchall()


def count_by_task(subject, path=None):
    """Сколько одобренных материалов в каждом задании: {номер: количество}."""
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT task_no, COUNT(*) AS n FROM materials WHERE status = 'approved' AND subject = ? "
            "GROUP BY task_no",
            (subject,),
        ).fetchall()
    return {r["task_no"]: r["n"] for r in rows}


# ---------- оценки и жалобы (ФТ-6, ФТ-7) ----------
def like(material_id, path=None):
    with closing(_connect(path)) as conn, conn:
        conn.execute("UPDATE materials SET likes = likes + 1 WHERE id = ?", (material_id,))


def report(material_id, path=None):
    """Добавляет жалобу. При достижении лимита материал скрывается до проверки."""
    with closing(_connect(path)) as conn, conn:
        conn.execute(
            "UPDATE materials SET reports = reports + 1, "
            "status = CASE WHEN reports + 1 >= ? THEN 'pending' ELSE status END "
            "WHERE id = ?",
            (REPORTS_LIMIT, material_id),
        )


# ---------- модерация (ФТ-5) ----------
def get_pending(path=None):
    with closing(_connect(path)) as conn:
        return conn.execute(
            "SELECT * FROM materials WHERE status = 'pending' ORDER BY added_at, id"
        ).fetchall()


def get_reported(path=None):
    """Показанные в каталоге материалы, на которые есть жалобы."""
    with closing(_connect(path)) as conn:
        return conn.execute(
            "SELECT * FROM materials WHERE status = 'approved' AND reports > 0 "
            "ORDER BY reports DESC, id"
        ).fetchall()


def approve(material_id, path=None):
    """Одобряет материал и сбрасывает жалобы."""
    with closing(_connect(path)) as conn, conn:
        conn.execute("UPDATE materials SET status = 'approved', reports = 0 WHERE id = ?", (material_id,))


def reject(material_id, path=None):
    with closing(_connect(path)) as conn, conn:
        conn.execute("UPDATE materials SET status = 'rejected' WHERE id = ?", (material_id,))


def delete_material(material_id, path=None):
    """Удаляет материал из базы (админ убирает устаревшие или некорректные ссылки)."""
    with closing(_connect(path)) as conn, conn:
        conn.execute("DELETE FROM materials WHERE id = ?", (material_id,))

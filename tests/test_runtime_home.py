import sqlite3
from pathlib import Path

from app.config import resolve_runtime_home


def _database(home: Path, label: str = "business") -> Path:
    path = home / "data" / "yanxiang_expense.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """CREATE TABLE users(username TEXT,deleted_at TEXT);
            CREATE TABLE members(name TEXT,deleted_at TEXT);
            CREATE TABLE invoices(is_demo INTEGER,deleted_at TEXT);
            CREATE TABLE marker(value TEXT);"""
        )
        conn.execute("INSERT INTO users VALUES(?,NULL)", (f"user-{label}",))
        conn.execute("INSERT INTO members VALUES(?,NULL)", (f"成员-{label}",))
        conn.execute("INSERT INTO invoices VALUES(0,NULL)")
        conn.execute("INSERT INTO marker VALUES(?)", (label,))
        conn.commit()
    finally:
        conn.close()
    return path


def _label(home: Path) -> str:
    conn = sqlite3.connect(home / "data" / "yanxiang_expense.db")
    try:
        return str(conn.execute("SELECT value FROM marker").fetchone()[0])
    finally:
        conn.close()


def _seed_profile_database(home: Path, label: str) -> Path:
    path = home / "data" / "yanxiang_expense.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """CREATE TABLE users(username TEXT,deleted_at TEXT);
            CREATE TABLE members(name TEXT,deleted_at TEXT);
            CREATE TABLE invoices(is_demo INTEGER,deleted_at TEXT);
            CREATE TABLE departments(name TEXT);
            CREATE TABLE marker(value TEXT);"""
        )
        conn.execute("INSERT INTO users VALUES('admin',NULL)")
        conn.executemany(
            "INSERT INTO members VALUES(?,NULL)",
            [(f"成员{index:02d}",) for index in range(1, 9)],
        )
        conn.execute("INSERT INTO invoices VALUES(1,NULL)")
        conn.execute("INSERT INTO departments VALUES('用户修改后的组别')")
        conn.execute("INSERT INTO marker VALUES(?)", (label,))
        conn.commit()
    finally:
        conn.close()
    return path


def test_packaged_runtime_home_follows_persistent_pointer(tmp_path: Path) -> None:
    old_home = tmp_path / "旧版软件"
    new_program = tmp_path / "新版软件"
    marker = tmp_path / "local-app-data" / "runtime-home.txt"
    _database(old_home, "old")
    _database(new_program, "new")
    marker.parent.mkdir(parents=True)
    marker.write_text(str(old_home), encoding="utf-8")

    resolved = resolve_runtime_home(
        new_program,
        environment={},
        packaged_windows=True,
        pointer_path=marker,
    )

    stable = marker.parent / "runtime"
    assert resolved == stable.resolve()
    assert _label(stable) == "old"


def test_newly_extracted_program_finds_nearby_existing_data(tmp_path: Path) -> None:
    old_home = tmp_path / "V2.2.3"
    new_program = tmp_path / "V2.2.4"
    marker = tmp_path / "missing" / "runtime-home.txt"
    _database(old_home, "nearby")
    new_program.mkdir()

    resolved = resolve_runtime_home(
        new_program,
        environment={},
        packaged_windows=True,
        pointer_path=marker,
    )

    stable = marker.parent / "runtime"
    assert resolved == stable.resolve()
    assert _label(stable) == "nearby"


def test_current_customized_database_wins_over_larger_nearby_copy(tmp_path: Path) -> None:
    current = tmp_path / "正在使用的软件"
    nearby = tmp_path / "旧备份软件"
    marker = tmp_path / "local-app-data" / "runtime-home.txt"
    _database(current, "current")
    nearby_database = _database(nearby, "nearby-larger")
    conn = sqlite3.connect(nearby_database)
    try:
        conn.executemany("INSERT INTO invoices VALUES(0,NULL)", [() for _ in range(20)])
        conn.commit()
    finally:
        conn.close()

    resolved = resolve_runtime_home(
        current,
        environment={},
        packaged_windows=True,
        pointer_path=marker,
    )

    stable = marker.parent / "runtime"
    assert resolved == stable.resolve()
    assert _label(stable) == "current"


def test_current_seed_profile_with_only_group_changes_still_wins(tmp_path: Path) -> None:
    current = tmp_path / "正在使用的软件"
    nearby = tmp_path / "其他旧副本"
    marker = tmp_path / "local-app-data" / "runtime-home.txt"
    _seed_profile_database(current, "current-group-only")
    nearby_database = _database(nearby, "nearby-larger")
    conn = sqlite3.connect(nearby_database)
    try:
        conn.executemany("INSERT INTO invoices VALUES(0,NULL)", [() for _ in range(20)])
        conn.commit()
    finally:
        conn.close()

    resolved = resolve_runtime_home(
        current,
        environment={},
        packaged_windows=True,
        pointer_path=marker,
    )

    stable = marker.parent / "runtime"
    assert resolved == stable.resolve()
    assert _label(stable) == "current-group-only"


def test_explicit_runtime_home_always_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "指定数据目录"
    resolved = resolve_runtime_home(
        tmp_path / "软件目录",
        environment={"YXRT_HOME": str(explicit)},
        packaged_windows=True,
        pointer_path=tmp_path / "runtime-home.txt",
    )
    assert resolved == explicit.resolve()


def test_existing_stable_runtime_is_reused_without_program_folder(tmp_path: Path) -> None:
    marker = tmp_path / "local-app-data" / "runtime-home.txt"
    stable = marker.parent / "runtime"
    _database(stable, "stable")

    resolved = resolve_runtime_home(
        tmp_path / "任意新版目录",
        environment={},
        packaged_windows=True,
        pointer_path=marker,
    )

    assert resolved == stable.resolve()
    assert _label(stable) == "stable"

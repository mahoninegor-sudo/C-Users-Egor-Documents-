# revisions.py
"""
Скрипт применения правок из revisions.json к проекту.

Запуск (из корня проекта):
    python revisions.py                    # применить все правки
    python revisions.py --dry-run          # показать, что будет сделано
    python revisions.py --file revisions.json
    python revisions.py --keep-going       # не останавливаться на ошибках
    python revisions.py --backup           # сохранить .bak-копии перед правкой

Типы правок:
    replace          — точная замена фрагмента (old → new)
    insert_after     — вставить content после строки с anchor
    insert_before    — вставить content перед строкой с anchor
    append           — дописать content в конец файла
    replace_line     — заменить строку line (1-based) на content
    replace_block    — заменить диапазон строк from..to на content (1-based)
    insert_at        — вставить content перед строкой line (1-based)
    delete_lines     — удалить диапазон строк from..to (1-based, включительно)
    create_file      — создать новый файл (с родительскими папками)
    mkdir            — создать пустую папку
    delete_file      — удалить файл (рекомендуется с --backup)

Флаги CLI:
    --dry-run          проверить, ничего не менять
    --keep-going       не останавливаться на первой ошибке
    --backup           сохранить .bak-копии перед правкой
    --show FILE N M    показать строки N..M файла (без применения правок)
    --check            после правок прогнать ast.parse() для изменённых .py
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path


# ──────────────────────────────────────────────────────────────
#  Пути и константы
# ──────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_JSON = SCRIPT_DIR / "revisions.json"
BACKUP_DIR_NAME = ".revisions_backup"


# ──────────────────────────────────────────────────────────────
#  Цветной вывод
# ──────────────────────────────────────────────────────────────

class C:
    OK = "\033[92m"
    WARN = "\033[93m"
    ERR = "\033[91m"
    INFO = "\033[96m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def _supports_color() -> bool:
    return sys.stdout.isatty()


def colorize(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{color}{text}{C.RESET}"


def log_ok(msg: str) -> None:
    print(colorize(f"  ✅ {msg}", C.OK))


def log_warn(msg: str) -> None:
    print(colorize(f"  ⚠  {msg}", C.WARN))


def log_err(msg: str) -> None:
    print(colorize(f"  ❌ {msg}", C.ERR))


def log_info(msg: str) -> None:
    print(colorize(f"  ℹ  {msg}", C.INFO))


# ──────────────────────────────────────────────────────────────
#  Загрузка JSON
# ──────────────────────────────────────────────────────────────

def load_revisions(json_path: Path) -> tuple[Path, list[dict]]:
    if not json_path.exists():
        raise FileNotFoundError(
            f"Не найден файл правок: {json_path}\n"
            f"Создай его рядом со скриптом или укажи путь через --file."
        )

    raw = json_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"revisions.json содержит некорректный JSON: {e}\n"
            f"Строка {e.lineno}, позиция {e.colno}."
        ) from e

    if isinstance(data, list):
        return SCRIPT_DIR, data

    if not isinstance(data, dict):
        raise ValueError("Корень revisions.json должен быть объектом или списком.")

    base_dir = Path(data.get("base_dir") or SCRIPT_DIR).expanduser()
    if not base_dir.is_absolute():
        base_dir = (SCRIPT_DIR / base_dir).resolve()

    revisions = data.get("revisions")
    if not isinstance(revisions, list):
        raise ValueError("Поле 'revisions' должно быть списком правок.")

    return base_dir, revisions


def resolve_target(base_dir: Path, file_field: str) -> Path:
    p = Path(file_field).expanduser()
    if p.is_absolute():
        return p
    return (base_dir / p).resolve()


# ──────────────────────────────────────────────────────────────
#  Резервные копии
# ──────────────────────────────────────────────────────────────

def make_backup(path: Path, backup_root: Path) -> Path:
    """Кладёт копию файла в backup_root с сохранением структуры."""
    try:
        rel = str(path.relative_to(path.anchor))
    except ValueError:
        rel = path.name
    dst = backup_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)
    return dst


# ──────────────────────────────────────────────────────────────
#  Хелперы для правки существующих файлов
# ──────────────────────────────────────────────────────────────

def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("".join(lines), encoding="utf-8")


def _require(rev: dict, *keys: str) -> None:
    missing = [k for k in keys if k not in rev]
    if missing:
        raise ValueError(f"в правке нет обязательных полей: {missing}")


# ──────────────────────────────────────────────────────────────
#  Обработчики: правка существующих файлов
# ──────────────────────────────────────────────────────────────

def _preview(s: str, limit: int = 200) -> str:
    """Короткое представление строки для логов."""
    s1 = s.replace("\n", "\\n").replace("\r", "\\r")
    if len(s1) > limit:
        s1 = s1[:limit] + "…"
    return repr(s1)


def _find_occurrences(text: str, old: str, context: int = 60,
                      max_show: int = 3) -> str:
    """Возвращает строку с фрагментами контекста вокруг вхождений old."""
    pieces: list[str] = []
    start = 0
    shown = 0
    while shown < max_show:
        idx = text.find(old, start)
        if idx == -1:
            break
        # номер строки
        line_no = text.count("\n", 0, idx) + 1
        left = max(0, idx - context)
        right = min(len(text), idx + len(old) + context)
        snippet = text[left:right]
        snippet = snippet.replace("\n", "\\n")
        pieces.append(f"    строка {line_no}: …{snippet}…")
        start = idx + 1
        shown += 1
    total = text.count(old)
    if total > shown:
        pieces.append(f"    … и ещё {total - shown} вхождений")
    return "\n".join(pieces)


def _diff_hint(text: str, old: str, first_line: str) -> str:
    """Возвращает строку с diff-подсказкой, если первая строка old есть в тексте."""
    idx = text.find(first_line)
    if idx == -1:
        return ""
    line_no = text.count("\n", 0, idx) + 1

    old_lines = old.splitlines()
    actual_lines = text[idx:].splitlines()[:len(old_lines)]

    diffs: list[str] = []
    for i, (o, a) in enumerate(zip(old_lines, actual_lines)):
        if o != a:
            diffs.append(
                f"    строка {line_no + i}: "
                f"\n      old:   {o!r}"
                f"\n      файл:  {a!r}"
            )
            if len(diffs) >= 5:
                break

    if not diffs:
        return (f"  (первая строка old найдена на строке {line_no}, "
                f"но блок не совпадает)")

    return (f"  diff вокруг первого вхождения (строка {line_no}):\n"
            + "\n".join(diffs))


def apply_replace(path: Path, rev: dict) -> str:
    _require(rev, "old", "new")
    text = path.read_text(encoding="utf-8")
    old = rev["old"]
    new = rev["new"]

    n = text.count(old)
    if n == 0:
        first_line = old.splitlines()[0] if old else ""
        hint = ""
        if first_line:
            hint = "\n" + _diff_hint(text, old, first_line)
        raise ValueError(
            f"не найдено вхождение 'old'\n"
            f"  old (первые 200 симв.): {_preview(old)}\n"
            f"  длина old: {len(old)} симв., {old.count(chr(10)) + 1} строк"
            f"{hint}"
        )

    if n > 1 and not rev.get("allow_multiple", False):
        ctx = _find_occurrences(text, old)
        raise ValueError(
            f"'old' встречается {n} раз — уточни фрагмент "
            f"или добавь \"allow_multiple\": true\n{ctx}"
        )

    text = text.replace(old, new, 1 if not rev.get("allow_multiple") else -1)
    path.write_text(text, encoding="utf-8")
    return f"replace: {n} совпад., заменено"


def apply_insert_after(path: Path, rev: dict) -> str:
    return _insert_relative(path, rev, after=True)


def apply_insert_before(path: Path, rev: dict) -> str:
    return _insert_relative(path, rev, after=False)


def _insert_relative(path: Path, rev: dict, *, after: bool) -> str:
    _require(rev, "anchor", "content")
    lines = _read_lines(path)
    anchor = rev["anchor"]
    content = rev["content"]

    idx = None
    for i, line in enumerate(lines):
        if anchor in line:
            idx = i
            break
    if idx is None:
        raise ValueError(f"не найден anchor: {anchor!r}")

    insert_at = idx + 1 if after else idx
    new_lines = content.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith(("\n", "\r")):
        new_lines[-1] = new_lines[-1] + "\n"

    lines[insert_at:insert_at] = new_lines
    _write_lines(path, lines)
    where = "после" if after else "перед"
    return f"insert_{'after' if after else 'before'}: вставлено {len(new_lines)} строк {where} стр.{idx + 1}"


def apply_append(path: Path, rev: dict) -> str:
    _require(rev, "content")
    text = path.read_text(encoding="utf-8")
    content = rev["content"]
    if text and not text.endswith("\n"):
        text += "\n"
    text += content
    path.write_text(text, encoding="utf-8")
    return f"append: +{len(content)} символов"


def apply_replace_line(path: Path, rev: dict) -> str:
    _require(rev, "line", "content")
    line_no = int(rev["line"])
    lines = _read_lines(path)
    if not (1 <= line_no <= len(lines)):
        raise ValueError(
            f"строка {line_no} вне диапазона (в файле {len(lines)} строк)"
        )
    content = rev["content"]
    if not content.endswith(("\n", "\r")):
        content += "\n"
    old = lines[line_no - 1]
    lines[line_no - 1] = content
    _write_lines(path, lines)
    return f"replace_line {line_no}: {old.rstrip()!r} → {content.rstrip()!r}"


def apply_delete_lines(path: Path, rev: dict) -> str:
    _require(rev, "from", "to")
    a = int(rev["from"])
    b = int(rev["to"])
    if a > b:
        a, b = b, a
    lines = _read_lines(path)
    if not (1 <= a <= len(lines) and 1 <= b <= len(lines)):
        raise ValueError(
            f"диапазон {a}..{b} вне файла (в файле {len(lines)} строк)"
        )
    del lines[a - 1:b]
    _write_lines(path, lines)
    return f"delete_lines {a}..{b}: удалено {b - a + 1} строк"


def apply_replace_block(path: Path, rev: dict) -> str:
    """Заменяет строки from..to (1-based, включительно) на content."""
    _require(rev, "from", "to", "content")
    a = int(rev["from"])
    b = int(rev["to"])
    if a > b:
        a, b = b, a
    lines = _read_lines(path)
    if not (1 <= a <= len(lines) and 1 <= b <= len(lines)):
        raise ValueError(
            f"диапазон {a}..{b} вне файла (в файле {len(lines)} строк)"
        )
    content = rev["content"]
    new_lines = content.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith(("\n", "\r")):
        new_lines[-1] = new_lines[-1] + "\n"

    old_block = lines[a - 1:b]
    lines[a - 1:b] = new_lines
    _write_lines(path, lines)
    return (f"replace_block {a}..{b}: "
            f"{len(old_block)} строк → {len(new_lines)} строк")


def apply_insert_at(path: Path, rev: dict) -> str:
    """Вставляет content перед строкой line (1-based)."""
    _require(rev, "line", "content")
    line_no = int(rev["line"])
    lines = _read_lines(path)
    if not (1 <= line_no <= len(lines) + 1):
        raise ValueError(
            f"строка {line_no} вне диапазона (в файле {len(lines)} строк)"
        )
    content = rev["content"]
    new_lines = content.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith(("\n", "\r")):
        new_lines[-1] = new_lines[-1] + "\n"
    lines[line_no - 1:line_no - 1] = new_lines
    _write_lines(path, lines)
    return f"insert_at {line_no}: вставлено {len(new_lines)} строк"

# ──────────────────────────────────────────────────────────────
#  Обработчики: файловая система
# ──────────────────────────────────────────────────────────────

def apply_create_file(path: Path, rev: dict) -> str:
    """
    Создаёт файл. Если файл уже есть:
      - overwrite=true  → перезаписывает
      - overwrite=false → ошибка (защита от случайной потери)
    Родительские папки создаются автоматически.
    """
    _require(rev, "content")
    overwrite = bool(rev.get("overwrite", False))

    if path.exists() and not overwrite:
        raise ValueError(
            f"файл уже существует: {path}. "
            f"Если хочешь перезаписать — добавь \"overwrite\": true"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    content = rev["content"]
    path.write_text(content, encoding="utf-8")
    n_lines = content.count("\n") + (0 if content.endswith("\n") else 1)
    verb = "перезаписан" if path.exists() else "создан"
    return f"create_file: {verb} {path.name} ({n_lines} строк, {len(content)} символов)"


def apply_mkdir(path: Path, rev: dict) -> str:
    """Создаёт папку (и все родительские). Если уже есть — не ошибка."""
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    return f"mkdir: {'уже была' if existed else 'создана'} {path}"


def apply_delete_file(path: Path, rev: dict) -> str:
    """
    Удаляет файл. Если задан \"backup\": true — предварительно копирует
    его в .revisions_backup/. Если файла нет — не ошибка.
    """
    if not path.exists():
        return f"delete_file: {path.name} уже отсутствует"

    if rev.get("backup", False):
        # backup_root известен только из run(); передаём через rev
        backup_root = rev.get("__backup_root__")
        if backup_root is not None:
            make_backup(path, Path(backup_root))

    path.unlink()
    return f"delete_file: удалён {path.name}"


HANDLERS = {
    "replace": apply_replace,
    "insert_after": apply_insert_after,
    "insert_before": apply_insert_before,
    "append": apply_append,
    "replace_line": apply_replace_line,
    "replace_block": apply_replace_block,
    "insert_at": apply_insert_at,
    "delete_lines": apply_delete_lines,
    "create_file": apply_create_file,
    "mkdir": apply_mkdir,
    "delete_file": apply_delete_file,
}


# ──────────────────────────────────────────────────────────────
#  Dry-run проверка
# ──────────────────────────────────────────────────────────────

def _dry_check(path: Path, rev: dict) -> None:
    """Проверяет, что правка применима, не изменяя файл."""
    rtype = rev["type"]

    if rtype == "replace":
        text = path.read_text(encoding="utf-8")
        old = rev.get("old")
        if not old:
            raise ValueError("нет поля 'old'")
        n = text.count(old)
        if n == 0:
            first_line = old.splitlines()[0] if old else ""
            hint = ""
            if first_line:
                idx = text.find(first_line)
                if idx != -1:
                    line_no = text.count("\n", 0, idx) + 1
                    hint = (f"; первая строка old встречается в строке {line_no}, "
                            f"но весь блок old — нет")
            raise ValueError(
                f"не найдено вхождение 'old' "
                f"(длина {len(old)} симв., "
                f"{old.count(chr(10)) + 1} строк){hint}"
            )
        if n > 1 and not rev.get("allow_multiple", False):
            ctx = _find_occurrences(text, old)
            raise ValueError(f"'old' встречается {n} раз\n{ctx}")
        if "new" not in rev:
            raise ValueError("нет поля 'new'")
    elif rtype in ("insert_after", "insert_before"):
        anchor = rev.get("anchor")
        if not anchor:
            raise ValueError("нет поля 'anchor'")
        text = path.read_text(encoding="utf-8")
        if anchor not in text:
            raise ValueError(f"не найден anchor: {anchor!r}")
        if "content" not in rev:
            raise ValueError("нет поля 'content'")
    elif rtype == "append":
        if "content" not in rev:
            raise ValueError("нет поля 'content'")
    elif rtype == "replace_line":
        line_no = int(rev.get("line", 0))
        n_lines = len(path.read_text(encoding="utf-8").splitlines())
        if not (1 <= line_no <= n_lines):
            raise ValueError(f"строка {line_no} вне файла ({n_lines})")
        if "content" not in rev:
            raise ValueError("нет поля 'content'")
    elif rtype == "replace_block":
        a = int(rev.get("from", 0))
        b = int(rev.get("to", 0))
        n_lines = len(path.read_text(encoding="utf-8").splitlines())
        if not (1 <= a <= n_lines and 1 <= b <= n_lines):
            raise ValueError(f"диапазон {a}..{b} вне файла ({n_lines})")
        if "content" not in rev:
            raise ValueError("нет поля 'content'")
    elif rtype == "insert_at":
        line_no = int(rev.get("line", 0))
        n_lines = len(path.read_text(encoding="utf-8").splitlines())
        if not (1 <= line_no <= n_lines + 1):
            raise ValueError(f"строка {line_no} вне диапазона (в файле {n_lines})")
        if "content" not in rev:
            raise ValueError("нет поля 'content'")
    elif rtype == "delete_lines":
        a = int(rev.get("from", 0))
        b = int(rev.get("to", 0))
        n_lines = len(path.read_text(encoding="utf-8").splitlines())
        if not (1 <= a <= n_lines and 1 <= b <= n_lines):
            raise ValueError(f"диапазон {a}..{b} вне файла ({n_lines})")
    elif rtype == "create_file":
        if "content" not in rev:
            raise ValueError("нет поля 'content'")
        if path.exists() and not rev.get("overwrite", False):
            raise ValueError(
                f"файл уже существует: {path}. Добавь \"overwrite\": true"
            )
    elif rtype == "mkdir":
        if path.exists() and not path.is_dir():
            raise ValueError(f"по пути {path} уже есть файл, не папка")
    elif rtype == "delete_file":
        # отсутствие файла — не ошибка (delete_file идемпотентен)
        pass
    else:
        raise ValueError(f"неизвестный тип {rtype!r}")


# ──────────────────────────────────────────────────────────────
#  Прогон
# ──────────────────────────────────────────────────────────────

def run(json_path: Path, *,
        dry_run: bool = False,
        keep_going: bool = False,
        make_backups: bool = False) -> int:
    print()
    print("=" * 72)
    print(f"  REVISIONS — применение правок")
    print(f"  JSON: {json_path}")
    print("=" * 72)

    try:
        base_dir, revisions = load_revisions(json_path)
    except Exception as e:
        log_err(str(e))
        return 2

    print(f"  base_dir: {base_dir}")
    print(f"  правок:   {len(revisions)}")
    if dry_run:
        print(colorize("  режим:    DRY-RUN (ничего не меняется)", C.WARN))
    if make_backups:
        print(colorize("  режим:    BACKUP включён", C.INFO))
    print()

    if not base_dir.exists():
        log_err(f"base_dir не существует: {base_dir}")
        return 2

    backup_root = SCRIPT_DIR / BACKUP_DIR_NAME / datetime.now().strftime("%Y%m%d_%H%M%S")

    ok_count = 0
    fail_count = 0
    failures: list[tuple[int, str]] = []

    for i, rev in enumerate(revisions, start=1):
        if not isinstance(rev, dict):
            log_err(f"[{i}] правка должна быть объектом, а не {type(rev).__name__}")
            fail_count += 1
            failures.append((i, "не объект"))
            if not keep_going:
                break
            continue

        file_field = rev.get("file")
        rtype = rev.get("type")

        header = f"[{i}/{len(revisions)}] {rtype} → {file_field}"
        print(header)

        if not file_field:
            log_err("  нет поля 'file'")
            fail_count += 1
            failures.append((i, "нет file"))
            if not keep_going:
                break
            continue

        if rtype not in HANDLERS:
            log_err(f"  неизвестный тип {rtype!r}; допустимо: {sorted(HANDLERS)}")
            fail_count += 1
            failures.append((i, f"тип {rtype!r}"))
            if not keep_going:
                break
            continue

        # Для mkdir путь указывает на папку; для остальных — на файл.
        target = resolve_target(base_dir, file_field)

        # Для правок, работающих с существующим файлом — проверяем наличие.
        if rtype in ("replace", "insert_after", "insert_before",
                     "append", "replace_line", "delete_lines"):
            if not target.exists():
                log_err(f"  файл не найден: {target}")
                fail_count += 1
                failures.append((i, f"нет файла {target}"))
                if not keep_going:
                    break
                continue

        if dry_run:
            try:
                _dry_check(target, rev)
                log_ok("  DRY-RUN: правка валидна")
                ok_count += 1
            except Exception as e:
                log_err(f"  DRY-RUN: {e}")
                fail_count += 1
                failures.append((i, str(e)))
                if not keep_going:
                    break
            continue

        if make_backups and rtype != "delete_file":
            # delete_file делает бэкап через __backup_root__
            if target.exists() and target.is_file():
                try:
                    bak = make_backup(target, backup_root)
                    print(colorize(f"  📦 backup: {bak}", C.DIM))
                except Exception as e:
                    log_warn(f"  не удалось сделать бэкап: {e}")

        # Прокидываем backup_root в delete_file
        if rtype == "delete_file" and make_backups:
            rev = {**rev, "__backup_root__": str(backup_root),
                   "backup": rev.get("backup", True)}

        try:
            msg = HANDLERS[rtype](target, rev)
            log_ok(f"  {msg}")
            ok_count += 1
        except Exception as e:
            fail_count += 1
            failures.append((i, str(e)))
            log_err(f"  {e}")
            if not keep_going:
                print()
                log_warn("остановлено на первой ошибке "
                         "(запусти с --keep-going, чтобы продолжить)")
                break

        print()

    print()
    print("=" * 72)
    print(f"ИТОГО: ✅ {ok_count}   ❌ {fail_count}")
    if failures:
        print("Провалившиеся правки:")
        for idx, msg in failures:
            print(f"  - [{idx}] {msg}")
    if make_backups and not dry_run and backup_root.exists():
        print(f"Бэкапы: {backup_root}")
    print("=" * 72)

    return 0 if fail_count == 0 else 1


# ──────────────────────────────────────────────────────────────
#  Точка входа
# ──────────────────────────────────────────────────────────────

def cmd_show(file_path: str, n_from: int, n_to: int) -> int:
    """Показывает строки n_from..n_to файла с номерами."""
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = (SCRIPT_DIR / p).resolve()
    if not p.exists():
        log_err(f"файл не найден: {p}")
        return 2

    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        log_err(f"не удалось прочитать {p}: {e}")
        return 2

    total = len(lines)
    a = max(1, n_from)
    b = min(total, n_to)
    if a > b:
        log_err(f"пустой диапазон: {a}..{b} (в файле {total} строк)")
        return 2

    print(f"\n{p}  ({total} строк, показываю {a}..{b})\n")
    width = len(str(b))
    for i in range(a - 1, b):
        print(f"  {i + 1:>{width}} | {lines[i]}")
    print()
    return 0


def _check_syntax_after(json_path: Path) -> None:
    """Прогоняет ast.parse() для изменённых .py-файлов."""
    import ast
    try:
        base_dir, revisions = load_revisions(json_path)
    except Exception:
        return

    seen: set[Path] = set()
    for rev in revisions:
        f = rev.get("file")
        if not f or not f.endswith(".py"):
            continue
        target = resolve_target(base_dir, f)
        if target in seen or not target.exists():
            continue
        seen.add(target)

    if not seen:
        return

    print()
    print("=" * 72)
    print("  ПРОВЕРКА СИНТАКСИСА (ast.parse)")
    print("=" * 72)
    for target in sorted(seen):
        try:
            src = target.read_text(encoding="utf-8")
            ast.parse(src, filename=str(target))
            log_ok(str(target))
        except SyntaxError as e:
            log_err(f"{target}: SyntaxError строка {e.lineno}: {e.msg}")
        except Exception as e:
            log_err(f"{target}: {type(e).__name__}: {e}")
    print("=" * 72)


def cmd_show(file_path: str, n_from: int, n_to: int) -> int:
    """Показывает строки n_from..n_to файла с номерами."""
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = (SCRIPT_DIR / p).resolve()
    if not p.exists():
        log_err(f"файл не найден: {p}")
        return 2

    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        log_err(f"не удалось прочитать {p}: {e}")
        return 2

    total = len(lines)
    a = max(1, n_from)
    b = min(total, n_to)
    if a > b:
        log_err(f"пустой диапазон: {a}..{b} (в файле {total} строк)")
        return 2

    print(f"\n{p}  ({total} строк, показываю {a}..{b})\n")
    width = len(str(b))
    for i in range(a - 1, b):
        print(f"  {i + 1:>{width}} | {lines[i]}")
    print()
    return 0


def _check_syntax_after(json_path: Path) -> None:
    """Прогоняет ast.parse() для изменённых .py-файлов."""
    import ast
    try:
        base_dir, revisions = load_revisions(json_path)
    except Exception:
        return

    seen: set[Path] = set()
    for rev in revisions:
        f = rev.get("file")
        if not f or not f.endswith(".py"):
            continue
        target = resolve_target(base_dir, f)
        if target in seen or not target.exists():
            continue
        seen.add(target)

    if not seen:
        return

    print()
    print("=" * 72)
    print("  ПРОВЕРКА СИНТАКСИСА (ast.parse)")
    print("=" * 72)
    for target in sorted(seen):
        try:
            src = target.read_text(encoding="utf-8")
            ast.parse(src, filename=str(target))
            log_ok(str(target))
        except SyntaxError as e:
            log_err(f"{target}: SyntaxError строка {e.lineno}: {e.msg}")
        except Exception as e:
            log_err(f"{target}: {type(e).__name__}: {e}")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Применяет правки из revisions.json к проекту."
    )
    parser.add_argument(
        "--file", "-f", default=str(DEFAULT_JSON),
        help="путь к JSON с правками (по умолчанию revisions.json рядом со скриптом)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="только проверить, ничего не менять",
    )
    parser.add_argument(
        "--keep-going", action="store_true",
        help="не останавливаться на первой ошибке",
    )
    parser.add_argument(
        "--backup", action="store_true",
        help="сохранять .bak-копии файлов перед правкой",
    )
    parser.add_argument(
        "--show", nargs=3, metavar=("FILE", "N", "M"),
        help="показать строки N..M файла и выйти (без применения правок)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="после правок прогнать ast.parse для изменённых .py",
    )
    args = parser.parse_args()

    if args.show:
        f, n, m = args.show
        try:
            return cmd_show(f, int(n), int(m))
        except ValueError:
            log_err("N и M должны быть числами")
            return 2

    try:
        rc = run(
            Path(args.file),
            dry_run=args.dry_run,
            keep_going=args.keep_going,
            make_backups=args.backup,
        )
        if args.check and rc == 0:
            _check_syntax_after(Path(args.file))
        return rc
    except KeyboardInterrupt:
        print("\n⚠ Прервано пользователем.")
        return 130
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
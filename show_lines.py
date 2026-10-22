# show_lines.py
"""
Показывает строки файлов из show.json — для диалога с DeepSeek.

Запуск:
    python show_lines.py                 # читает show.json рядом
    python show_lines.py --file my.json  # другой JSON
    python show_lines.py --stdin         # читает JSON из stdin

Формат show.json:
{
  "requests": [
    { "comment": "что ищем", "file": "Bot/vk_bot.py",
      "from": 200, "to": 250 },

    { "comment": "где встречается",
      "file": "Bot/vk_bot.py", "key": "catalog_open" },

    { "file": "Bot/adapters/vk/keyboards.py",
      "key": "Редактировать", "context": 3 },

    { "file": "Bot/core/models.py" }   // весь файл
  ]
}

Поля:
  file      — путь относительно cwd (или абсолютный)
  from,to   — диапазон строк (1-based, включительно)
  key       — подстрока для поиска
  context   — сколько строк вокруг найденного (по умолчанию 5)
  comment   — необязательный заголовок перед выводом
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


DEFAULT_JSON = Path(__file__).resolve().parent / "show.json"


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _resolve(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    return p


def show_range(path: Path, a: int, b: int) -> int:
    lines = _read_lines(path)
    total = len(lines)
    a = max(1, a)
    b = min(total, b)
    if a > b:
        print(f"  (пустой диапазон: {a}..{b} в файле {total} строк)")
        return 2

    print(f"  {path}  ({total} строк, показываю {a}..{b})")
    width = len(str(b))
    for i in range(a - 1, b):
        print(f"  {i + 1:>{width}} | {lines[i]}")
    return 0


def show_full(path: Path) -> int:
    lines = _read_lines(path)
    total = len(lines)
    print(f"  {path}  ({total} строк, весь файл)")
    width = len(str(total))
    for i, line in enumerate(lines, start=1):
        print(f"  {i:>{width}} | {line}")
    return 0


def search(path: Path, needle: str, context: int) -> int:
    lines = _read_lines(path)
    total = len(lines)
    hits: list[int] = []
    for i, line in enumerate(lines):
        if needle in line:
            hits.append(i)

    if not hits:
        print(f"  {path}: нет строк с {needle!r}")
        return 1

    print(f"  {path}  ({total} строк, "
          f"найдено {len(hits)} вхождений, ±{context} строк)")
    width = len(str(total))

    shown: set[int] = set()
    prev_end = -1000
    for hit in hits:
        a = max(0, hit - context)
        b = min(total - 1, hit + context)
        if a > prev_end + 1:
            print("  ─────")
        for j in range(a, b + 1):
            if j in shown:
                continue
            shown.add(j)
            marker = ">>>" if j == hit else "   "
            print(f"  {marker} {j + 1:>{width}} | {lines[j]}")
        prev_end = b
    return 0


def _run_request(req: dict, base_dir: Path) -> int:
    if not isinstance(req, dict):
        print("  ⚠ запрос должен быть объектом")
        return 2
    if "file" not in req:
        print("  ⚠ нет поля 'file'")
        return 2

    path_str = req["file"]
    p = base_dir / path_str if not Path(path_str).is_absolute() else Path(path_str)
    p = p.resolve()
    if not p.exists():
        print(f"  ❌ файл не найден: {p}")
        return 2

    comment = req.get("comment")
    if comment:
        print(f"\n  ── {comment} ──")

    if "key" in req:
        ctx = int(req.get("context", 5))
        return search(p, str(req["key"]), ctx)

    if "from" in req and "to" in req:
        return show_range(p, int(req["from"]), int(req["to"]))

    return show_full(p)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Показать строки файлов из show.json."
    )
    parser.add_argument("--file", "-f", default=str(DEFAULT_JSON),
                        help="путь к JSON (по умолчанию show.json рядом)")
    parser.add_argument("--stdin", action="store_true",
                        help="читать JSON из stdin")
    args = parser.parse_args()

    if args.stdin:
        try:
            data = json.loads(sys.stdin.read())
        except Exception as e:
            print(f"❌ некорректный JSON из stdin: {e}", file=sys.stderr)
            return 2
    else:
        p = Path(args.file).expanduser()
        if not p.exists():
            print(f"❌ JSON не найден: {p}", file=sys.stderr)
            return 2
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception as e:
            print(f"❌ некорректный JSON: {e}", file=sys.stderr)
            return 2

    if not isinstance(data, dict) or "requests" not in data:
        print("❌ ожидается объект с полем 'requests'", file=sys.stderr)
        return 2

    requests = data["requests"]
    if not isinstance(requests, list):
        print("❌ 'requests' должен быть списком", file=sys.stderr)
        return 2

    # base_dir: относительно JSON-файла или текущей папки
    base_dir_str = data.get("base_dir")
    if base_dir_str:
        base_dir = Path(base_dir_str).expanduser()
        if not base_dir.is_absolute():
            base_dir = (Path.cwd() / base_dir).resolve()
    else:
        base_dir = Path.cwd()

    print("=" * 72)
    print(f"  SHOW — {len(requests)} запрос(ов)")
    print(f"  base_dir: {base_dir}")
    print("=" * 72)

    rc = 0
    for i, req in enumerate(requests, start=1):
        print(f"\n[{i}/{len(requests)}]")
        try:
            r = _run_request(req, base_dir)
            if r != 0:
                rc = r
        except Exception as e:
            print(f"  ❌ ошибка: {type(e).__name__}: {e}")
            rc = 1

    print()
    print("=" * 72)
    return rc


if __name__ == "__main__":
    sys.exit(main())
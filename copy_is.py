#!/usr/bin/env python3
"""
copy_is.py — копирует содержимое указанных файлов в буфер обмена.

Примеры:
    python copy_is.py main.py utils.py config.json
    python copy_is.py src/ -r
    python copy_is.py . -r -o all_code.txt
    python copy_is.py src/ -r --list
"""

import argparse
import os
import sys

try:
    import tkinter as tk
    HAS_TK = True
except ImportError:
    HAS_TK = False

MAX_COPY_BYTES = 20 * 1024 * 1024


def fix_console_encoding():
    """Чтобы кириллица в Windows-консоли не падала с UnicodeEncodeError."""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def is_binary(text):
    return "\x00" in text[:4096]


def display_path(path):
    """Красивый путь: относительно cwd, если возможно."""
    try:
        rel = os.path.relpath(path)
        if not rel.startswith(".."):
            return rel.replace(os.sep, "/")
    except ValueError:
        pass
    return path.replace(os.sep, "/")


def iter_files(paths, recursive):
    for raw in paths:
        p = os.path.abspath(raw)
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            if recursive:
                for root, dirs, files in os.walk(p):
                    dirs.sort()  # стабильный порядок
                    for name in sorted(files):
                        yield os.path.join(root, name)
            else:
                for name in sorted(os.listdir(p)):
                    full = os.path.join(p, name)
                    if os.path.isfile(full):
                        yield full
        else:
            print(f"[!] Не найдено: {raw}", file=sys.stderr)


def build_text(files, max_bytes):
    parts = []
    total = 0
    included = 0
    skipped = 0
    truncated = False

    for path in files:
        rel = display_path(path)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            parts.append(f"\n=== {rel} (ошибка чтения: {e}) ===\n")
            skipped += 1
            continue

        if is_binary(content):
            parts.append(f"\n=== {rel} (бинарный файл, пропущен) ===\n")
            skipped += 1
            continue

        block = f"\n=== {rel} ===\n{content}\n"
        block_bytes = len(block.encode("utf-8"))

        if total + block_bytes > max_bytes:
            truncated = True
            parts.append(
                f"\n=== {rel} (пропущен: превышен лимит {max_bytes} байт) ===\n"
            )
            skipped += 1
            continue

        parts.append(block)
        total += block_bytes
        included += 1

    if truncated:
        parts.append(
            f"\n=== ВНИМАНИЕ: часть файлов пропущена из-за лимита "
            f"{max_bytes} байт ===\n"
        )

    return "".join(parts), included, skipped


def copy_to_clipboard(text):
    """Копирует текст в буфер. Возвращает True при успехе."""
    if HAS_TK:
        try:
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()   # важно: именно update() отправляет данные в буфер
            root.destroy()
            return True
        except Exception as e:
            print(f"[!] tkinter: {e}", file=sys.stderr)

    # Fallback: pyperclip, если установлен
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        pass

    return False


def main():
    fix_console_encoding()

    parser = argparse.ArgumentParser(
        description="Копирует содержимое указанных файлов в буфер обмена "
                    "или в файл.",
    )
    parser.add_argument(
        "paths", nargs="+",
        help="Файлы или папки (для папок без -r — только верхний уровень)",
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true",
        help="Обходить папки рекурсивно",
    )
    parser.add_argument(
        "-o", "--output",
        help="Сохранить в файл вместо копирования в буфер",
    )
    parser.add_argument(
        "-p", "--print", dest="print_stdout", action="store_true",
        help="Вывести в stdout вместо буфера",
    )
    parser.add_argument(
        "--list", dest="list_only", action="store_true",
        help="Только показать список файлов, которые будут обработаны",
    )
    parser.add_argument(
        "--max-bytes", type=int, default=MAX_COPY_BYTES,
        help=f"Максимальный размер результата в байтах "
             f"(по умолчанию {MAX_COPY_BYTES})",
    )
    args = parser.parse_args()

    files = list(iter_files(args.paths, args.recursive))
    if not files:
        print("[!] Не найдено ни одного файла.", file=sys.stderr)
        return 1

    if args.list_only:
        print(f"Будет обработано файлов: {len(files)}")
        for f in files:
            print("  •", display_path(f))
        return 0

    text, included, skipped = build_text(files, args.max_bytes)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            print(f"[!] Не удалось сохранить: {e}", file=sys.stderr)
            return 1
        print(f"[✓] Сохранено в {args.output}: "
              f"{included} файл(ов), {len(text)} символов")
        return 0

    if args.print_stdout:
        print(text)
        return 0

    if copy_to_clipboard(text):
        size_kb = len(text.encode("utf-8")) / 1024
        print(f"[✓] Скопировано: {included} файл(ов), "
              f"{len(text)} символов ({size_kb:.1f} КБ)")
        if skipped:
            print(f"    Пропущено: {skipped} файл(ов) "
                  f"(бинарные / ошибки / лимит)")
        return 0

    print("[!] Не удалось скопировать в буфер.", file=sys.stderr)
    print("    Используйте -o out.txt, чтобы сохранить в файл.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

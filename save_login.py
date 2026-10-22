#!/usr/bin/env python3
"""
save_login.py — сохраняет GitHub-логин в .git-user.

Использование:
    python save_login.py                          # спросит логин
    python save_login.py mahoninegor-sudo         # сразу задать
    python save_login.py --show                   # показать текущий
    python save_login.py --remove                 # удалить .git-user

Логин используется push_git.py для подстановки в URL при пуше
(вместе с токеном из .git-token).
"""

import argparse
import os
import stat
import sys

USER_FILE = ".git-user"
GITIGNORE = ".gitignore"


def fix_console_encoding():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def ensure_gitignore():
    """Добавляет .git-user в .gitignore, если его там нет."""
    lines = []
    if os.path.exists(GITIGNORE):
        with open(GITIGNORE, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    for line in lines:
        if line.strip() in (USER_FILE, f"/{USER_FILE}"):
            print(f"[i] {USER_FILE} уже в .gitignore")
            return

    with open(GITIGNORE, "a", encoding="utf-8") as f:
        if lines and lines[-1].strip():
            f.write("\n")
        f.write(f"\n# GitHub login — не коммитить\n{USER_FILE}\n")
    print(f"[✓] {USER_FILE} добавлен в .gitignore")


def restrict_permissions(path):
    if os.name != "nt":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def save_login(username=None):
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r", encoding="utf-8") as f:
            old = f.read().strip()
        print(f"[i] Уже сохранён логин: {old}")
        if input("Перезаписать? [y/N]: ").strip().lower() != "y":
            print("Отменено.")
            return old

    if not username:
        username = input("Ваш GitHub-логин: ").strip()

    if not username:
        print("[!] Логин пуст — отмена.")
        return None

    # Простая валидация: буквы, цифры, дефис
    if not all(c.isalnum() or c == "-" for c in username):
        print("[!] Логин содержит недопустимые символы "
              "(ожидается буквы/цифры/дефис).")
        if input("Всё равно сохранить? [y/N]: ").strip().lower() != "y":
            return None

    with open(USER_FILE, "w", encoding="utf-8") as f:
        f.write(username + "\n")
    restrict_permissions(USER_FILE)
    print(f"[✓] Логин сохранён в {USER_FILE}: {username}")
    return username


def show_login():
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r", encoding="utf-8") as f:
            print(f"[i] Логин из {USER_FILE}: {f.read().strip()}")
    elif os.environ.get("GITHUB_USER"):
        print(f"[i] Из GITHUB_USER env: {os.environ['GITHUB_USER']}")
    else:
        print("[!] Логин не сохранён и переменная GITHUB_USER не задана.")


def remove_login():
    if not os.path.exists(USER_FILE):
        print(f"[i] {USER_FILE} не существует.")
        return
    if input(f"Удалить {USER_FILE}? [y/N]: ").strip().lower() == "y":
        os.remove(USER_FILE)
        print(f"[✓] {USER_FILE} удалён.")


def main():
    fix_console_encoding()
    parser = argparse.ArgumentParser(
        description="Сохраняет GitHub-логин в .git-user."
    )
    parser.add_argument("username", nargs="?",
                        help="Логин (если не указан — спросит)")
    parser.add_argument("--show", action="store_true",
                        help="Показать сохранённый логин")
    parser.add_argument("--remove", action="store_true",
                        help="Удалить .git-user")
    args = parser.parse_args()

    print("=== Настройка GitHub-логина ===\n")

    if args.show:
        show_login()
        return 0
    if args.remove:
        remove_login()
        return 0

    ensure_gitignore()
    save_login(args.username)
    print("\nГотово. Теперь push_git.py не будет спрашивать логин.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
"""
create_token.py — создаёт .git-token для хранения GitHub Personal Access Token.

Использование:
    python create_token.py              # создать .git-token в текущей папке
    python create_token.py --setup-git  # + настроить credential.helper store
    python create_token.py --show       # показать сохранённый токен (маскированно)
    python create_token.py --remove     # удалить .git-token

⚠️ Токен хранится в открытом виде. Скрипт добавляет его в .gitignore,
   но НЕ коммитьте .git-token в репозиторий ни при каких обстоятельствах.
"""

import argparse
import getpass
import os
import stat
import sys

TOKEN_FILE = ".git-token"
GITIGNORE = ".gitignore"
PAT_LINK = "https://github.com/settings/tokens"


def fix_console_encoding():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def ensure_gitignore():
    """Добавляет .git-token в .gitignore, если его там ещё нет."""
    lines = []
    if os.path.exists(GITIGNORE):
        with open(GITIGNORE, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    for line in lines:
        if line.strip() in (TOKEN_FILE, f"/{TOKEN_FILE}"):
            print(f"[i] {TOKEN_FILE} уже в .gitignore")
            return

    with open(GITIGNORE, "a", encoding="utf-8") as f:
        if lines and lines[-1].strip():
            f.write("\n")
        f.write(f"\n# GitHub PAT — НЕ КОММИТИТЬ\n{TOKEN_FILE}\n")
    print(f"[✓] {TOKEN_FILE} добавлен в .gitignore")


def restrict_permissions(path):
    """На Unix — только владельцу (600). На Windows — noop (но не мешает)."""
    if os.name != "nt":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def create_token():
    if os.path.exists(TOKEN_FILE):
        print(f"[!] {TOKEN_FILE} уже существует.")
        ans = input("Перезаписать? [y/N]: ").strip().lower()
        if ans != "y":
            print("Отменено.")
            return None

    print(f"\nСоздать токен: {PAT_LINK}")
    print("• Classic token → права: repo")
    print("• Fine-grained → Contents: Read and write\n")

    token = getpass.getpass("Вставьте токен (ввод не отображается): ").strip()
    if not token:
        print("[!] Пустой токен — отмена.")
        return None

    if not (token.startswith("ghp_") or token.startswith("github_pat_")):
        print("[!] Токен не похож на GitHub PAT (ожидалось ghp_ или github_pat_)")
        if input("Всё равно сохранить? [y/N]: ").strip().lower() != "y":
            return None

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    restrict_permissions(TOKEN_FILE)
    print(f"[✓] Токен сохранён в {TOKEN_FILE}")
    return token


def read_token():
    """Читает токен из файла или из env GITHUB_TOKEN. None, если нет."""
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            tok = f.read().strip()
            if tok:
                return tok
    return os.environ.get("GITHUB_TOKEN")


def setup_git_credentials(token):
    """
    Настраивает стандартный credential.helper store и прописывает токен
    в ~/.git-credentials. После этого `git push` НЕ спросит пароль.
    """
    if not token:
        print("[!] Нет токена для настройки git.")
        return

    username = os.environ.get("GITHUB_USER") or input(
        "Ваш GitHub-логин: "
    ).strip()
    if not username:
        print("[!] Логин не указан — отмена.")
        return

    cred_path = os.path.expanduser("~/.git-credentials")
    entry = f"https://{username}:{token}@github.com"

    lines = []
    if os.path.exists(cred_path):
        with open(cred_path, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines()
                     if "github.com" not in l]
    lines.append(entry)

    with open(cred_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    restrict_permissions(cred_path)

    os.system("git config --global credential.helper store")
    print(f"[✓] Настроено: {cred_path}")
    print("    Теперь `git push` работает без пароля.")


def show_token():
    tok = read_token()
    if not tok:
        print("[!] Токен не найден.")
        return
    masked = tok[:7] + "…" + tok[-4:] if len(tok) > 15 else "***"
    print(f"[i] Источник: "
          f"{TOKEN_FILE if os.path.exists(TOKEN_FILE) else 'GITHUB_TOKEN env'}")
    print(f"[i] Токен: {masked} (длина {len(tok)})")


def remove_token():
    if not os.path.exists(TOKEN_FILE):
        print(f"[i] {TOKEN_FILE} не существует — нечего удалять.")
        return
    ans = input(f"Удалить {TOKEN_FILE}? [y/N]: ").strip().lower()
    if ans == "y":
        os.remove(TOKEN_FILE)
        print(f"[✓] {TOKEN_FILE} удалён.")


def main():
    fix_console_encoding()

    parser = argparse.ArgumentParser(
        description="Создаёт .git-token для хранения GitHub PAT."
    )
    parser.add_argument("--setup-git", action="store_true",
                        help="Также настроить git credential.helper store")
    parser.add_argument("--show", action="store_true",
                        help="Показать сохранённый токен (маскированно)")
    parser.add_argument("--remove", action="store_true",
                        help="Удалить .git-token")
    args = parser.parse_args()

    print("=== Настройка GitHub PAT ===\n")

    if args.show:
        show_token()
        return 0

    if args.remove:
        remove_token()
        return 0

    ensure_gitignore()
    token = create_token()
    if not token:
        return 1

    if args.setup_git:
        print()
        setup_git_credentials(token)

    print("\nГотово.")
    print(f"  • При ручном `git push` логин = ваш ник, пароль = содержимое {TOKEN_FILE}")
    print(f"  • Автоматически: `python create_token.py --setup-git` (один раз)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
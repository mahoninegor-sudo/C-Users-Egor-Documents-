#!/usr/bin/env python3
"""
push_git.py — заливает локальный репозиторий в GitHub «как есть».

Что делает:
    1. git add -A .             — индексирует все изменения (включая удаления)
    2. git commit (если есть)   — создаёт коммит с авто-сообщением
    3. git push --force-with-lease origin <branch>
                                — перезаписывает ветку на GitHub локальным
                                  состоянием: файлы, которых нет локально,
                                  удаляются с GitHub; ваши коммиты — заливаются.

Логин и токен берутся из файлов:
    .git-user   — создаётся `python save_login.py`
    .git-token  — создаётся `python create_token.py`

Использование:
    python push_git.py                    # закоммитить и залить как есть
    python push_git.py -m "fix bug"       # своё сообщение коммита
    python push_git.py --branch dev       # другая ветка
    python push_git.py --force            # чистый --force (без проверки lease)
    python push_git.py --no-force         # обычный push (без перезаписи)
    python push_git.py --dry-run          # только показать, что будет сделано
"""

import argparse
import datetime
import os
import subprocess
import sys

TOKEN_FILE = ".git-token"
USER_FILE = ".git-user"


def fix_console_encoding():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def run(cmd, check=True, capture=True):
    try:
        result = subprocess.run(
            cmd, capture_output=capture, text=True,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        print(f"[!] Команда не найдена: {cmd[0]}")
        if check:
            sys.exit(1)
        return 1, "", ""

    if check and result.returncode != 0:
        print(f"[!] Ошибка: {' '.join(cmd)}")
        if result.stdout:
            print(result.stdout.strip())
        if result.stderr:
            print(result.stderr.strip())
        sys.exit(result.returncode)
    return result.returncode, result.stdout, result.stderr


def ensure_git_repo():
    code, _, _ = run(["git", "rev-parse", "--is-inside-work-tree"], check=False)
    if code != 0:
        print("[!] Это не Git-репозиторий. Выполните `git init`.")
        sys.exit(1)


def current_branch():
    _, out, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip()


def has_staged_changes():
    """True, если после `git add` есть что коммитить."""
    code, _, _ = run(["git", "diff", "--cached", "--quiet"], check=False)
    return code != 0


def read_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get("GITHUB_TOKEN", "").strip()


def read_user():
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get("GITHUB_USER", "").strip()


def setup_auth_env(user, token):
    """Подставляет user:token в URL origin. Возвращает исходный URL или None."""
    if not user or not token:
        return None
    _, out, _ = run(["git", "remote", "get-url", "origin"], check=False)
    url = out.strip()
    if not url.startswith("https://") or "@" in url:
        return None  # SSH или уже с логином — не трогаем

    new_url = url.replace("https://", f"https://{user}:{token}@")
    run(["git", "remote", "set-url", "origin", new_url])
    print(f"[i] origin: подставлен токен (user={user})")
    return url


def restore_remote_url(original):
    if not original:
        return
    run(["git", "remote", "set-url", "origin", original], check=False)


def main():
    fix_console_encoding()

    parser = argparse.ArgumentParser(
        description="Заливает локальный репозиторий в GitHub «как есть» "
                    "(force push)."
    )
    parser.add_argument("-m", "--message", help="Сообщение коммита")
    parser.add_argument("--branch", help="Ветка (по умолчанию — текущая)")
    parser.add_argument("--force", action="store_true",
                        help="Использовать чистый --force вместо "
                             "--force-with-lease (опаснее)")
    parser.add_argument("--no-force", action="store_true",
                        help="Обычный push без перезаписи истории")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать, что будет сделано")
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")
    ensure_git_repo()

    branch = args.branch or current_branch()
    if not branch:
        print("[!] Не удалось определить ветку.")
        sys.exit(1)
    print(f"[i] Ветка: {branch}")

    # --- git add -A ---
    print("[→] git add -A .")
    run(["git", "add", "-A", "."])

    # --- commit только если есть что коммитить ---
    if has_staged_changes():
        message = args.message or (
            f"update: {datetime.datetime.now():%Y-%m-%d %H:%M}"
        )
        print(f"[→] git commit -m {message!r}")
        run(["git", "commit", "-m", message])
    else:
        print("[i] Нечего коммитить — коммит не создаётся.")

    if args.dry_run:
        if args.no_force:
            mode = f"git push origin {branch}"
        elif args.force:
            mode = f"git push --force origin {branch}"
        else:
            mode = f"git push --force-with-lease origin {branch}"
        print(f"\n[dry-run] Дальше был бы: {mode}")
        return 0

    # --- auth ---
    user, token = read_user(), read_token()
    if not user or not token:
        print("[!] Нет .git-user / .git-token — git может спросить пароль.")
    original_url = None
    if user and token:
        original_url = setup_auth_env(user, token)

    # --- push ---
    push_cmd = ["git", "push"]
    if args.force:
        push_cmd.append("--force")
    elif not args.no_force:
        push_cmd.append("--force-with-lease")
    push_cmd.extend(["origin", branch])

    print(f"[→] {' '.join(push_cmd)}")
    try:
        code, out, err = run(push_cmd, check=False)
    finally:
        if original_url:
            restore_remote_url(original_url)

    if code != 0:
        # Попытка №2: если ветки нет на remote — создать её
        if ("no upstream" in (err or out)
                or "does not match any" in (err or out)):
            print(f"[→] git push -u origin {branch}")
            if user and token:
                original_url = setup_auth_env(user, token)
            try:
                code, out, err = run(
                    ["git", "push", "-u", "origin", branch], check=False
                )
            finally:
                if original_url:
                    restore_remote_url(original_url)
        if code != 0:
            print("[!] push не удался:")
            print((out or err).strip())
            return code

    print("[✓] Готово — GitHub теперь точная копия локальной папки.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

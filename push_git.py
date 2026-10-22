#!/usr/bin/env python3
"""
push_git.py — коммитит и пушит изменения в GitHub одной командой.

Логин и токен берутся из файлов:
    .git-user   — создаётся `python save_login.py`
    .git-token  — создаётся `python create_token.py`

Использование:
    python push_git.py                     # авто-сообщение "update: YYYY-MM-DD HH:MM"
    python push_git.py -m "fix bug"        # своё сообщение
    python push_git.py --pull              # сначала git pull --rebase
    python push_git.py --dry-run           # показать, что будет сделано
    python push_git.py --branch dev        # другая ветка
    python push_git.py --require-auth      # упасть, если нет .git-user/.git-token
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


def has_changes():
    _, out, _ = run(["git", "status", "--porcelain"])
    return bool(out.strip())

def count_changes():
    _, out, _ = run(["git", "status", "--porcelain"])
    lines = [l for l in out.splitlines() if l.strip()]
    added    = sum(1 for l in lines if l.startswith("A") or l.startswith("??"))
    modified = sum(1 for l in lines if l.startswith(" M") or l.startswith("M"))
    deleted  = sum(1 for l in lines if l.startswith(" D") or l.startswith("D"))
    return added, modified, deleted, len(lines)


# ---------- читаем логин/токен из файлов ----------

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


def check_auth_files(require=False):
    """Возвращает (user, token). Если чего-то нет — сообщает."""
    user  = read_user()
    token = read_token()

    missing = []
    if not user:
        missing.append(f"{USER_FILE}  (создать: python save_login.py)")
    if not token:
        missing.append(f"{TOKEN_FILE} (создать: python create_token.py)")

    if not missing:
        return user, token

    print("[!] Не найдены данные для авторизации:")
    for m in missing:
        print(f"    • {m}")

    if require:
        print("\n[!] --require-auth: завершаю без пуша.")
        sys.exit(1)

    print("\n[i] Продолжаю без подстановки токена — git может спросить пароль.")
    return user, token


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


# ---------- main ----------

def main():
    fix_console_encoding()

    parser = argparse.ArgumentParser(
        description="Коммит + пуш в GitHub одной командой."
    )
    parser.add_argument("-m", "--message", help="Сообщение коммита")
    parser.add_argument("--pull", action="store_true",
                        help="Сначала git pull --rebase")
    parser.add_argument("--branch", help="Ветка (по умолчанию — текущая)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать, что будет сделано")
    parser.add_argument("--no-token", action="store_true",
                        help="Не подставлять токен из .git-token")
    parser.add_argument("--require-auth", action="store_true",
                        help="Упасть, если нет .git-user / .git-token")
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")
    ensure_git_repo()

    # Читаем логин/токен заранее — один раз
    if args.no_token:
        user, token = "", ""
    else:
        user, token = check_auth_files(require=args.require_auth)

    branch = args.branch or current_branch()
    if not branch:
        print("[!] Не удалось определить ветку.")
        sys.exit(1)
    print(f"[i] Ветка: {branch}")

        # --- проверить изменения ---
    if not has_changes():
        # Есть ли неотправленные коммиты?
        _, out, _ = run(
            ["git", "rev-list", "--count", f"origin/{branch}..HEAD"],
            check=False,
        )
        ahead = int(out.strip() or "0")
        if ahead > 0:
            print(f"[i] Изменений нет, но есть {ahead} неотправленных "
                  f"коммит(ов) — пушу.")
        else:
            print("[i] Изменений нет и всё отправлено — делать нечего.")
            return 0
    else:
        added, modified, deleted, total = count_changes()
        print(f"[i] Изменений: {total} "
              f"(+{added} / ~{modified} / -{deleted})")
        if args.dry_run:
            run(["git", "status", "--short"], check=False)
            print("\n[dry-run] Дальше были бы: "
                  "git add . → git commit → git push")
            return 0

        print("[→] git add .")
        run(["git", "add", "."])

        message = args.message or (
            f"update: {datetime.datetime.now():%Y-%m-%d %H:%M}"
        )
        print(f"[→] git commit -m {message!r}")
        run(["git", "commit", "-m", message])

    # --- опционально pull ---
    if args.pull:
        print("[→] git pull --rebase")
        code, out, err = run(["git", "pull", "--rebase"], check=False)
        if code != 0:
            print("[!] Конфликт при pull. Разрешите вручную и запустите снова.")
            print(out.strip() or err.strip())
            return 1

    # --- пуш с подстановкой токена ---
    original_url = None
    if user and token:
        original_url = setup_auth_env(user, token)

    print(f"[→] git push origin {branch}")
    try:
        code, out, err = run(["git", "push", "origin", branch], check=False)
    finally:
        if original_url:
            restore_remote_url(original_url)

    if code != 0:
        # Если ветка не отслеживается — ставим upstream и пробуем снова
        if "no upstream" in (err or out) or "set-upstream" in (err or out):
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

    print("[✓] Готово.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

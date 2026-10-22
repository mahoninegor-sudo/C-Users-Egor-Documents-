#!/usr/bin/env python3
"""
push_git.py — коммитит и пушит изменения в GitHub одной командой.

Использование:
    python push_git.py                     # авто-сообщение "update: YYYY-MM-DD HH:MM"
    python push_git.py -m "fix bug"        # своё сообщение
    python push_git.py --pull              # сначала git pull, потом push
    python push_git.py --dry-run           # показать, что будет сделано
    python push_git.py --branch dev        # другая ветка
"""

import argparse
import datetime
import os
import subprocess
import sys

TOKEN_FILE = ".git-token"


def fix_console_encoding():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def run(cmd, check=True, capture=True):
    """Запускает команду, возвращает (код, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
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


def setup_auth_env():
    """
    Если есть .git-token — подставляет его в URL origin для этого процесса,
    чтобы не вводить пароль. Не модифицирует .git/config.
    """
    if not os.path.exists(TOKEN_FILE):
        return
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        token = f.read().strip()
    if not token:
        return

    _, out, _ = run(["git", "remote", "get-url", "origin"], check=False)
    url = out.strip()
    if not url.startswith("https://") or "@" in url:
        return  # SSH или уже с логином — не трогаем

    # Спрашиваем логин один раз (или берём из GITHUB_USER)
    user = os.environ.get("GITHUB_USER", "").strip()
    if not user:
        user = input("GitHub-логин: ").strip()
    if not user:
        return

    new_url = url.replace("https://", f"https://{user}:{token}@")
    run(["git", "remote", "set-url", "origin", new_url])
    print(f"[i] origin настроен с токеном из {TOKEN_FILE}")


def restore_remote_url():
    """Убирает токен из URL origin обратно на чистый https."""
    _, out, _ = run(["git", "remote", "get-url", "origin"], check=False)
    url = out.strip()
    if "://" not in url or "@" not in url:
        return
    # https://user:token@github.com/foo/bar.git -> https://github.com/foo/bar.git
    clean = url.split("://", 1)[1].split("@", 1)[1]
    run(["git", "remote", "set-url", "origin", "https://" + clean],
        check=False)


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
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")
    ensure_git_repo()

    branch = args.branch or current_branch()
    if not branch:
        print("[!] Не удалось определить ветку.")
        sys.exit(1)
    print(f"[i] Ветка: {branch}")

    # --- проверить изменения ---
    if not has_changes():
        print("[i] Изменений нет — коммит и пуш не нужны.")
        # Но если ветка ещё не на remote — всё равно пушим
        code, _, _ = run(
            ["git", "rev-parse", "--abbrev-ref", f"origin/{branch}"],
            check=False,
        )
        if code != 0:
            print(f"[i] Ветка {branch} не на remote — пушу.")
        else:
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

        # --- git add ---
        print("[→] git add .")
        run(["git", "add", "."])

        # --- git commit ---
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

    # --- настройка токена (если есть) ---
    if not args.no_token:
        setup_auth_env()

    # --- git push ---
    print(f"[→] git push origin {branch}")
    try:
        code, out, err = run(["git", "push", "origin", branch], check=False)
    finally:
        if not args.no_token:
            restore_remote_url()

    if code != 0:
        # Если ветка не отслеживается — ставим upstream и пробуем снова
        if "no upstream" in (err or out) or "set-upstream" in (err or out):
            print(f"[→] git push -u origin {branch}")
            code, out, err = run(
                ["git", "push", "-u", "origin", branch], check=False
            )
        if code != 0:
            print("[!] push не удался:")
            print((out or err).strip())
            return code

    print("[✓] Готово.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
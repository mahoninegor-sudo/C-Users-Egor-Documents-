#!/usr/bin/env python3
"""
push_git.py — заливает локальный репозиторий в GitHub «как есть».

Что делает:
    1. Создаёт .gitkeep в пустых папках — чтобы на GitHub появились
       такие же папки, как локально (git не хранит пустые каталоги).
    2. git add -A .             — индексирует все изменения (включая удаления)
    3. git commit (если есть)   — создаёт коммит с авто-сообщением
    4. git push --force-with-lease origin <branch>
                                — перезаписывает ветку на GitHub локальным
                                  состоянием: файлы, которых нет локально,
                                  удаляются с GitHub; ваши коммиты — заливаются.

Репозиторий определяется по пути: можно передать файл или папку — скрипт
сам найдёт git-репозиторий, в котором они лежат.

Логин и токен берутся из файлов:
    .git-user   — создаётся `python save_login.py`
    .git-token  — создаётся `python create_token.py`
Если их нет — работает Git Credential Manager (git спросит пароль один раз)
или переменные окружения GITHUB_USER / GITHUB_TOKEN.

Использование:
    python push_git.py                     # репозиторий, где лежит сам скрипт
    python push_git.py C:\\path\\to\\file.py  # репозиторий, в котором лежит файл
    python push_git.py ..\\project           # репозиторий, в котором лежит папка
    python push_git.py -m "fix bug"        # своё сообщение коммита
    python push_git.py --branch dev        # другая ветка
    python push_git.py --force             # чистый --force (без проверки lease)
    python push_git.py --no-force          # обычный push (без перезаписи)
    python push_git.py --no-keep-empty     # не создавать .gitkeep
    python push_git.py --dry-run           # только показать, что будет сделано
"""

import argparse
import datetime
import os
import re
import subprocess
import sys

TOKEN_FILE = ".git-token"
USER_FILE = ".git-user"
KEEP_FILE = ".gitkeep"


def fix_console_encoding():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def run(cmd, check=True, cwd=None):
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=cwd,
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


def resolve_repo_root(start):
    """Ищет корень git-репозитория, в котором лежит файл/папка start."""
    start = os.path.abspath(start)
    if os.path.isfile(start):
        start = os.path.dirname(start)

    code, out, err = run(
        ["git", "-C", start, "rev-parse", "--show-toplevel"], check=False
    )
    if code != 0 or not out.strip():
        print(f"[!] Не git-репозиторий: {start}")
        if (err or "").strip():
            print((err or "").strip())
        sys.exit(1)
    return os.path.normpath(out.strip())


def current_branch(root):
    # symbolic-ref работает даже до первого коммита
    code, out, _ = run(
        ["git", "-C", root, "symbolic-ref", "--short", "-q", "HEAD"],
        check=False,
    )
    if code == 0 and out.strip():
        return out.strip()
    code, out, _ = run(
        ["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"], check=False
    )
    return out.strip() if code == 0 else ""


def has_staged_changes(root):
    """True, если после `git add` есть что коммитить."""
    code, out, _ = run(
        ["git", "-C", root, "status", "--porcelain"], check=False
    )
    return code == 0 and bool(out.strip())


def keep_empty_dirs(root, dry_run=False):
    """
    Создаёт .gitkeep в пустых папках, чтобы git (и GitHub) их сохранил.

    Возвращает (список_относительных_путей, число_пропущенных_по_gitignore).
    Папки из .gitignore не трогаем.
    """
    created = []
    skipped = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        if filenames or dirnames:
            continue
        if os.path.basename(dirpath) == ".git":
            continue

        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        code, _, _ = run(
            ["git", "-C", root, "check-ignore", "-q", "--", dirpath],
            check=False,
        )
        if code == 0:          # папка игнорируется — .gitkeep не нужен
            skipped += 1
            continue

        keep = os.path.join(dirpath, KEEP_FILE)
        if not os.path.exists(keep):
            if not dry_run:
                try:
                    open(keep, "a", encoding="utf-8").close()
                except OSError as e:
                    print(f"[!] Не удалось создать {keep}: {e}")
                    continue
            created.append(rel)

    return created, skipped


def report_keep(created, skipped, dry_run):
    if not created and not skipped:
        return
    verb = "будут помечены" if dry_run else "помечены"
    line = f"[i] Пустые папки: {verb} {len(created)} шт. ({KEEP_FILE})"
    if skipped:
        line += f", пропущено по .gitignore: {skipped}"
    print(line)
    for rel in created[:20]:
        print(f"    + {rel}/{KEEP_FILE}")
    if len(created) > 20:
        print(f"    … и ещё {len(created) - 20}")


def _read_credential_file(path):
    """Читает файл без BOM; пустые строки и пробелы по краям отбрасываются."""
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read().strip()


def read_token():
    return _read_credential_file(TOKEN_FILE) or \
        (os.environ.get("GITHUB_TOKEN", "") or "").strip()


def read_user():
    return _read_credential_file(USER_FILE) or \
        (os.environ.get("GITHUB_USER", "") or "").strip()


def valid_username(user):
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", user or ""))


def valid_token(token):
    # GitHub PAT: ghp_..., github_pat_...; допускаем буквы/цифры/подчёркивания
    return bool(re.fullmatch(r"[A-Za-z0-9_]{20,}", token or ""))


def setup_auth_env(root, user, token):
    """Подставляет user:token в URL origin. Возвращает исходный URL или None."""
    if not user or not token:
        return None
    if not valid_username(user):
        print(f"[!] .git-user выглядит некорректно ({user!r}) — "
              f"подстановка токена пропущена.")
        return None
    if not valid_token(token):
        print("[!] .git-token пустой или содержит мусор — подстановка "
              "токена пропущена (работает Git Credential Manager).")
        return None

    _, out, _ = run(["git", "-C", root, "remote", "get-url", "origin"],
                    check=False)
    url = out.strip()
    if not url.startswith("https://") or "@" in url:
        return None  # SSH или уже с логином — не трогаем

    new_url = url.replace("https://", f"https://{user}:{token}@", 1)
    run(["git", "-C", root, "remote", "set-url", "origin", new_url])
    print(f"[i] origin: подставлен токен (user={user})")
    return url


def restore_remote_url(root, original):
    if not original:
        return
    run(["git", "-C", root, "remote", "set-url", "origin", original],
        check=False)


def sanitize_url(url):
    """Убирает логин/токен из https-URL перед печатью."""
    return re.sub(r"//[^/@]+@", "//", url or "")


def push_command(branch, args):
    cmd = ["git", "push"]
    if args.force:
        cmd.append("--force")
    elif not args.no_force:
        cmd.append("--force-with-lease")
    cmd.extend(["origin", branch])
    return cmd


def main():
    fix_console_encoding()

    parser = argparse.ArgumentParser(
        description="Заливает локальный репозиторий в GitHub «как есть» "
                    "(force push), сохраняя все папки."
    )
    parser.add_argument(
        "path", nargs="?", default=None,
        help="файл или папка внутри репозитория "
             "(по умолчанию — папка, где лежит сам скрипт)",
    )
    parser.add_argument("-m", "--message", help="Сообщение коммита")
    parser.add_argument("--branch", help="Ветка (по умолчанию — текущая)")
    parser.add_argument("--force", action="store_true",
                        help="Использовать чистый --force вместо "
                             "--force-with-lease (опаснее)")
    parser.add_argument("--no-force", action="store_true",
                        help="Обычный push без перезаписи истории")
    parser.add_argument("--no-keep-empty", dest="keep_empty",
                        action="store_false",
                        help="Не создавать .gitkeep в пустых папках")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать, что будет сделано")
    args = parser.parse_args()

    root = resolve_repo_root(
        args.path or os.path.dirname(os.path.abspath(__file__))
    )
    print(f"[i] Репозиторий: {root}")

    branch = (args.branch or current_branch(root)).strip()
    if not branch:
        print("[!] Не удалось определить ветку.")
        return 1
    print(f"[i] Ветка: {branch}")

    # --- .gitkeep для пустых папок ---
    created, skipped = ([], 0)
    if args.keep_empty:
        created, skipped = keep_empty_dirs(root, dry_run=args.dry_run)
        report_keep(created, skipped, dry_run=args.dry_run)

    # --- dry-run ---
    if args.dry_run:
        _, out, _ = run(["git", "add", "-A", "--dry-run", "."],
                        check=False, cwd=root)
        print("[dry-run] git add -A .")
        print(out.strip() or "    (изменений нет)")
        print(f"\n[dry-run] Дальше был бы: "
              f"{' '.join(push_command(branch, args))}")
        return 0

    # --- git add -A ---
    print("[→] git add -A .")
    run(["git", "add", "-A", "."], cwd=root)

    # --- commit только если есть что коммитить ---
    if has_staged_changes(root):
        message = args.message or (
            f"update: {datetime.datetime.now():%Y-%m-%d %H:%M}"
        )
        print(f"[→] git commit -m {message!r}")
        run(["git", "commit", "-m", message], cwd=root)
    else:
        print("[i] Нечего коммитить — коммит не создаётся.")

    # --- auth ---
    user, token = read_user(), read_token()
    original_url = None
    if user and token:
        original_url = setup_auth_env(root, user, token)
    else:
        print("[i] Авторизация: Git Credential Manager "
              "(нет .git-user/.git-token).")

    # --- push ---
    push_cmd = push_command(branch, args)
    print(f"[→] {' '.join(push_cmd)}")
    try:
        code, out, err = run(push_cmd, check=False, cwd=root)
    finally:
        if original_url:
            restore_remote_url(root, original_url)

    if code != 0:
        # Попытка №2: если ветки нет на remote — создать её
        text = f"{out}\n{err}"
        if ("no upstream" in text or "does not match any" in text
                or "has no upstream branch" in text):
            print(f"[→] git push -u origin {branch}")
            if user and token:
                original_url = setup_auth_env(root, user, token)
            try:
                code, out, err = run(
                    ["git", "-C", root, "push", "-u", "origin", branch],
                    check=False,
                )
            finally:
                if original_url:
                    restore_remote_url(root, original_url)
        if code != 0:
            print("[!] push не удался:")
            print((out or err).strip())
            return code

    _, head, _ = run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                     check=False)
    _, remote, _ = run(["git", "-C", root, "remote", "get-url", "origin"],
                       check=False)

    print(f"[✓] Готово — GitHub ({sanitize_url(remote.strip())}, "
          f"ветка {branch}) = {head.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

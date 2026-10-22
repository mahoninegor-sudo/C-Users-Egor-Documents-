import http.server
import os
import re
import socketserver
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
import html
import mimetypes
import json

ROOT = os.path.dirname(os.path.abspath(__file__))

COMMON_EXTS = [
    "txt", "md", "py", "js", "ts", "html", "css", "json", "xml",
    "csv", "log", "sh", "bat", "c", "cpp", "h", "java", "go", "rs",
    "yml", "yaml", "ini", "conf", "sql", "php", "rb",
]

MAX_COPY_BYTES = 20 * 1024 * 1024  # лимит на ответ /copy-content

# --- GitHub: заливка репозитория через push_git.py ---
PUSH_SCRIPT = os.path.join(ROOT, "push_git.py")
PUSH_TIMEOUT_SEC = 180


def _git(*args, cwd=None, timeout=15):
    """Запускает git и возвращает CompletedProcess (или None, если git нет)."""
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd or ROOT, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def repo_root(path=None):
    """Корень git-репозитория, в котором лежит path (по умолчанию ROOT)."""
    res = _git("-C", path or ROOT, "rev-parse", "--show-toplevel")
    if res is None or res.returncode != 0:
        return None
    out = res.stdout.strip()
    return os.path.normpath(out) if out else None


def repo_info():
    """Короткая сводка о репозитории для страницы."""
    root = repo_root()
    if not root:
        return None

    def val(*args):
        res = _git("-C", root, *args)
        return res.stdout.strip() if res and res.returncode == 0 else ""

    return {
        "root": root,
        "branch": val("rev-parse", "--abbrev-ref", "HEAD"),
        "remote": val("remote", "get-url", "origin"),
        "last": val("log", "-1", "--pretty=%h %s"),
    }


def hide_credentials(url):
    """Убирает логин/токен из https-URL для показа в браузере."""
    return re.sub(r"//[^/@]+@", "//", url or "")


def push_to_github():
    """
    Заливает на GitHub репозиторий, в котором лежит ROOT.

    Вызывает push_git.py: он коммитит актуальное состояние и делает push,
    поэтому на GitHub оказывается точная копия репозитория вместе со всеми
    папками (пустые папки сохраняются через .gitkeep).

    Возвращает (успех, лог).
    """
    script = PUSH_SCRIPT
    if not os.path.isfile(script):
        root = repo_root()
        if root and os.path.isfile(os.path.join(root, "push_git.py")):
            script = os.path.join(root, "push_git.py")
    if not os.path.isfile(script):
        return False, f"push_git.py не найден: {script}"

    try:
        res = subprocess.run(
            [sys.executable, script, ROOT],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=PUSH_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return False, f"push_git.py не ответил за {PUSH_TIMEOUT_SEC} с."
    except OSError as e:
        return False, f"Не удалось запустить push_git.py: {e}"

    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    log = out + ("\n\n" + err if err else "")
    return res.returncode == 0, log.strip()


class Handler(http.server.BaseHTTPRequestHandler):
    # ---------- маршрутизация ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        if path == "/copy-content":
            self.handle_copy_content(parsed.query)
        elif path.startswith("/view/"):
            self.show_file(path[len("/view/"):])
        elif path.startswith("/raw/"):
            self.send_raw(path[len("/raw/"):])
        else:
            self.show_dir(path.lstrip("/"))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        data = urllib.parse.parse_qs(body, keep_blank_values=True)

        if parsed.path == "/create-file":
            self.handle_create_file(data)
        elif parsed.path == "/create-folder":
            self.handle_create_folder(data)
        elif parsed.path == "/push-github":
            self.handle_push_github(data)
        else:
            self.send_error(404, "Не найдено")

    # ---------- утилиты ----------
    def safe_path(self, rel):
        full = os.path.abspath(os.path.join(ROOT, rel))
        if not full.startswith(ROOT + os.sep) and full != ROOT:
            return None
        return full

    def send_html(self, content, status=200):
        data = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, url):
        self.send_response(303)
        self.send_header("Location", url)
        self.end_headers()

    def sanitize_name(self, name):
        name = (name or "").strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            return None
        return name

    def dir_url(self, rel):
        return "/" + urllib.parse.quote(rel) if rel else "/"

    def error_page(self, message, rel_dir=""):
        back = self.dir_url(rel_dir)
        self.send_html(f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Ошибка</title>
<style>body{{font-family:system-ui,sans-serif;margin:2em;}}</style>
</head><body>
<h2>⚠ Ошибка</h2>
<p>{html.escape(message)}</p>
<p><a href="{back}">← Вернуться</a></p>
</body></html>""", status=400)

    # ---------- создание файла ----------
    def handle_create_file(self, data):
        rel_dir = data.get("dir", [""])[0]
        name = self.sanitize_name(data.get("name", [""])[0])
        ext = data.get("ext", [""])[0]
        push_requested = data.get("push", [""])[0] == "1"

        if ext == "__custom__":
            ext = data.get("ext_custom", [""])[0]
        ext = ext.strip().lstrip(".")

        dir_full = self.safe_path(rel_dir)
        if dir_full is None or not os.path.isdir(dir_full):
            self.error_page("Папка для создания не найдена", rel_dir)
            return
        if name is None:
            self.error_page("Недопустимое имя файла", rel_dir)
            return

        filename = f"{name}.{ext}" if ext else name
        target = os.path.join(dir_full, filename)

        try:
            with open(target, "x", encoding="utf-8"):
                pass
        except FileExistsError:
            self.error_page(f"Файл уже существует: {filename}", rel_dir)
            return
        except OSError as e:
            self.error_page(f"Ошибка создания: {e}", rel_dir)
            return

        if push_requested:
            ok, log = push_to_github()
            self.show_action_result(
                f"Файл создан: {filename}", rel_dir,
                push_ok=ok, push_log=log,
            )
            return

        self.redirect(self.dir_url(rel_dir))

    # ---------- создание папки ----------
    def handle_create_folder(self, data):
        rel_dir = data.get("dir", [""])[0]
        name = self.sanitize_name(data.get("name", [""])[0])
        push_requested = data.get("push", [""])[0] == "1"

        dir_full = self.safe_path(rel_dir)
        if dir_full is None or not os.path.isdir(dir_full):
            self.error_page("Папка для создания не найдена", rel_dir)
            return
        if name is None:
            self.error_page("Недопустимое имя папки", rel_dir)
            return

        target = os.path.join(dir_full, name)
        try:
            os.mkdir(target)
        except FileExistsError:
            self.error_page(f"Папка уже существует: {name}", rel_dir)
            return
        except OSError as e:
            self.error_page(f"Ошибка создания: {e}", rel_dir)
            return

        if push_requested:
            ok, log = push_to_github()
            self.show_action_result(
                f"Папка создана: {name}", rel_dir,
                push_ok=ok, push_log=log,
            )
            return

        self.redirect(self.dir_url(rel_dir))

    # ---------- отправка репозитория на GitHub ----------
    def handle_push_github(self, data):
        rel_dir = data.get("dir", [""])[0]
        if self.safe_path(rel_dir) is None:
            rel_dir = ""
        ok, log = push_to_github()
        self.show_action_result(
            "Отправка на GitHub", rel_dir,
            push_ok=ok, push_log=log,
        )

    def show_action_result(self, title, rel_dir="", *, push_ok=None,
                           push_log=""):
        """Страница «что сделали» с результатом push_git.py."""
        back = self.dir_url(rel_dir)

        banner = ""
        if push_ok is True:
            banner = '<p class="ok">✅ Репозиторий загружен на GitHub</p>'
        elif push_ok is False:
            banner = ('<p class="err">❌ Загрузить на GitHub не удалось</p>'
                      '<p class="hint">Файлы на диске сохранены. '
                      'Исправьте причину и нажмите «Загрузить в GitHub» '
                      'ещё раз.</p>')

        log_block = ""
        if push_log:
            log_block = (
                '<details open><summary>Лог push_git.py</summary>'
                f'<pre>{html.escape(push_log)}</pre></details>'
            )

        self.send_html(f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2em; max-width: 900px; }}
  .ok {{ color: #1a7f37; font-weight: 600; }}
  .err {{ color: #cf222e; font-weight: 600; }}
  .hint {{ color: #57606a; font-size: 14px; }}
  pre {{ background: #f6f8fa; padding: 1em; border-radius: 6px;
         overflow: auto; font-size: 12.5px; line-height: 1.4;
         white-space: pre-wrap; word-break: break-word; }}
  summary {{ cursor: pointer; color: #57606a; }}
</style></head>
<body>
<p><a href="{back}">← К папке</a></p>
<h2>{html.escape(title)}</h2>
{banner}
{log_block}
</body></html>""")

    # ---------- копирование содержимого директории ----------
    def handle_copy_content(self, query):
        params = urllib.parse.parse_qs(query)
        rel_dir = params.get("dir", [""])[0]

        full = self.safe_path(rel_dir)
        if full is None or not os.path.isdir(full):
            self.send_error(404, "Папка не найдена")
            return

        parts = []
        total = 0
        truncated = False

        for root, dirs, files in os.walk(full):
            dirs.sort()
            for fname in sorted(files):
                fpath = os.path.join(root, fname)
                relpath = os.path.relpath(fpath, ROOT).replace(os.sep, "/")

                try:
                    fsize = os.path.getsize(fpath)
                except OSError:
                    fsize = 0

                if total + fsize > MAX_COPY_BYTES:
                    truncated = True
                    parts.append(
                        f"\n=== {relpath} (пропущен: превышен лимит "
                        f"{MAX_COPY_BYTES} байт) ===\n"
                    )
                    continue

                try:
                    with open(fpath, "r", encoding="utf-8",
                              errors="replace") as f:
                        content = f.read()
                    if "\x00" in content[:4096]:
                        parts.append(
                            f"\n=== {relpath} (бинарный файл, пропущен) ===\n"
                        )
                        continue
                    block = f"\n=== {relpath} ===\n{content}\n"
                    parts.append(block)
                    total += len(block.encode("utf-8"))
                except Exception as e:
                    parts.append(
                        f"\n=== {relpath} (ошибка чтения: {e}) ===\n"
                    )

        if truncated:
            parts.append(
                f"\n=== ВНИМАНИЕ: часть файлов пропущена из-за лимита "
                f"{MAX_COPY_BYTES} байт ===\n"
            )

        text = "".join(parts)
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------- карточка GitHub ----------
    def github_card(self, info, rel_esc):
        """Карточка в тулбаре: заливка всего репозитория на GitHub."""
        if not info:
            return """
  <div class="card">
    <h3>☁ GitHub</h3>
    <p class="note warn">Папка не в git-репозитории — заливка недоступна.</p>
  </div>"""

        remote = hide_credentials(info.get("remote", "")) or "(origin не задан)"
        branch = info.get("branch") or "?"
        last = info.get("last") or ""
        last_html = (f'<div class="repo dim">{html.escape(last)}</div>'
                     if last else "")
        return f"""
  <div class="card">
    <h3>☁ GitHub</h3>
    <p class="note">Весь репозиторий — через push_git.py</p>
    <div class="repo">🌿 {html.escape(branch)} · {html.escape(remote)}</div>
    {last_html}
    <form method="post" action="/push-github" onsubmit="pushSubmit(this)">
      <input type="hidden" name="dir" value="{rel_esc}">
      <button type="submit">☁ Загрузить в GitHub</button>
    </form>
  </div>"""

    # ---------- листинг папки ----------
    def show_dir(self, rel):
        full = self.safe_path(rel)
        if full is None or not os.path.isdir(full):
            self.send_error(404, "Папка не найдена")
            return

        rel_esc = html.escape(rel)
        rel_json = json.dumps(rel)  # для JS, с кавычками
        dirs, files = [], []
        if rel:
            parent = os.path.dirname(rel.rstrip("/"))
            dirs.append(f'<li>📁 <a href="{self.dir_url(parent)}">..</a></li>')

        for name in sorted(os.listdir(full)):
            item_full = os.path.join(full, name)
            item_rel = os.path.join(rel, name) if rel else name
            q = urllib.parse.quote(item_rel)
            if os.path.isdir(item_full):
                dirs.append(f'<li>📁 <a href="/{q}">{html.escape(name)}/</a></li>')
            else:
                size = os.path.getsize(item_full)
                files.append(
                    f'<li>📄 <a href="/view/{q}" target="_blank">'
                    f'{html.escape(name)}</a> '
                    f'<span style="color:#888">({size} байт)</span></li>'
                )

        rows = "\n".join(dirs + files)
        ext_options = "\n".join(
            f'<option value="{e}">{e}</option>' for e in COMMON_EXTS
        )
        push_card = self.github_card(repo_info(), rel_esc)

        self.send_html(f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>/{rel_esc}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2em; max-width: 1100px; }}
  li {{ margin: .3em 0; }}

  .toolbar {{
      display: flex; gap: 1.5em; margin-bottom: 1.5em;
      flex-wrap: wrap; align-items: flex-start;
  }}
  .card {{
      border: 1px solid #d0d7de; border-radius: 8px;
      padding: .8em 1em; background: #fafbfc; min-width: 280px;
  }}
  .card h3 {{ margin: 0 0 .6em; font-size: 15px; }}
  .card form {{ display: flex; gap: .5em; flex-wrap: wrap; align-items: center; }}
  .card input, .card select {{
      padding: .45em .6em; font-size: 14px;
      border: 1px solid #ccc; border-radius: 6px;
  }}
  .card input[name="name"] {{ flex: 1 1 120px; }}
  .card button {{
      padding: .5em 1.1em; font-size: 14px; font-weight: 600;
      background: #2da44e; color: white; border: none;
      border-radius: 6px; cursor: pointer;
  }}
  .card button:hover {{ background: #2c974b; }}
  .card button:disabled {{ background: #94d3a2; cursor: wait; }}
  .status {{ font-size: 13px; color: #555; margin-left: .6em; }}
  .note {{ font-size: 13px; color: #666; margin: .2em 0 .6em; }}
  .note.warn {{ color: #a15c00; }}
  .repo {{ font-size: 12.5px; color: #444; margin: .2em 0 .5em;
          word-break: break-all; max-width: 320px; }}
  .repo.dim {{ color: #888; }}
  .push-chk {{ display: inline-flex; align-items: center; gap: .35em;
              font-size: 13px; color: #444; white-space: nowrap; }}
  .push-chk input {{ margin: 0; }}
</style></head>
<body>
<h2>Содержимое: /{rel_esc}</h2>

<div class="toolbar">

  <div class="card">
    <h3>➕ Создать файл</h3>
    <form method="post" action="/create-file">
      <input type="hidden" name="dir" value="{rel_esc}">
      <input name="name" placeholder="имя файла" required autocomplete="off">
      <select name="ext" id="ext-select" onchange="onExtChange()">
        <option value="">(без расширения)</option>
        {ext_options}
        <option value="__custom__">другое…</option>
      </select>
      <input name="ext_custom" id="ext-custom" placeholder="своё расширение"
             style="display:none" autocomplete="off">
      <button type="submit">Создать</button>
      <label class="push-chk"
             title="После создания залить весь репозиторий на GitHub">
        <input type="checkbox" name="push" value="1" checked> ⬆ в GitHub
      </label>
    </form>
  </div>

  <div class="card">
    <h3>📁 Создать папку</h3>
    <form method="post" action="/create-folder">
      <input type="hidden" name="dir" value="{rel_esc}">
      <input name="name" placeholder="имя папки" required autocomplete="off">
      <button type="submit">Создать</button>
      <label class="push-chk"
             title="После создания залить весь репозиторий на GitHub">
        <input type="checkbox" name="push" value="1" checked> ⬆ в GitHub
      </label>
    </form>
  </div>

  <div class="card">
    <h3>📋 Скопировать содержимое</h3>
    <p style="font-size:13px;color:#666;margin:.2em 0 .6em;">
      Все текстовые файлы рекурсивно (с путями-заголовками)
    </p>
    <button type="button" id="copy-btn" onclick="copyAll()">📋 Скопировать</button>
    <span class="status" id="copy-status"></span>
  </div>

  {push_card}

</div>

<ul>
{rows}
</ul>

<script>
const CURRENT_DIR = {rel_json};

function onExtChange() {{
  var sel = document.getElementById('ext-select');
  var inp = document.getElementById('ext-custom');
  if (sel.value === '__custom__') {{
    inp.style.display = 'inline-block';
    inp.focus();
  }} else {{
    inp.style.display = 'none';
  }}
}}

function pushSubmit(form) {{
  // push_git.py может работать несколько секунд — блокируем кнопку
  var btn = form.querySelector('button');
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = 'Загружаю…';
}}

async function copyTextToClipboard(text) {{
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    await navigator.clipboard.writeText(text);
    return;
  }}
  // fallback для старых браузеров
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta);
}}

async function copyAll() {{
  const btn = document.getElementById('copy-btn');
  const status = document.getElementById('copy-status');
  btn.disabled = true;
  status.textContent = 'Собираю…';
  try {{
    const url = '/copy-content?dir=' + encodeURIComponent(CURRENT_DIR);
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const text = await resp.text();
    if (!text.trim()) {{
      status.textContent = '⚠ В папке нет текстовых файлов';
      return;
    }}
    await copyTextToClipboard(text);
    status.textContent = '✅ Скопировано (' + text.length +
                         ' символов, ' + Math.round(text.length / 1024) + ' КБ)';
  }} catch (e) {{
    status.textContent = '❌ Ошибка: ' + e.message;
  }} finally {{
    btn.disabled = false;
  }}
}}
</script>
</body></html>""")

    # ---------- страница с содержимым файла ----------
    def show_file(self, rel):
        full = self.safe_path(rel)
        if full is None or not os.path.isfile(full):
            self.send_error(404, "Файл не найден")
            return

        size = os.path.getsize(full)
        name = os.path.basename(full)
        raw_url = "/raw/" + urllib.parse.quote(rel)
        parent = os.path.dirname(rel)
        back_url = self.dir_url(parent)

        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            if "\x00" in text[:4096]:
                raise ValueError("похоже на бинарный файл")
            body = f"<pre>{html.escape(text)}</pre>"
        except Exception as e:
            body = (
                f"<p><i>Не удалось показать как текст ({html.escape(str(e))}).</i></p>"
                f'<p><a href="{raw_url}">Открыть / скачать файл</a></p>'
            )

        self.send_html(f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>{html.escape(name)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2em; }}
  pre {{ background: #f4f4f4; padding: 1em; overflow: auto;
         white-space: pre-wrap; word-wrap: break-word;
         border-radius: 6px; font-size: 13px; line-height: 1.4; }}
  .meta {{ color: #666; font-size: 90%; }}
</style></head>
<body>
<p><a href="{back_url}">← К папке</a> &nbsp;|&nbsp;
   <a href="{raw_url}" target="_blank">Открыть как есть</a></p>
<h2>{html.escape(name)}</h2>
<p class="meta">Размер: {size} байт</p>
{body}
</body></html>""")

    # ---------- «сырой» файл ----------
    def send_raw(self, rel):
        full = self.safe_path(rel)
        if full is None or not os.path.isfile(full):
            self.send_error(404, "Файл не найден")
            return
        ctype, _ = mimetypes.guess_type(full)
        ctype = ctype or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print("[server]", fmt % args)


# ---------- запуск ----------
PORT = 8001


def open_browser():
    webbrowser.open_new_tab(f"http://localhost:{PORT}/")


threading.Timer(0.5, open_browser).start()

with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler) as httpd:
    print(f"Открой http://localhost:{PORT}/")
    print(f"Папка: {ROOT}")
    print("Ctrl+C — остановить")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")

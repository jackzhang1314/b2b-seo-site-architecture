"""Open a project's editable architecture workbench on a persistent loopback URL."""
from __future__ import annotations

import argparse
import errno
import json
import mimetypes
import os
import re
import shutil
import sys
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from urllib.request import urlopen

from page_confirmation import FILENAME, DecisionConflict, change_page, encode, read_handoff
from plan_records import Row, obj, records, text
from register_project import atomic_write, project_lock
from revisions import read_revisions
from seo_project import local_file, local_path, project_for

EMBED = b'<script type="application/json" id="saved-page-context"></script>'
ALTERNATE = {"index.html", "design.html"}


def page_list(context: Row) -> bytes:
    labels = {"build": "要建", "skip": "不建", "pending": "待定"}
    rows = []
    for page in records(context, "pages"):
        name = str(page.get("name_zh") or page["name"])
        label = labels[text(page, "decision")]
        rows.append(f"<tr><td>{escape(name)}</td><td>{escape(text(page, 'url'))}</td><td>{label}</td></tr>")
    return ("<table><caption>用户已保存的逐页选择</caption><thead><tr><th>页面</th><th>规划网址</th>"
            "<th>本次选择</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>").encode()


class ReviewServer(ThreadingHTTPServer):
    def __init__(self, root: Path, folder: Path, port: int) -> None:
        self.root = root.resolve()
        self.folder = folder.resolve()
        if not self.folder.is_relative_to(self.root):
            raise ValueError("Select a workbench within its website project")
        read_handoff(self.folder)
        super().__init__(("127.0.0.1", port), ReviewRequest)

    def catalogue(self) -> dict[Path, str]:
        files = {self.folder: "standalone"}
        agents = self.root / "AGENTS.md"
        if agents.is_file():
            entries = read_revisions(self.root, local_file(self.root, "AGENTS.md").read_text(encoding="utf-8-sig"))
            files.update({p.parent: role for p, (role, _) in entries.items()})
        return files

    def url(self, folder: Path) -> str:
        route = (folder.resolve() / "index.html").relative_to(self.root).as_posix()
        return f"http://127.0.0.1:{self.server_port}/" + quote(route, safe="/")

    def locate(self, target: str) -> tuple[Path, Path | None, str]:
        name = unquote(urlsplit(target).path, errors="strict").lstrip("/")
        if not name:
            name = (self.folder / "index.html").relative_to(self.root).as_posix()
        elif name.endswith("/"):
            name += "index.html"
        path = local_path(self.root, name)
        if re.fullmatch(r"\.seo-architecture/[a-f0-9]{24}\.js", name):
            return local_file(self.root, name), None, ""
        for folder, role in sorted(self.catalogue().items(), key=lambda pair: len(pair[0].parts), reverse=True):
            if not path.is_relative_to(folder):
                continue
            relative = path.relative_to(folder)
            if any(part.startswith(".") for part in relative.parts):
                break
            return local_file(folder, relative.as_posix()), folder, role
        raise FileNotFoundError("Unknown workbench resource")


class ReviewRequest(BaseHTTPRequestHandler):
    server: ReviewServer

    def allowed_host(self) -> bool:
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def send_data(self, data: bytes, kind: str, status: int = 200, *, editable: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Accept")
        self.send_header("X-Content-Type-Options", "nosniff")
        if editable:
            self.send_header("X-Workbench-Editor", "1")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def fail(self, status: int, message: str) -> None:
        self.send_data(encode({"error": message}), "application/json; charset=utf-8", status)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self.allowed_host():
            self.fail(403, "Invalid local host")
            return
        if urlsplit(self.path).path == "/":
            self.send_response(302)
            self.send_header("Location", self.server.url(self.server.folder))
            self.end_headers()
            return
        try:
            path, folder, role = self.server.locate(self.path)
            if folder and (folder / FILENAME).is_file() and path.parent == folder and path.name in {*ALTERNATE, FILENAME}:
                context = {**read_handoff(folder), "registration": role, "workbench_url": self.server.url(folder)}
                if path.name == FILENAME or self.headers.get("Accept", "").split(",")[0].strip() == "application/json":
                    self.send_data(encode(context), "application/json; charset=utf-8", editable=True)
                else:
                    # Plain HTML readers also receive the saved state; script delimiters in customer text stay inert.
                    payload = json.dumps(context, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c").encode()
                    body = path.read_bytes().replace(EMBED, EMBED.replace(b"</script>", payload + b"</script>"), 1)
                    body = body.replace(b"<!-- saved-page-list -->", page_list(context), 1)
                    self.send_data(body, "text/html; charset=utf-8", editable=True)
                return
            with path.open("rb") as stream:
                kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Length", str(os.fstat(stream.fileno()).st_size))
                self.send_header("Content-Type", kind)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                if folder and path.relative_to(folder).parts[0] in {"supplied", "analysis"}:
                    self.send_header("Content-Disposition", "attachment")
                    self.send_header("Content-Security-Policy", "sandbox")
                self.end_headers()
                if self.command != "HEAD":
                    shutil.copyfileobj(stream, self.wfile)
        except (OSError, ValueError, KeyError):
            self.fail(404, "Workspace file unavailable")

    def do_POST(self) -> None:
        origin = self.headers.get("Origin")
        if not self.allowed_host() or origin != "http://" + str(self.headers.get("Host")):
            self.fail(403, "Use the workspace editor to save decisions")
            return
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            self.fail(415, "Expected a JSON page decision")
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 1 or length > 8192:
                self.fail(413, "Invalid decision size")
                return
            path, folder, role = self.server.locate(self.path)
            if not folder or path != folder / FILENAME:
                self.fail(404, "Unknown confirmation endpoint")
                return
            body = obj(json.loads(self.rfile.read(length)))
            result = change_page(folder, body)
            self.send_data(encode({**result, "registration": role, "workbench_url": self.server.url(folder)}),
                           "application/json; charset=utf-8", editable=True)
        except DecisionConflict as error:
            self.fail(409, str(error))
        except (ValueError, KeyError):
            self.fail(400, "页面或确认信息无效，请刷新工作台核对。")
        except OSError:
            self.fail(503, "暂时无法保存到项目，请重试。")


def open_server(folder: Path, port: int | None, launch: bool) -> None:
    folder = folder.resolve()
    handoff = read_handoff(folder)
    root = project_for(folder) or folder
    with project_lock(root):
        directory = local_path(root, ".seo-architecture")
        if (root / ".seo-architecture").is_symlink():
            raise ValueError("Invalid local editor settings directory")
        directory.mkdir(exist_ok=True)
        settings = directory / "local-editor.json"
        previous = settings.read_bytes() if settings.is_file() and not settings.is_symlink() else None
        wanted = port if port is not None else int(json.loads(previous)["port"]) if previous else 0
        if not 0 <= wanted <= 65535:
            raise ValueError("Choose a local port between 0 and 65535")
        relative = quote((folder / "index.html").relative_to(root).as_posix(), safe="/")
        try:
            server = ReviewServer(root, folder, wanted)
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            url = f"http://127.0.0.1:{wanted}/{relative}"
            probe = url.rsplit("/", 1)[0] + "/" + FILENAME
            try:
                with urlopen(probe, timeout=2) as response:
                    same = (response.headers.get("X-Workbench-Editor") == "1" and
                            json.load(response).get("revision_id") == handoff["revision_id"])
            except (OSError, ValueError, AttributeError):
                same = False
            if not same:
                raise ValueError(f"Port {wanted} is in use. To choose a new address, use --port 0.") from error
            entry = folder / "workbench-open.js"
            if entry.is_file():
                atomic_write(entry, ("window.workbenchEditorUrl = " + json.dumps(url) + ";\n").encode(),
                             entry.read_bytes())
            print(url, flush=True)
            if launch:
                webbrowser.open(url)
            return
        try:
            atomic_write(settings, encode({"port": server.server_port}), previous)
            url = server.url(folder)
            for revision in server.catalogue():
                entry = revision / "workbench-open.js"
                if entry.is_file():
                    payload = ("window.workbenchEditorUrl = " + json.dumps(server.url(revision)) + ";\n").encode()
                    atomic_write(entry, payload, entry.read_bytes())
        except BaseException:
            server.server_close()
            raise
    with server:
        print(url, flush=True)
        if launch:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbench", type=Path, required=True, help="Directory containing the generated index.html")
    parser.add_argument("--port", type=int, help="Use a specific port; default reuses this project's saved address")
    parser.add_argument("--open", action="store_true", help="Open the editable workbench in the default browser")
    args = parser.parse_args()
    try:
        open_server(args.workbench, args.port, args.open)
    except (ValueError, KeyError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

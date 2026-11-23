"""HTML 템플릿 → PDF (헤드리스 Chrome)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from .models import Status, Value

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
]

CHECK = Markup('<span class="chk">«확인 필요»</span>')


def fv(v: Value | None, unit: str = "") -> Markup:
    """값 표시 규칙 (doc/02 §4)."""
    if v is None:
        return Markup("–")
    if v.status == Status.NEEDS_CHECK or (v.status == Status.CONFLICT and not v.show_amount):
        return CHECK
    if v.amount is None:
        if v.text in (None, "공란"):
            return Markup('<span class="blank">–</span>')
        body = escape(v.text)
    elif v.key.endswith("d.debt") and v.amount is not None:
        body = f"{v.amount:+,.1f}%p"
    elif v.key.endswith(".eok"):
        body = f"{v.amount:,.2f}"
    elif isinstance(v.amount, float):
        body = f"{v.amount:,.1f}%"
    else:
        body = f"{v.amount:,}{unit}"
    mark = ""
    if v.status == Status.ASSUMED:
        mark = '<sup class="as">A</sup>'
    elif v.status == Status.CONFLICT:
        mark = '<sup class="cf">※</sup>'
    return Markup(f"{body}{mark}")


def cite(v: Value | None) -> str:
    if v is None or not v.evidence:
        return v.formula if v is not None and v.formula else ""
    return "; ".join(e.short() for e in v.evidence)


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).exists() or shutil.which(c):
            return c
    raise SystemExit("PDF 변환에 필요한 Chrome/Chromium을 찾지 못했습니다.")


def render(ctx: dict, out_dir: str | Path, basename: str, template: str = "report.html.j2") -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html", "j2"]))
    env.filters["fv"] = fv
    env.filters["cite"] = cite
    env.filters["won"] = lambda n: "–" if n is None else f"{n:,}"
    html = env.get_template(template).render(**ctx, Status=Status, CHECK=CHECK)
    html_path = out_dir / f"{basename}.html"
    pdf_path = out_dir / f"{basename}.pdf"
    html_path.write_text(html, encoding="utf-8")
    subprocess.run([find_chrome(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    "--allow-file-access-from-files", f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                   check=True, capture_output=True, timeout=120)
    return html_path, pdf_path

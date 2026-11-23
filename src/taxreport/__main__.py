"""사용법:
    python -m taxreport --prior tax/샘플컴퍼니_2024_세무조정계산서.pdf \
                        --current tax/샘플컴퍼니_2025_세무조정계산서.pdf --out out
"""
import argparse
import csv
import json
import logging
from pathlib import Path

from .build import build
from .charts import build_charts
from .fee import build_fee
from .render import render


def save_warning_crops(ctx, out: Path, prior: str, current: str):
    """경고 위치를 PDF에서 잘라 저장 — 'PDF에 실제로 보이는 내용'인지 대조용."""
    import pdfplumber
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    d = out / "경고_캡처"
    d.mkdir(exist_ok=True)
    files = {Path(prior).name: prior, Path(current).name: current}
    opened = {}
    for w in ctx["warnings"]:
        if not w.get("bbox") or w["file"] not in files or not isinstance(w["page"], int):
            continue
        pdf = opened.setdefault(w["file"], pdfplumber.open(files[w["file"]]))
        p = pdf.pages[w["page"] - 1]
        x0, x1, t, b = w["bbox"]
        box = (max(0, x0 - 230), max(0, t - 14), min(p.width, x1 + 40), min(p.height, b + 14))
        try:
            p.crop(box).to_image(resolution=130).save(d / f"{w['id']}.png")
            w["crop"] = f"경고_캡처/{w['id']}.png"
        except Exception:  # noqa: BLE001 — 캡처 실패는 보고서에 영향 없음
            pass
    for pdf in opened.values():
        pdf.close()


def main():
    ap = argparse.ArgumentParser(description="전기·당기 세무조정계산서 PDF → 법인세 분석 보고서 PDF")
    ap.add_argument("--prior", required=True, help="전기 세무조정계산서 PDF")
    ap.add_argument("--current", required=True, help="당기 세무조정계산서 PDF")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parents[2] / "config"))
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    ctx = build(a.prior, a.current, a.config)
    # 실행마다 새 폴더에 저장 — 이전 결과를 덮어쓰지 않음
    from datetime import datetime
    co = ctx["C"]["basic"]["name"].text.replace("(주)", "").replace("주식회사", "")
    out = Path(a.out) / f"{co}_{ctx['cy']}_{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=False)
    ctx["charts"] = build_charts(ctx)
    name = ctx["C"]["basic"]["name"].text.replace("(주)", "").replace("주식회사", "")
    base = f"{name}_{ctx['cy']}_법인세분석보고서"
    html, pdf = render(ctx, out, base)
    ctx["fee"] = build_fee(ctx, a.config)
    _, fee_pdf = render(ctx, out, f"{name}_{ctx['cy']}_조정료청구서", "fee.html.j2")

    save_warning_crops(ctx, out, a.prior, a.current)
    cols = ["id", "kind", "year", "file", "form", "page", "item", "shown", "judge", "action", "note", "q", "crop"]
    with open(out / f"{base}_경고목록.csv", "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.DictWriter(fp, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for w in ctx["warnings"]:
            wr.writerow(w)
    ledger = [{"section": s, "key": v.key, "label": v.label, "amount": v.amount, "text": v.text, "status": v.status.value,
               "flag": v.flag, "evidence": [e.__dict__ for e in v.evidence], "formula": v.formula,
               "questions": v.questions, "note": v.note} for s, v in ctx["ledger"]]
    (out / f"{base}_근거대장.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")

    f = ctx["fee"]
    print(f"PDF: {pdf}\n조정료청구서: {fee_pdf} (청구 금액 {f['total'].status.value})")
    print(f"근거 대장 {len(ledger)}건 · 경고 {len(ctx['warnings'])}건 · 대사 {len(ctx['findings'])}건")
    for w in ctx["warnings"]:
        print(f"  {w['id']} [{w['kind']}] {w['year']} {w['form']} p{w['page']} · {w['item']} · 표시값 {w['shown']} · {w['note'][:70]}")


if __name__ == "__main__":
    main()

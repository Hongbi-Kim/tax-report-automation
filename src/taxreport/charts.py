"""요구사항 2번 그래프. 확정되지 않은 값은 그리지 않고 그래프 아래에 '확인 필요'로 밝힌다."""
from __future__ import annotations

import base64
import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from .build import known, num  # noqa: E402

for f in (Path.home() / "Library/Fonts/Pretendard-Regular.otf", Path("/System/Library/Fonts/AppleSDGothicNeo.ttc")):
    if f.exists():
        font_manager.fontManager.addfont(str(f))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(f)).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

C_PRIOR, C_CUR = "#9aa5b1", "#1e3a5f"


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=170, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color("#d1d5db")
    ax.spines["bottom"].set_color("#9ca3af")
    ax.tick_params(colors="#374151", labelsize=8)
    ax.yaxis.grid(True, color="#eef0f3", linewidth=0.8)
    ax.set_axisbelow(True)


def grouped(title, labels, prior, cur, py, cy, unit="억원", scale=1e8):
    missing = [f"{lab}({yr})" for lab, p, c in zip(labels, prior, cur) for yr, v in ((py, p), (cy, c)) if v is None]
    fig, ax = plt.subplots(figsize=(6.2, 2.6))
    x = range(len(labels))
    w = 0.36
    pv = [(v / scale) if v is not None else 0 for v in prior]
    cv = [(v / scale) if v is not None else 0 for v in cur]
    b1 = ax.bar([i - w / 2 for i in x], pv, w, color=C_PRIOR, label=f"{py}")
    b2 = ax.bar([i + w / 2 for i in x], cv, w, color=C_CUR, label=f"{cy}")
    for bars, vals in ((b1, prior), (b2, cur)):
        for bar, v in zip(bars, vals):
            txt = "확인 필요" if v is None else f"{v / scale:,.2f}"
            ax.annotate(txt, (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 2 if bar.get_height() >= 0 else -9), textcoords="offset points",
                        ha="center", fontsize=7, color="#b42318" if v is None else "#374151")
    ax.axhline(0, color="#9ca3af", linewidth=0.8)
    ax.set_xticks(list(x), labels)
    ax.set_ylabel(unit, fontsize=8, color="#6b7280")
    ax.legend(frameon=False, fontsize=8, loc="lower right", bbox_to_anchor=(1.0, 1.02), ncols=2)
    ax.margins(y=0.15)
    _style(ax)
    return {"title": title, "img": _png(fig), "missing": missing}


def build_charts(ctx: dict) -> list[dict]:
    py, cy = ctx["py"], ctx["cy"]
    P, C = ctx["P"], ctx["C"]
    charts = []
    # 1. 주요 손익 비교
    labs, pv, cv = [], [], []
    for lab, code in (("매출액", 1), ("매출총이익", 66), ("영업이익", 129), ("당기순이익", 219)):
        labs.append(lab)
        pv.append(num(P["is"].get(code)) if known(P["is"].get(code)) else None)
        cv.append(num(C["is"].get(code)) if known(C["is"].get(code)) else None)
    charts.append(grouped("① 전기 대비 주요 손익 (단위: 억원)", labs, pv, cv, py, cy))

    # 2. 판관비 구성 비율 (당기)
    sga_tot = ctx["sga_tot"][1]
    items = {c: v for c, v in C["is"].items() if 67 < c < 129 and v.label[:1].isdigit() and not v.blank}
    if known(sga_tot) and num(sga_tot) and all(known(v) for v in items.values()):
        ranked = sorted(items.values(), key=lambda v: -num(v))
        top, rest = ranked[:5], ranked[5:]
        vals = [num(v) for v in top] + ([sum(num(v) for v in rest)] if rest else [])
        labels = [v.label.split(".", 1)[-1] for v in top] + (["기타 항목 합계"] if rest else [])
        fig, ax = plt.subplots(figsize=(6.2, 2.7))
        colors = ["#1e3a5f", "#2f5d8a", "#4f7cac", "#7fa3c8", "#aec6df", "#d6e2ee"]
        wedges, _t, autot = ax.pie(vals, labels=None, autopct=lambda p: f"{p:.1f}%", startangle=90, counterclock=False,
                                   colors=colors[:len(vals)], pctdistance=0.78, wedgeprops={"width": 0.42, "edgecolor": "white"},
                                   textprops={"fontsize": 7, "color": "white"})
        ax.legend(wedges, [f"{lab} {v:,.0f}원" for lab, v in zip(labels, vals)], loc="center left",
                  bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=7.5)
        ax.set_aspect("equal")
        charts.append({"title": f"② {cy} 판매비와관리비 구성 비율 (단위: %)", "img": _png(fig), "missing": []})
    else:
        unk = [v.label.split(".", 1)[-1] for v in items.values() if not known(v)]
        why = []
        if not known(sga_tot):
            why.append(f"판매비와관리비 합계({sga_tot.flag or '확인 필요'}{': ' + sga_tot.text if sga_tot.text and sga_tot.flag == '글자표기' else ''})")
        if unk:
            why.append("금액 미확인 항목(" + ", ".join(unk) + ")")
        charts.append({"title": f"② {cy} 판매비와관리비 구성 비율", "img": None,
                       "missing": ["구성 비율 보류 — " + " · ".join(why) + " 확인 필요. 비율의 분모가 정해지지 않아 그리지 않았습니다."]})

    # 3. 판관비 주요 5개 항목 전기·당기
    rows = ctx["sga_rows"][:5]
    if rows:
        charts.append(grouped("③ 판매비와관리비 주요 5개 항목 (단위: 백만원)", [r["label"] for r in rows],
                              [num(r["prior"]) if known(r["prior"]) else None for r in rows],
                              [num(r["cur"]) if known(r["cur"]) else None for r in rows], py, cy, "백만원", 1e6))
    # 4. 재무상태 비교
    labs, pv, cv = [], [], []
    for lab, code in (("자산총계", 228), ("부채총계", 333), ("자본총계", 382)):
        labs.append(lab)
        pv.append(num(P["bs"].get(code)) if known(P["bs"].get(code)) else None)
        cv.append(num(C["bs"].get(code)) if known(C["bs"].get(code)) else None)
    charts.append(grouped("④ 재무상태 비교 (단위: 억원)", labs, pv, cv, py, cy))
    return charts

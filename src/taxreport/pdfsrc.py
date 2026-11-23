"""PDF 읽기·페이지 식별 (doc/03 §1, §2.1)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

NUM_RE = re.compile(r"^-?\(?[\d,]*\d\)?$")


def parse_num(tok: str) -> int | None:
    t = tok.replace(",", "").replace(":", "").strip()
    neg = t.startswith("-") or (t.startswith("(") and t.endswith(")")) or t.startswith("△")
    t = t.strip("-()△")
    if not t or not t.isdigit():
        return None
    return -int(t) if neg else int(t)


def is_num(tok: str) -> bool:
    return bool(NUM_RE.match(tok)) and any(c.isdigit() for c in tok)


def nospace(s: str) -> str:
    return re.sub(r"\s+", "", s)


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float


@dataclass
class Line:
    top: float
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


GLUED_RE = re.compile(r"^(-?\d{1,3}(?:,\d{3})+)([^\d,].*)$")


def split_glued(words: list[Word]) -> list[Word]:
    """'163,718,790II.비유동자산'처럼 금액과 다음 칸 글자가 붙어 추출된 토큰을 나눈다(3차 포맷)."""
    out = []
    for w in words:
        m = GLUED_RE.match(w.text)
        if m:
            k = len(m.group(1)) / len(w.text)
            cut = w.x0 + (w.x1 - w.x0) * k
            out += [Word(m.group(1), w.x0, cut, w.top), Word(m.group(2), cut + 0.5, w.x1, w.top)]
        else:
            out.append(w)
    return out


def cluster_lines(words: list[Word], tol: float = 3.5) -> list[Line]:
    lines: list[Line] = []
    for w in sorted(words, key=lambda w: (w.top, w.x0)):
        if lines and abs(w.top - lines[-1].top) <= tol:
            lines[-1].words.append(w)
        else:
            lines.append(Line(w.top, [w]))
    for ln in lines:
        ln.words.sort(key=lambda w: w.x0)
    return lines


# 우선순위 순. (식별명, 첫 줄들에서 찾을 문자열(공백 제거), 제외 문자열)
FORM_RULES = [
    ("목차", "목차", None),
    ("합계표준재무상태표", "합계표준재무상태표", None),
    ("표준재무상태표", "표준재무상태표", None),
    ("표준손익계산서", "표준손익계산서", None),
    ("법인지방소득세신고서", "법인지방소득세과세표준및세액신고서", None),
    ("법인지방소득세조정계산서", "법인지방소득세과세표준및세액조정계산서", None),
    ("법인세과세표준및세액신고서", "법인세과세표준및세액신고서", None),
    ("법인세과세표준및세액조정계산서", "법인세과세표준및세액조정계산서", None),
    ("법인세과세표준및세액조정계산서", "re:법인세과세표준및.{0,60}세액조정계산서", "지방"),
    ("최저한세조정계산서", "최저한세조정계산서", None),
    ("공제감면세액합계표(갑)", "추가납부세액합계표(갑)", None),
    ("공제감면세액합계표(을)", "추가납부세액합계표(을)", None),
    ("공제감면세액계산서(2)", "공제감면세액계산서(2)", None),
    ("공제감면세액계산서(3)", "제8호서식부표3", None),
    ("가지급금등의인정이자조정명세서(갑)", "가지급금등의인정이자조정명세서(갑)", None),
    ("가지급금등의인정이자조정명세서(을)", "가지급금등의인정이자조정명세서(을)", None),
    ("업무무관부동산등관련차입금이자조정명세서", "업무무관부동산등에관련한차입금이자조정명세서", None),
    ("원천납부세액명세서", "원천납부세액명세서", None),
    ("공제감면세액합계표(을)", "공제감면세액및", "(갑)"),
    ("수입금액조정명세서", "수입금액조정명세서", "조정후수입금액명세서"),
    ("조정후수입금액명세서", "조정후수입금액명세서", None),
    ("세금공과금명세서", "세금공과금명세서", None),
    ("재고자산평가조정명세서", "평가조정명세서", None),
    ("소득구분계산서", "소득구분계산서", None),
    ("전산조직운용명세서", "전산조직운용명세서", None),
    ("조정반신청서", "조정반신청서", None),
    ("성실신고확인자선임신고서", "성실신고확인자", None),
    ("주식등변동상황명세서", "주식등변동상황명세서", None),
    ("감가상각비명세서", "유·무형자산감가상각비명세서", None),
    ("고정자산관리대장", "고정자산관리대장", None),
    ("소득금액조정합계표", "소득금액조정합계표", None),
    ("과목별소득금액조정명세서(1)", "과목별소득금액조정명세서(1)", None),
    ("과목별소득금액조정명세서(2)", "과목별소득금액조정명세서(2)", None),
    ("기업업무추진비조정명세서(갑)", "기업업무추진비조정명세서(갑)", None),
    ("기업업무추진비조정명세서(을)", "기업업무추진비조정명세서(을)", None),
    ("자본금과적립금조정명세서(갑)", "자본금과적립금조정명세서(갑)", None),
    ("자본금과적립금조정명세서(을)", "자본금과적립금조정명세서(을)", None),
    ("자본금과적립금조정명세서(병)", "자본금과적립금조정명세서(병)", None),
    ("주요계정명세서(갑)", "주요계정명세서(갑)", None),
    ("주요계정명세서(을)", "주요계정명세서(을)", None),
    ("중소기업등기준검토표", "중소기업등기준검토표", None),
    ("세액감면신청서", "세액감면(면제)신청서", None),
    ("이익잉여금처분(결손금처리)계산서", "이익잉여금처분(결손금처리)계산서", None),
    ("합계잔액시산표", "합계잔액시산표", None),
    ("원가명세서", "원가명세서", None),
    ("재무상태표", "재무상태표", "표준"),
    ("손익계산서", "손익계산서", "표준"),
    ("결손금처리계산서", "결손금처리계산서", None),
    ("이익잉여금처분계산서", "이익잉여금처분계산서", None),
]


class PdfDoc:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.name = self.path.name
        self.lines: dict[int, list[Line]] = {}
        self.text: dict[int, str] = {}
        self.masks: dict[int, list[tuple[float, float, float, float]]] = {}
        self.banner: dict[int, bool] = {}
        self.width: dict[int, float] = {}
        with pdfplumber.open(self.path) as pdf:
            self.n = len(pdf.pages)
            for i, p in enumerate(pdf.pages, 1):
                ws = split_glued([Word(w["text"], w["x0"], w["x1"], w["top"]) for w in p.extract_words()])
                self.lines[i] = cluster_lines(ws)
                self.text[i] = p.extract_text() or ""
                self.width[i] = float(p.width)
                self.banner[i] = "회색처리금액은확인필요" in nospace(self.text[i][:200])
                # 가림 칸: 채움색이 옅은 회색(0.85~0.95)인 곡선/사각형. 표 머리글 배경(0.929 사각형)은
                # 안내 문구가 없는 쪽에도 나오므로 '안내 문구가 있는 쪽의 곡선'만 가림으로 본다.
                ms = []
                if self.banner[i]:
                    for o in p.curves:
                        c = o.get("non_stroking_color")
                        if o.get("fill") and isinstance(c, (tuple, list)) and len(c) == 3 and len(set(c)) == 1 and 0.85 <= c[0] <= 0.95:
                            ms.append((o["x0"], o["x1"], o["top"], o["bottom"]))
                self.masks[i] = ms
                if self.lines[i] and self.banner[i] and "실습용" in self.lines[i][0].text:
                    self.lines[i] = self.lines[i][1:]  # 안내 문구 줄은 서식 식별에서 제외
        self.forms = self._identify()

    def _identify(self) -> dict[int, str]:
        forms: dict[int, str] = {}
        prev = None
        after_fs = False
        for pn in range(1, self.n + 1):
            head = nospace("".join(ln.text for ln in self.lines[pn][:7]))
            found = None
            if pn == 1 and ("세무조정계산서" in head or len(self.lines[pn]) <= 6):
                found = "표지"
            else:
                for name, key, excl in FORM_RULES:
                    hit = re.search(key[3:], head) if key.startswith("re:") else key in head
                    if hit and not (excl and excl in head):
                        found = name
                        break
            if found is None:
                first = nospace(self.lines[pn][0].text) if self.lines[pn] else ""
                if (after_fs or (prev in ("이익잉여금처분(결손금처리)계산서", "전산조직운용명세서", "결산부속명세서", "기타")
                                 and len(first) <= 14 and "법인세법" not in first)) and first.endswith("명세서"):
                    found = "결산부속명세서"
                elif prev == "결산부속명세서" and "명세서" not in self.text[pn]:
                    found = "기타"  # 뒤표지 등
                elif prev and (re.search(r"쪽중제\d+쪽", head) or prev == "결산부속명세서" or prev.startswith("공제감면")

                               or prev in ("세액감면신청서", "중소기업등기준검토표", "합계잔액시산표")):
                    found = prev
                else:
                    found = "기타"
            if found == "손익계산서":
                after_fs = True
            if found in ("합계잔액시산표",):
                after_fs = True
            forms[pn] = found
            prev = found
        return forms

    def masked(self, pn: int, x0: float, x1: float, top: float, bottom: float, pad: float = 2) -> tuple | None:
        """주어진 영역과 겹치는 가림 칸."""
        for m in self.masks.get(pn, []):
            if m[0] - pad < x1 and x0 < m[1] + pad and m[2] - pad < bottom and top < m[3] + pad:
                return m
        return None

    def pages_of(self, form: str) -> list[int]:
        return [p for p, f in self.forms.items() if f == form]

    def first_page(self, form: str) -> int | None:
        ps = self.pages_of(form)
        return ps[0] if ps else None

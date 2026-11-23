"""서식별 추출기 (doc/03 §2). 모든 값은 Evidence를 가진 Value로 나온다.

2차 변경: 좌표를 고정값 대신 서식 머리글(「코드」, 「01」 등) 기준으로 잡고,
칸마다 '숫자 / 글자 표기(예: 확인중) / 회색 가림 / 빈칸'을 구분한다 (doc/09).
"""
from __future__ import annotations

import re

from .models import Evidence, Status, Value
from .pdfsrc import Line, PdfDoc, is_num, nospace, parse_num

MASK_NOTE = "PDF에서 회색 처리(가림)된 칸 — 금액이 보이지 않음(0 아님)"


def ev(doc: PdfDoc, form: str, page: int, item: str, raw: str = "", column: str | None = None, bbox=None) -> Evidence:
    return Evidence(doc.name, form, page, item, column, raw.strip(), bbox)


def missing(key: str, label: str, form: str, note: str, questions=None, flag="서식없음", evidence=None) -> Value:
    return Value(key, label, status=Status.NEEDS_CHECK, note=f"{form}: {note}", questions=questions or [],
                 flag=flag, evidence=evidence or [])


def cell(doc: PdfDoc, form: str, pn: int, ln: Line, x_from: float, x_to: float, key: str, label: str,
         item: str, column: str | None = None, colon_amount: bool = False) -> Value:
    """한 칸을 읽는다: 숫자 → 확정 / 글자 → 확인 필요(글자표기) / 회색 → 확인 필요(가림) / 아무것도 없음 → 공란."""
    toks = [w for w in ln.words if x_from <= w.x0 < x_to]
    bbox = (x_from, x_to, ln.top - 2, ln.top + 10)
    e = ev(doc, form, pn, item, ln.text, column, bbox)
    if colon_amount:
        digits = "".join(w.text for w in toks if re.match(r"^[-:\d,]+$", w.text)).replace(":", "").replace(",", "")
        words = [w.text for w in toks if not re.match(r"^[-:\d,]+$", w.text)]
        amount = parse_num(digits) if re.search(r"\d", digits) else None
    else:
        nums = [w for w in toks if is_num(w.text)]
        amount = parse_num(nums[0].text) if nums else None
        words = [w.text for w in toks if not is_num(w.text) and w.text not in ("(", ")", ":")]
    if amount is not None:
        return Value(key, label, amount=amount, evidence=[e])
    if words and any(re.search(r"[가-힣]", t) for t in words):
        return Value(key, label, status=Status.NEEDS_CHECK, text=" ".join(words), flag="글자표기", evidence=[e],
                     note=f"금액 칸에 숫자 대신 '{' '.join(words)}' 표기")
    if doc.masked(pn, x_from, x_to, ln.top - 1, ln.top + 9):
        return Value(key, label, status=Status.NEEDS_CHECK, flag="가림", evidence=[e], note=MASK_NOTE)
    return Value(key, label, text="공란", evidence=[e])


# ───────────────────────── 표지 / 신고서 (I장) ─────────────────────────
def _sp(label: str) -> str:
    """글자 사이 띄어쓰기가 있어도 맞도록(예: '사 업 자 등 록 번 호')."""
    return r"\s*".join(re.escape(c) for c in label)


BASIC_PATTERNS = [
    ("biz_no", "사업자등록번호", r"①\s*" + _sp("사업자등록번호") + r"\s+(\d{3}-\d{2}-\d{5})"),
    ("corp_no", "법인등록번호", r"②\s*" + _sp("법인등록번호") + r"\s+(\d{6}-\d{7})"),
    ("name", "법인명", r"③\s*" + _sp("법인명") + r"\s+(.+?)\s*(?=④|$)"),
    ("ceo", "대표자명", r"⑤\s*" + _sp("대표자성명") + r"\s+(\S+)"),
    ("address", "사업장 소재지", r"⑦\s*" + _sp("소재지") + r"\s+(.+?)\s*(?=[⑧-⑳]|$)"),
    ("biz_type", "업태", r"⑧\s*" + _sp("업태") + r"\s+(.+?)\s*(?=⑨|$)"),
    ("biz_item", "종목", r"⑨\s*" + _sp("종목") + r"\s+(.+?)\s*(?=⑩|$)"),
    ("ind_code", "주업종코드", r"⑩\s*" + _sp("주업종코드") + r"\s+(\d+)"),
    ("period", "사업연도", r"⑪\s*" + _sp("사업연도") + r"\s+(\d{4}\.\d{2}\.\d{2}\s*~\s*\d{4}\.\d{2}\.\d{2})"),
    ("corp_type_code", "법인유형별구분", _sp("법인유형별구분") + r"\s+(\S+\s*코드\s*\d+)"),
    ("close_date", "결산확정일", _sp("결산확정일") + r"\s+(\d{4}\.\d{2}\.\d{2})"),
    ("file_date", "신고일", _sp("신고일") + r"\s+(\d{4}\.\d{2}\.\d{2})"),
]


KIND_COL = {"1": "중소기업", "2": "중소기업", "3": "중소기업", "6": "중소기업",
            "7": "중견기업", "8": "상호출자제한기업", "9": "그 외 기업"}
KIND_ROW = {"11": "상장법인", "21": "코스닥상장법인", "30": "기타법인", "60": "비영리법인",
            "71": "상장법인", "72": "코스닥상장법인", "73": "기타법인", "74": "비영리법인",
            "81": "상장법인", "82": "코스닥상장법인", "83": "기타법인", "84": "비영리법인",
            "91": "상장법인", "92": "코스닥상장법인", "93": "기타법인", "94": "비영리법인", "50": "당기순이익과세"}
CLASS_MAP = {"1": "내국법인", "2": "외국법인", "3": "외국투자법인"}


def read_check_marks(doc: PdfDoc, form: str, pn: int) -> dict[str, Value]:
    """신고서의 체크 표시(○)는 글자가 아니라 동그라미 도형이다. 도형 안에 들어 있는 글자(번호·코드)로 선택값을 판독한다.
    (담당자 미회신 시 판독 — 2026-11-21 사용자 지시, Q-2-03)"""
    import pdfplumber
    labels = {"corp_class": "법인구분(내국/외국)", "corp_kind": "법인종류(중소/일반 등)", "sme": "중소기업 여부"}
    res: dict[str, Value] = {}
    try:
        with pdfplumber.open(doc.path) as pdf:
            p = pdf.pages[pn - 1]
            circles = [c for c in p.curves if 7 < c["width"] < 18 and 7 < c["height"] < 18 and not c.get("fill")]
            marks = []
            for c in circles:
                ch = "".join(x["text"] for x in sorted(p.chars, key=lambda x: x["x0"])
                             if c["x0"] - 1 <= (x["x0"] + x["x1"]) / 2 <= c["x1"] + 1
                             and c["top"] - 1 <= (x["top"] + x["bottom"]) / 2 <= c["bottom"] + 1)
                marks.append((c, re.sub(r"[^\d]", "", ch)))
    except Exception:  # noqa: BLE001
        marks = []
    lines = doc.lines[pn]

    def line_of(key):
        return next((ln for ln in lines if key in nospace(ln.text)), None)

    def mark_ev(c, item, raw):
        return ev(doc, form, pn, f"{item} — 체크 표시(○) 판독", raw, None, (c["x0"], c["x1"], c["top"], c["bottom"]))

    cls_ln, sep = line_of("법인구분"), None
    if cls_ln:
        sep = next((w.x0 for w in cls_ln.words if w.text.startswith("⑭")), doc.width[pn])
        for c, d in marks:
            if abs(c["top"] - cls_ln.top) < 8 and c["x0"] < sep and d in CLASS_MAP:
                res["corp_class"] = Value("corp_class", labels["corp_class"], text=CLASS_MAP[d],
                                          note="신고서 체크 표시(○) 도형 판독", questions=["Q-2-03"],
                                          evidence=[mark_ev(c, "⑬법인구분", f"○ 안 글자 '{d}' — {cls_ln.text}")])
    for c, d in marks:
        if d in KIND_ROW and len(d) == 2:
            kind = KIND_COL.get(d[0], "") if d != "50" else "당기순이익과세"
            row = KIND_ROW[d]
            e = mark_ev(c, "⑮종류별구분", f"○ 안 코드 '{d}' — {kind} 열 · {row} 행")
            res["corp_kind"] = Value("corp_kind", labels["corp_kind"], text=f"{kind} · {row} (코드 {d})",
                                     note="신고서 체크 표시(○) 도형 판독", questions=["Q-2-03"], evidence=[e])
            res["sme"] = Value("sme", labels["sme"], text="해당" if kind == "중소기업" else "해당 없음",
                               note=f"종류별구분 코드 {d}({kind} 열)에서 판독", questions=["Q-2-03"], evidence=[e])
            break
    for k, label in labels.items():
        res.setdefault(k, Value(k, label, status=Status.NEEDS_CHECK, flag="체크표시",
                                note=f"신고서 p{pn}에 체크 표시로만 기재되어 있고 표시 도형을 판독하지 못함",
                                evidence=[ev(doc, form, pn, label)]))
    return res


def extract_basic(doc: PdfDoc) -> dict[str, Value]:
    out: dict[str, Value] = {}
    form = "법인세과세표준및세액신고서"
    pn = doc.first_page(form)
    if pn is None:
        for k, label, _ in BASIC_PATTERNS:
            out[k] = missing(k, label, form, "서식 없음")
        return out
    for k, label, pat in BASIC_PATTERNS:
        for ln in doc.lines[pn]:
            m = re.search(pat, ln.text)
            if m:
                txt = m.group(1).strip()
                if k == "name":
                    txt = re.sub(r"\s*\(\s*(\d+\s*기|최종)\s*\)\s*", "", txt)  # '(10기)', '(최종)' 표기 제거
                if k not in ("address",):
                    txt = txt.replace(" ", "")
                out[k] = Value(k, label, text=txt, evidence=[ev(doc, form, pn, label, ln.text)])
                break
        else:
            out[k] = missing(k, label, form, "텍스트에서 찾지 못함", flag="추출실패",
                             evidence=[ev(doc, form, pn, label)])
    out.update(read_check_marks(doc, form, pn))
    money = [("revenue", "수입금액"), ("tax_base", "과세표준"), ("calc_tax", "산출세액"),
             ("total_tax", "총부담세액"), ("prepaid", "기납부세액"), ("payable", "차감납부할세액")]
    for k, label in money:
        hit = None
        for ln in doc.lines[pn]:
            if re.sub(r"^\(\d+\)", "", nospace(ln.text)).startswith(label):
                # 라벨 글자 끝 다음부터 '법인세' 열(첫 금액 열)까지
                lab_end = max(w.x1 for w in ln.words if not is_num(w.text) and w.text not in ("(", ")")
                              and not re.match(r"^\(\d+\)$", w.text) and w.x0 < ln.words[0].x0 + 130)
                hit = cell(doc, form, pn, ln, lab_end, lab_end + 170, f"ret.{k}", label, label, "법인세")
                break
        out[f"ret.{k}"] = hit or missing(f"ret.{k}", label, form, f"'{label}' 줄을 찾지 못함", flag="추출실패")
    for ln in doc.lines.get(1, []):
        m = re.search(r"第\s*(\d+)\s*期", ln.text) or re.search(r"^제\s*(\d+)\s*기$", ln.text.strip())
        if m:
            out["term"] = Value("term", "기수", text=f"제{m.group(1)}기", evidence=[ev(doc, "표지", 1, "기수", ln.text)])
    out.setdefault("term", missing("term", "기수", "표지", "기수 표기를 찾지 못함", flag="추출실패"))
    return out


# ───────────────────────── 세액조정계산서 (III장) ─────────────────────────
TAXCALC_LEFT = {
    "01": "결산서상 당기순손익", "02": "익금산입", "03": "손금산입", "04": "차가감 소득금액",
    "05": "기부금한도초과액", "54": "기부금한도초과이월액 손금산입", "06": "각사업연도 소득금액",
    "07": "이월결손금", "08": "비과세소득", "09": "소득공제", "10": "과세표준", "55": "선박표준이익",
    "56": "과세표준(⑩+선박표준이익)", "11": "세율", "12": "산출세액", "13": "지점유보소득", "16": "산출세액 합계",
    "17": "최저한세 적용대상 공제감면세액", "18": "차감세액", "19": "최저한세 적용제외 공제감면세액",
    "20": "가산세액", "21": "가감계", "22": "중간예납세액", "23": "수시부과세액", "24": "원천납부세액",
    "25": "간접투자회사등의 외국납부세액", "26": "기납부세액 소계", "27": "신고납부전가산세액", "28": "기납부세액 합계",
}
TAXCALC_RIGHT = {"29": "감면분추가납부세액", "30": "차감납부할세액", "46": "차감납부할세액 계",
                 "48": "분납할세액", "49": "차감납부세액"}


def _anchor_x(lines: list[Line], code: str, label_key: str) -> float | None:
    for ln in lines:
        if label_key in nospace(ln.text):
            for w in ln.words:
                if w.text == code:
                    return w.x0
    return None


def extract_taxcalc(doc: PdfDoc) -> dict[str, Value]:
    form = "법인세과세표준및세액조정계산서"
    pn = doc.first_page(form)
    out: dict[str, Value] = {}
    if pn is None:
        for c, label in {**TAXCALC_LEFT, **TAXCALC_RIGHT}.items():
            out[f"tc.{c}"] = missing(f"tc.{c}", label, form, "서식 없음")
        return out
    lines = doc.lines[pn]
    lx = _anchor_x(lines, "01", "결산서상당기순손익")
    rx = _anchor_x(lines, "29", "감면분추가납부세액")
    if lx is None or rx is None:
        for c, label in {**TAXCALC_LEFT, **TAXCALC_RIGHT}.items():
            out[f"tc.{c}"] = missing(f"tc.{c}", label, form, "코드 기준 위치(01·29)를 찾지 못함", flag="추출실패")
        return out
    split = lx + 0.42 * (rx - lx)  # 오른쪽 단 항목명(⑤ 등)이 시작되기 전까지
    width = doc.width[pn]
    for codes, cx, xmax in ((TAXCALC_LEFT, lx, split), (TAXCALC_RIGHT, rx, width)):
        for c, label in codes.items():
            best: Value | None = None
            for ln in lines:
                for w in ln.words:
                    if w.text == c and abs(w.x0 - cx) <= 8:
                        v = cell(doc, form, pn, ln, w.x1 + 1, xmax, f"tc.{c}", label, f"코드{c} {label}")
                        if best is None or (best.blank and not v.blank):
                            best = v
                if best is not None and best.amount is not None:
                    break
            if best is None:  # 4차 포맷: 코드 두 자리가 한 글자씩 나뉘어 추출됨(예: '2' '3')
                for ln in lines:
                    ws_ = ln.words
                    for i_, w in enumerate(ws_[:-1]):
                        n_ = ws_[i_ + 1]
                        if w.text == c[0] and n_.text == c[1] and 0 <= n_.x0 - w.x1 <= 6 and abs(w.x0 - cx) <= 8:
                            best = cell(doc, form, pn, ln, n_.x1 + 1, xmax, f"tc.{c}", label, f"코드{c} {label}")
                            break
                    if best is not None:
                        break
            if best is None:  # 3차 포맷: 코드가 괄호에 붙어 추출됨(예: ')16')
                for ln in lines:
                    for w in ln.words:
                        if re.match(rf"^.*\){c}$", w.text) and (w.x0 < split if codes is TAXCALC_LEFT else w.x0 >= split - 20):
                            best = cell(doc, form, pn, ln, w.x1 + 1, xmax, f"tc.{c}", label, f"코드{c} {label}")
                            break
                    if best is not None:
                        break
            out[f"tc.{c}"] = best or missing(f"tc.{c}", label, form, f"코드 {c} 위치를 찾지 못함", flag="추출실패")
    return out


# ───────────────────────── 표준재무상태표·표준손익계산서 (II장) ─────────────────────────
LEVEL_RE = [(re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\."), 0), (re.compile(r"^\(\d+\)"), 1),
            (re.compile(r"^\d+\."), 2), (re.compile(r"^[가-하]\."), 3)]

# 합계 칸이 비어 있어도 하위 항목이 가려져 있거나 금액이 있으면 0으로 볼 수 없다
PARENT_RANGES = {
    "표준손익계산서": {1: range(2, 35), 35: range(36, 66), 67: range(68, 129), 130: range(131, 179), 179: range(180, 217)},
    "표준재무상태표": {1: range(2, 80), 80: range(81, 228), 229: range(230, 284), 284: range(285, 333), 334: range(335, 382)},
}


def label_level(label: str) -> int:
    for rx, lv in LEVEL_RE:
        if rx.match(label):
            return lv
    if any(k in label for k in ("총계", "자본총계", "부채와")):
        return 0
    return 3


def extract_standard(doc: PdfDoc, form: str) -> dict[int, Value]:
    """코드별 금액. 「코드」 머리글 x좌표로 코드 열을 찾고 좌우 2단을 나눈다.
    빈 칸이 ':'로 채워진 포맷(1·2차)과 아무것도 없는 포맷(3차) 모두 처리."""
    out: dict[int, Value] = {}
    for pn in doc.pages_of(form):
        heads = sorted(w.x0 for ln in doc.lines[pn] for w in ln.words if w.text == "코드")
        if not heads:
            continue
        mid = (heads[0] + heads[-1]) / 2 if len(heads) >= 2 else doc.width[pn] / 2 - 15
        pending: dict[bool, str | None] = {True: None, False: None}
        for ln in doc.lines[pn]:
            ws = ln.words
            # 코드가 없는 줄의 글자 = 다음 줄 항목명의 앞부분(두 줄 항목명)
            for side in (True, False):
                part = [x for x in ws if (x.x0 < mid - 12) == side]
                if part and not any(x.text.isdigit() and any(-6 <= x.x0 - h <= 14 for h in heads) for x in part) \
                        and not any(x.text == "코드" for x in part):
                    pending[side] = " ".join(x.text for x in part if not re.match(r"^[-:\d,]+$", x.text))
            for i, w in enumerate(ws):
                if not w.text.isdigit():
                    continue
                if not any(-6 <= w.x0 - h <= 14 for h in heads):
                    continue
                left = w.x0 < mid
                xmax = doc.width[pn]
                if left:
                    nxt_label = [x for x in ws[i + 1:] if not re.match(r"^[-:\d,]+$", x.text) and x.x0 > w.x1]
                    xmax = nxt_label[0].x0 if nxt_label else mid + 40
                if left:
                    lab_ws = [x for x in ws if 30 <= x.x0 < w.x0 and x is not w]
                else:  # 왼쪽 단 마지막 금액/코드 뒤부터 오른쪽 코드 앞까지
                    lefts = [x for x in ws if x.x0 < mid - 12 and (re.match(r"^[-:\d,]+$", x.text))]
                    start = max((x.x1 for x in lefts), default=mid - 15)
                    lab_ws = [x for x in ws if start <= x.x0 < w.x0 and x is not w and not re.match(r"^[-:\d,]+$", x.text)]
                if not lab_ws:
                    continue
                label = re.sub(r"^\)\s*", "", " ".join(x.text for x in lab_ws).strip())
                if not label_level(nospace(label)) < 3 and not re.match(r"^[가-하]\.", nospace(label)):
                    prev_lab = pending.get(left)
                    if prev_lab:
                        label = prev_lab + " " + label
                pending[left] = None
                code = int(w.text)
                v = cell(doc, form, pn, ln, w.x1 + 1, xmax, f"{form}.{code}", label,
                         f"코드{code} {nospace(label)}", colon_amount=True)
                if v.text and v.flag == "글자표기" and all(t in ":" for t in v.text.split()):
                    v = Value(v.key, label, text="공란", evidence=v.evidence)
                if code not in out or (out[code].amount is None and v.amount is not None):
                    out[code] = v
    # 합계빈칸 점검
    for parent, rng in PARENT_RANGES.get(form, {}).items():
        pv = out.get(parent)
        if pv is not None and pv.blank:
            kids = [out[c] for c in rng if c in out and out[c].status == Status.NEEDS_CHECK]
            if kids:
                why = ", ".join(f"코드{k.key.split('.')[-1]} {'가림' if k.flag == '가림' else '글자 표기'}"
                                for k in kids[:4])
                out[parent] = Value(pv.key, pv.label, status=Status.NEEDS_CHECK, flag="합계빈칸", evidence=pv.evidence,
                                    note=f"합계 칸이 빈칸이지만 하위 항목({why})이 확인되지 않아 0으로 볼 수 없음")
    return out


# ───────────────────────── 결산서 (대조용, R12) ─────────────────────────
FS_ITEMS = {
    "재무상태표": [("자산총계", 228), ("부채총계", 333), ("자본총계", 382), ("자본금", 334)],
    "손익계산서": [("Ⅰ.매출액", 1), ("Ⅲ.매출총이익", 66), ("Ⅳ.판매비와관리비", 67), ("Ⅴ.영업", 129),
               ("Ⅵ.영업외수익", 130), ("Ⅶ.영업외비용", 179), ("Ⅷ.법인세차감전", 217), ("Ⅸ.법인세등", 218),
               ("Ⅹ.당기순", 219)],
}


def extract_closing(doc: PdfDoc) -> dict[str, dict[int, Value]]:
    out: dict[str, dict[int, Value]] = {}
    for form, items in FS_ITEMS.items():
        out[form] = {}
        for pn in doc.pages_of(form):
            # 당기·전기 열 경계: '(전)기' 머리글
            prior_x = None
            for ln in doc.lines[pn][:14]:
                if any("(당)기" in w.text for w in ln.words) and any("(전)기" in w.text for w in ln.words):
                    prior_x = next(w.x0 for w in ln.words if "(전)기" in w.text) - 30
                    break
            for ln in doc.lines[pn]:
                ns = nospace(ln.text)
                ns2 = re.sub(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.", "", ns)
                for key, code in items:
                    if code in out[form]:
                        continue
                    if key == "자본금":  # 대분류 줄(Ⅰ.자본금)만 — 세부 줄은 금액이 소계 열에 있음
                        if not re.match(r"^[ⅠⅡⅢⅣⅤ]+\.자본금", ns):
                            continue
                    elif not ns.startswith(key):
                        continue
                    lab_end = max(w.x1 for w in ln.words if not is_num(w.text))
                    sign = -1 if ("손실" in ns or "결손금" in ns) else 1
                    bound = prior_x or (lab_end + (doc.width[pn] - lab_end) / 2)
                    for col, (a, b) in (("당기", (lab_end, bound)), ("전기", (bound, doc.width[pn]))):
                        v = cell(doc, form, pn, ln, a, b, f"fs.{form}.{col}.{code}", key, key, col)
                        if v.amount is not None and sign < 0 and v.amount > 0:
                            v.amount = -v.amount
                        out[form][code if col == "당기" else -code] = v
    return out


# ───────────────────────── 자본금과적립금조정명세서(갑) 결손금 (IV장) ─────────────────────────
LOSS_COLS = {107: "발생액 계", 142: "일반결손금", 180: "배분한도초과결손금", 236: "소급공제", 270: "차감계",
             306: "기공제액", 343: "당기공제액", 376: "보전", 407: "감소 계", 440: "잔액 기한내",
             480: "잔액 기한경과", 517: "잔액 계"}


def extract_losses(doc: PdfDoc) -> list[dict]:
    form = "자본금과적립금조정명세서(갑)"
    rows = []
    for pn in doc.pages_of(form):
        dx = 0.0
        for ln in doc.lines[pn]:
            for w in ln.words:
                if w.text.startswith("⑥사업연도"):
                    dx = w.x0 - 54
        for ln in doc.lines[pn]:
            if ln.words and re.match(r"^\d{4}[-.]\d{2}[-.]\d{2}$", ln.words[0].text) and any(is_num(w.text) and "," in w.text for w in ln.words[1:]):
                row = {"year": ln.words[0].text, "page": pn, "raw": ln.text, "cols": {}}
                for w in ln.words[1:]:
                    if is_num(w.text):
                        x = min(LOSS_COLS, key=lambda c: abs(c + dx - w.x0))
                        row["cols"][LOSS_COLS[x]] = Value(f"loss.{ln.words[0].text}.{LOSS_COLS[x]}", LOSS_COLS[x],
                                                          amount=parse_num(w.text),
                                                          evidence=[ev(doc, form, pn, f"Ⅱ.결손금 {ln.words[0].text} {LOSS_COLS[x]}", ln.text)])
                rows.append(row)
    return rows


# ───────────────────────── 소득금액조정 (V장) ─────────────────────────
def extract_adjustments(doc: PdfDoc) -> dict:
    form = "소득금액조정합계표"
    pn = doc.first_page(form)
    res = {"present": pn is not None, "page": pn, "add": [], "deduct": [], "add_total": None, "deduct_total": None}
    if pn is None:
        return res
    mid = None
    started = False
    for ln in doc.lines[pn]:
        t = nospace(ln.text)
        if "①과" in t or "①과목" in t:
            started = True
            for w in ln.words:
                if w.text.startswith("④과"):
                    mid = w.x0 - 10
            continue
        if not started:
            continue
        mid = mid or doc.width[pn] / 2
        for side, ws in (("add", [w for w in ln.words if w.x0 < mid]), ("deduct", [w for w in ln.words if w.x0 >= mid])):
            if not ws:
                continue
            if ws[0].text == "합계":
                nums = [w for w in ws if is_num(w.text)]
                res[f"{side}_total"] = Value(f"adj.{side}.total", "합계", amount=parse_num(nums[0].text) if nums else None,
                                             text=None if nums else "공란",
                                             evidence=[ev(doc, form, pn, f"{'익금산입·손금불산입' if side == 'add' else '손금산입·익금불산입'} 합계", ln.text)])
                continue
            nums = [w for w in ws if is_num(w.text) and "," in w.text]
            if not nums or "210" in ws[0].text:
                continue
            name = " ".join(w.text for w in ws if w.x0 < nums[0].x0)
            if not name:  # 과목명이 위·아래 줄로 나뉨
                i_ln = doc.lines[pn].index(ln)
                parts = []
                for nb in (doc.lines[pn][i_ln - 1] if i_ln else None, doc.lines[pn][i_ln + 1] if i_ln + 1 < len(doc.lines[pn]) else None):
                    if nb is not None:
                        t_ = [w for w in nb.words if (w.x0 < mid) == (side == "add") and not is_num(w.text)]
                        if t_ and abs(nb.top - ln.top) < 12:
                            parts.append(" ".join(w.text for w in t_ if w.x0 < nums[0].x0))
                name = "".join(parts)
            rest = [w.text for w in ws if w.x0 > nums[0].x0 and not is_num(w.text)]
            code = [w.text for w in ws if w.x0 > nums[0].x0 and w.text.isdigit()]
            res[side].append({
                "subject": name, "amount": Value(f"adj.{side}.{name}", name, amount=parse_num(nums[0].text),
                                                 evidence=[ev(doc, form, pn, name, ln.text)]),
                "disposal": " ".join(rest), "code": code[0] if code else ""})
    for sub, side in (("과목별소득금액조정명세서(1)", "add"), ("과목별소득금액조정명세서(2)", "deduct")):
        sp = doc.first_page(sub)
        res[f"{side}_detail_page"] = sp
        if sp is None:
            continue
        for ln in doc.lines[sp]:
            nums = [w for w in ln.words if is_num(w.text) and "," in w.text]
            if nums and ln.words[0].text != "합계":
                name = " ".join(w.text for w in ln.words if w.x0 < nums[0].x0)
                for item in res[side]:
                    if nospace(item["subject"]) == nospace(name):
                        after = [w.text for w in ln.words if w.x0 > nums[0].x1]
                        item["detail"] = " ".join(after[1:]) if len(after) > 1 else ""
                        item["detail_ev"] = ev(doc, sub, sp, name, ln.text)
    return res


def extract_entertainment(doc: PdfDoc) -> dict:
    form = "기업업무추진비조정명세서(갑)"
    pn = doc.first_page(form)
    out = {"page": pn}
    if pn is None:
        return out
    for ln in doc.lines[pn]:
        t = nospace(ln.text)
        lab = [w for w in ln.words if not is_num(w.text)]
        x_from = max(w.x1 for w in lab) if lab else ln.words[0].x0
        if t.startswith("①기업업무추진비해당금액"):
            out["amount"] = cell(doc, form, pn, ln, x_from, doc.width[pn], "ent.amount", "기업업무추진비 해당금액", "①기업업무추진비해당금액")
        if "한도초과액" in t and t[0] in "⑭⑮":
            out["excess"] = cell(doc, form, pn, ln, x_from, doc.width[pn], "ent.excess", "한도초과액", t[:12])
    return out


# ───────────────────────── 공제·감면 (VI장) ─────────────────────────
LAW_RE = re.compile(r"(조세특례제한법|조특법|법인세법|^영제|^법제|제\d+조)")


CREDIT_COLS = {"③": "대상세액", "④": "감면세액", "⑤": "전기이월액", "⑥": "당기발생액", "⑦": "공제세액"}


def extract_credits(doc: PdfDoc) -> dict:
    """공제감면세액합계표(갑). 열 머리글(③대상세액 ④감면세액 ⑤전기이월액 ⑥당기발생액 ⑦공제세액)로 숫자의 의미를 정한다.
    '적용된 공제·감면세액'은 ④감면세액 또는 ⑦공제세액 열의 값뿐이다(⑥당기발생액·⑤이월액은 적용액이 아님 — 3차)."""
    form = "공제감면세액합계표(갑)"
    pages = doc.pages_of(form)
    res = {"present": bool(pages), "pages": pages, "rows": [], "total": None}
    section = ""
    for pn in pages:
        code_heads = [w.x0 for ln in doc.lines[pn] for w in ln.words if w.text == "코드"]
        cols: dict[str, float] = {}
        for ln in doc.lines[pn]:
            t = nospace(ln.text)
            if "코드" in t and any(k in t for k in ("대상세액", "공제세액", "감면세액", "당기발생액")):
                cols = {}
                ws = ln.words
                for i, w in enumerate(ws):
                    for circ, name in CREDIT_COLS.items():
                        if w.text.startswith(circ):
                            lab = w if len(w.text) > 1 else (ws[i + 1] if i + 1 < len(ws) else w)
                            cols[name] = lab.x0 + 12
                continue
            m = re.match(r"^(\d)\.(최저한세적용(?:제외|대상)공제감면세액|.*세액공제)", t)
            if m:
                section = "최저한세 적용제외" if "제외" in t else ("최저한세 적용대상" if "대상" in t else "세액공제")
            if t.startswith("공제감면세액총계"):
                nums = [w.text for w in ln.words if is_num(w.text) and "," in w.text]
                res["total"] = Value("cr.total", "공제감면세액 총계", amount=parse_num(nums[-1]) if nums else None,
                                     text=None if nums else "공란", evidence=[ev(doc, form, pn, "공제감면세액총계", ln.text)])
                continue
            codes = [w for w in ln.words if re.match(r"^[0-9][0-9A-Z]{2}$", w.text)
                     and any(abs(w.x0 - h) <= 14 for h in code_heads)]
            nums = [w for w in ln.words if is_num(w.text) and "," in w.text]
            if not (codes and nums):
                continue
            before = [w for w in ln.words if w.x0 < codes[0].x0]
            law = " ".join(w.text for w in before if LAW_RE.search(w.text))
            name = " ".join(w.text for w in before if not LAW_RE.search(w.text))
            name = re.sub(r"^\S\s+(?=\()", "", name)          # 세로 구분 글자('공') 제거
            name = re.sub(r"\(\d{3}\)\s*", "", name).strip()  # 서식 줄번호 '(212)' 제거
            if re.search(r"(합계|소계|총계|계$)", nospace(name)):
                continue
            vals = {}
            for w in nums:
                col = min(cols, key=lambda c: abs(cols[c] - w.x0)) if cols else "감면세액"
                vals[col] = Value(f"cr.{codes[0].text}.{col}", f"{name} {col}", amount=parse_num(w.text),
                                  evidence=[ev(doc, form, pn, f"{name} 코드{codes[0].text} {col}", ln.text, col)])
            if "공제세액" in cols:
                section = "세액공제"
            applied_col = "공제세액" if section == "세액공제" else "감면세액"
            applied = vals.get(applied_col) or Value(f"cr.{codes[0].text}.{applied_col}", f"{name} {applied_col}", text="공란",
                                                     evidence=[ev(doc, form, pn, f"{name} 코드{codes[0].text} {applied_col}", ln.text, applied_col)])
            res["rows"].append({"name": name, "law": law, "code": codes[0].text, "section": section,
                                "amount": applied, "applied_col": applied_col, "vals": vals})
    for row in res["rows"]:
        for pn in doc.pages_of("세액감면신청서"):
            for ln in doc.lines[pn]:
                m = re.search(rf"\b{row['code']}\s+(\d+(?:\.\d+)?%)", ln.text)
                if m:
                    row["rate"] = Value(f"cr.{row['code']}.rate", "감면율", text=m.group(1),
                                        evidence=[ev(doc, "세액감면신청서", pn, f"코드{row['code']} 공제율", ln.text)])
                    break
            if row.get("rate"):
                break
    return res


# ───────────────────────── 가지급금 등의 인정이자 조정명세서(을) ─────────────────────────
def extract_deemed_interest(doc: PdfDoc) -> list[dict]:
    """특수관계인별 가지급금 기말 잔액(당좌대출이자율 적수 계산표의 '계' 줄 잔액). 3차: 결산부속명세서에 대여금 명세가
    없거나 표준재무상태표 계정명(예: 기타당좌자산)만으로 가지급금인지 알 수 없을 때 대조 근거로 쓴다."""
    form = "가지급금등의인정이자조정명세서(을)"
    out = []
    for pn in doc.pages_of(form):
        who = {"name": "", "role": ""}
        sec2 = False
        for ln in doc.lines[pn]:
            t = nospace(ln.text)
            m = re.search(r"직책\((.*?)\)성명\((.*?)\)", t)
            if m:
                who = {"role": m.group(1), "name": m.group(2)}
            if t.startswith("2.당좌대출이자율") or t.startswith("1.가중평균차입이자율"):
                sec2 = True
            if sec2 and t.startswith("계"):
                money = [w for w in ln.words[1:] if is_num(w.text) and "," in w.text]
                small = [w for w in ln.words[1:] if w.text.isdigit() and int(w.text) <= 366]
                if not money:
                    continue
                cut = small[0].x0 if small else 1e9
                pre = [w for w in money if w.x0 < cut]
                if not pre:
                    continue
                bal = pre[-1]
                out.append({"name": who["name"], "role": who["role"], "page": pn,
                            "balance": Value(f"di.{who['name']}.{pn}", f"가지급금 기말잔액({who['name']})", amount=parse_num(bal.text),
                                             evidence=[ev(doc, form, pn, f"{who['name']} 가지급금 잔액(계)", ln.text, "잔액")])})
                sec2 = False
    uniq = {}
    for x in out:
        uniq.setdefault((x["name"], x["balance"].amount), x)
    return list(uniq.values())


# ───────────────────────── 법인지방소득세 (III장) ─────────────────────────
def extract_local(doc: PdfDoc) -> dict[str, Value]:
    form = "법인지방소득세신고서"
    pn = doc.first_page(form)
    out = {}
    items = [("local.calc", "법인지방소득세 산출세액", "표준산출세액"), ("local.total", "법인지방소득세 총부담세액", "총부담세액"),
             ("local.payable", "법인지방소득세 차감납부할세액", "차감납부할세액")]
    for k, label, key in items:
        if pn is None:
            out[k] = missing(k, label, form, "서식 없음")
            continue
        for ln in doc.lines[pn]:
            if nospace(ln.text).startswith(key):
                lab = [w for w in ln.words if not is_num(w.text)]
                x_from = max(w.x1 for w in lab if w.x0 < ln.words[0].x0 + 110)
                out[k] = cell(doc, form, pn, ln, x_from, x_from + 200, k, label, key)
                break
        else:
            out[k] = missing(k, label, form, f"'{key}' 줄을 찾지 못함", flag="추출실패")
    return out


# ───────────────────────── 결산부속명세서 (VIII장) ─────────────────────────
def extract_schedules(doc: PdfDoc) -> dict[str, dict]:
    """명세서 제목 단위로 분리. 열은 머리글 x좌표로 매핑 (doc/01 F5). 빈 금액 칸이 회색이면 가림으로 기록."""
    form = "결산부속명세서"
    res: dict[str, dict] = {}
    cur = None
    cols: dict[str, float] = {}
    for pn in doc.pages_of(form):
        for ln in doc.lines[pn]:
            t = nospace(ln.text)
            if len(ln.words) == 1 and t.endswith("명세서"):
                cur = t
                res.setdefault(cur, {"page": pn, "rows": [], "total": {}, "title_page": pn})
                cols = {}
                continue
            if cur is None:
                continue
            if t.startswith("코드") and ("계정과목명" in t or "금액" in t):
                cols = {}
                code_seen = 0
                for w in ln.words:
                    if w.text == "코드":
                        code_seen += 1
                        if code_seen == 2:
                            cols["거래처코드"] = w.x0
                    elif w.text in ("기초액", "당기증가", "당기감소", "금액", "거래처명", "적요", "비고", "상대거래처"):
                        cols[w.text] = w.x0
                continue
            numcols = [c for c in ("기초액", "당기증가", "당기감소", "금액") if c in cols]
            if not numcols:
                continue

            def masked_cols(vals: dict, keyp: str, label: str):
                for c in numcols:
                    if c not in vals and doc.masked(pn, cols[c] - 15, cols[c] + 45, ln.top - 1, ln.top + 9):
                        vals[c] = Value(f"{keyp}.{c}", c, status=Status.NEEDS_CHECK, flag="가림", note=MASK_NOTE,
                                        evidence=[ev(doc, f"결산부속명세서-{cur}", pn, f"{label} {c}", ln.text, c,
                                                     (cols[c] - 15, cols[c] + 45, ln.top - 2, ln.top + 10))])
            if ln.words[0].text == "합계":
                for w in ln.words[1:]:
                    if is_num(w.text):
                        c = min(numcols, key=lambda c: abs(cols[c] - w.x0))
                        res[cur]["total"][c] = Value(f"sch.{cur}.total.{c}", f"{cur} 합계 {c}", amount=parse_num(w.text),
                                                     evidence=[ev(doc, f"결산부속명세서-{cur}", pn, f"합계 {c}", ln.text, c)])
                masked_cols(res[cur]["total"], f"sch.{cur}.total", "합계")
                continue
            five = bool(re.match(r"^\d{2,6}$", ln.words[0].text)) and "상대거래처" in cols
            if re.match(r"^\d{3}$", ln.words[0].text) or five:
                if five:  # 3차 포맷: 첫 칸이 거래처코드, 다음이 상대거래처
                    row = {"account_code": "", "account": cur.replace("명세서", ""), "partner_code": ln.words[0].text,
                           "partner": " ".join(w.text for w in ln.words[1:] if not is_num(w.text) and w.x0 < min(cols[c] for c in numcols) - 20),
                           "page": pn, "raw": ln.text, "vals": {}}
                    rest = ln.words[1:]
                else:
                    row = {"account_code": ln.words[0].text, "account": ln.words[1].text if len(ln.words) > 1 else "",
                           "partner_code": "", "partner": "", "page": pn, "raw": ln.text, "vals": {}}
                    rest = ln.words[2:]
                for w in rest:
                    if re.match(r"^\d{5}$", w.text) and not row["partner_code"]:
                        row["partner_code"] = w.text
                    elif is_num(w.text) and ("," in w.text or (five and w.x0 > min(cols[c] for c in numcols) - 40)):
                        c = min(numcols, key=lambda c: abs(cols[c] - w.x0))
                        row["vals"][c] = Value(f"sch.{cur}.{row['partner_code']}.{c}", c, amount=parse_num(w.text),
                                               evidence=[ev(doc, f"결산부속명세서-{cur}", pn,
                                                            f"{row['partner_code']} {c}", ln.text, c)])
                    elif not is_num(w.text) and not row["partner"] and row["partner_code"]:
                        row["partner"] = w.text
                masked_cols(row["vals"], f"sch.{cur}.{row['partner_code']}", row["partner_code"])
                res[cur]["rows"].append(row)
    return res


def extract_all(doc: PdfDoc) -> dict:
    return {
        "doc": doc,
        "basic": extract_basic(doc),
        "tc": extract_taxcalc(doc),
        "bs": extract_standard(doc, "표준재무상태표"),
        "is": extract_standard(doc, "표준손익계산서"),
        "closing": extract_closing(doc),
        "losses": extract_losses(doc),
        "adj": extract_adjustments(doc),
        "ent": extract_entertainment(doc),
        "credits": extract_credits(doc),
        "deemed": extract_deemed_interest(doc),
        "local": extract_local(doc),
        "sch": extract_schedules(doc),
    }

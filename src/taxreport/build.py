"""대사·계산·장별 내용 구성 (doc/03 §3, doc/04, doc/09).

2차 원칙
- 가림·글자 표기·합계 빈칸 등 확정할 수 없는 입력은 «확인 필요»로 두고, 그 값에 의존하는 계산만 보류한다.
- 서식끼리 숫자가 다르면 같은 범위의 값인지 먼저 확인하고, 근거 없이 한쪽으로 덮어쓰지 않는다(경고로 남김).
- 빈칸을 0으로 넣거나 다른 서식에서 임의로 가져오지 않는다(서식 칸이 '공란'으로 확정된 경우만 계산에서 0).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .extract import extract_all, label_level
from .models import Status, Value
from .pdfsrc import PdfDoc, is_num, nospace, parse_num

OK = (Status.CONFIRMED, Status.DERIVED, Status.ASSUMED)


def amt(v: Value | None):
    return None if v is None else v.amount


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b * 100


def won(n) -> str:
    return "–" if n is None else f"{n:,.0f}"


def signed(n) -> str:
    return "–" if n is None else (f"+{n:,.0f}" if n > 0 else f"{n:,.0f}")


def p1(x) -> str:
    return "–" if x is None else f"{x:,.1f}%"


def known(v: Value | None) -> bool:
    """계산에 쓸 수 있는 값인가(확정·계산·가정이고 금액이 있거나 '공란'으로 확정)."""
    return v is not None and v.status in OK and (v.amount is not None or v.blank)


def num(v: Value | None):
    if not known(v):
        return None
    return 0 if v.blank else v.amount


def calc(key: str, label: str, fn, inputs: list[Value], formula: str, **kw) -> Value:
    """입력이 모두 확정이면 계산, 아니면 '계산 보류'(의존 계산만 보류)."""
    bad = [v for v in inputs if not known(v)]
    if bad:
        return Value(key, label, status=Status.NEEDS_CHECK, flag="계산보류", inputs=[v.key for v in inputs],
                     formula=formula, questions=sorted({q for v in bad for q in v.questions}),
                     note="보류: 입력값 " + ", ".join(f"{v.label}({v.flag or v.status.value})" for v in bad) + " 확인 필요")
    vals = [num(v) for v in inputs]
    try:
        res = fn(*vals)
    except ZeroDivisionError:
        res = None
    status = Status.ASSUMED if any(v.status == Status.ASSUMED for v in inputs) else Status.DERIVED
    if res is None:
        return Value(key, label, text="해당 없음", status=status, inputs=[v.key for v in inputs], formula=formula,
                     note="기준값이 0")
    return Value(key, label, amount=res, status=status, inputs=[v.key for v in inputs], formula=formula, **kw)


def ratio(key, label, a: Value, b: Value, formula) -> Value:
    return calc(key, label, lambda x, y: x / y * 100 if y else None, [a, b], formula)


class Ledger:
    """근거 대장: 보고서에 쓰인 모든 값."""

    def __init__(self):
        self.rows: list[tuple[str, Value]] = []
        self._seen: set[tuple[str, str]] = set()

    def use(self, section: str, v: Value | None) -> Value | None:
        if v is None:
            return v
        k = (section, v.key)
        if k not in self._seen:
            self._seen.add(k)
            self.rows.append((section, v))
        return v


def _revenue_scope(doc: PdfDoc) -> dict | None:
    """수입금액조정명세서의 '영업외수익' 행(수입금액에 포함된 매출 외 항목)."""
    pn = doc.first_page("수입금액조정명세서")
    if pn is None:
        return None
    items, masked = [], False
    for ln in doc.lines[pn]:
        t = nospace(ln.text)
        if t.startswith("영업외수익"):
            nums = [w.text for w in ln.words if is_num(w.text) and "," in w.text]
            name = ln.words[1].text if len(ln.words) > 1 else "영업외수익"
            if nums:
                items.append((name, parse_num(nums[0])))
        if doc.masked(pn, 0, doc.width[pn], ln.top - 1, ln.top + 9) and ("상품매출" in t or t.startswith("계")):
            masked = True
    return {"page": pn, "items": items, "masked": masked}


def _rekey(obj, yr: str, seen: set):
    if id(obj) in seen:
        return
    seen.add(id(obj))
    if isinstance(obj, Value):
        if not obj.key.startswith(yr + ":"):
            obj.key = f"{yr}:{obj.key}"
    elif isinstance(obj, dict):
        for v in obj.values():
            _rekey(v, yr, seen)
    elif isinstance(obj, list):
        for v in obj:
            _rekey(v, yr, seen)


def build(prior_pdf: str, current_pdf: str, cfg_dir: str | Path) -> dict:
    cfg_dir = Path(cfg_dir)
    firm = yaml.safe_load((cfg_dir / "firm.yaml").read_text())
    qcfg = yaml.safe_load((cfg_dir / "questions.yaml").read_text())

    P = extract_all(PdfDoc(prior_pdf))
    C = extract_all(PdfDoc(current_pdf))
    # 전기·당기 값의 키가 겹치지 않게 연도를 붙인다(계산 보류의 원인 추적용)
    for X in (P, C):
        yr0 = X["basic"]["period"].text[:4]
        _rekey(X, yr0, set())
    L = Ledger()
    findings: list[dict] = []
    py = P["basic"]["period"].text[:4]
    cy = C["basic"]["period"].text[:4]

    def finding(rid, title, ok, detail, evs, questions=(), kind="대사"):
        findings.append({"id": rid, "title": title, "ok": ok, "detail": detail, "kind": kind,
                         "evidence": [e for e in evs if e], "questions": list(questions)})

    same = bool(P["basic"]["biz_no"].text) and P["basic"]["biz_no"].text == C["basic"]["biz_no"].text
    consecutive = int(cy) == int(py) + 1
    if not (same and consecutive):
        raise SystemExit(f"입력 오류: 같은 회사의 연속 2개 사업연도가 아닙니다 ({P['basic']['biz_no'].text}/{py}, {C['basic']['biz_no'].text}/{cy})")

    PB, CB, PI, CI = P["bs"], C["bs"], P["is"], C["is"]

    def S(d, code, form="표준서식") -> Value:
        v = d.get(code)
        if v is None:
            return Value(f"missing.{form}.{code}", f"코드{code}", status=Status.NEEDS_CHECK, flag="서식없음",
                         note=f"{form}에서 코드{code}를 찾지 못함")
        return v

    def ev0(v):
        return v.evidence[0] if v is not None and v.evidence else None

    def evs(*vs):
        return [e for v in vs if v is not None for e in v.evidence]

    # ════════════ I. 기본정보 ════════════
    b = C["basic"]
    basic_rows = []
    for key, label in [("name", "법인명"), ("biz_no", "사업자등록번호"), ("ceo", "대표자명"), ("corp_no", "법인등록번호"),
                       ("address", "사업장 소재지"), ("biz_type", "업태"), ("biz_item", "종목"), ("ind_code", "주업종코드"),
                       ("term", "기수"), ("period", "사업연도"), ("corp_type_code", "법인유형별구분"),
                       ("corp_class", "법인구분"), ("corp_kind", "법인종류"), ("sme", "중소기업 여부"),
                       ("close_date", "결산확정일"), ("file_date", "신고일")]:
        v = L.use("I", b[key])
        basic_rows.append({"label": label, "v": v})
        if key in ("name", "biz_no", "ceo", "corp_no", "address", "biz_type", "biz_item", "ind_code"):
            pv = P["basic"][key]
            if pv.text != v.text:
                finding(f"I-{key}", f"기본정보 '{label}' 전기·당기 다름", None,
                        f"{py}: {pv.text} / {cy}: {v.text} (당기 신고서 기준으로 표시)", evs(pv, v), kind="참고")
    basic_note = (f"{cy} 사업연도 법인세과세표준및세액신고서 기준. 법인구분·법인종류·중소기업 여부는 신고서에 체크 표시(○)로만 "
                  f"기재되어 있어, 동그라미 도형 안의 번호·코드를 판독해 적었습니다(담당자 미회신, Q-2-03). "
                  f"판독하지 못한 항목은 «확인 필요»로 표시합니다.")

    # ════════════ II. 표준재무제표 ════════════
    def fs_rows(Pd, Cd):
        codes = sorted({c for c, v in Pd.items() if not v.blank} | {c for c, v in Cd.items() if not v.blank})
        rows = []
        for c in codes:
            cv, pv = Cd.get(c), Pd.get(c)
            lab = (cv or pv).label
            L.use("II", cv), L.use("II", pv)
            rows.append({"code": c, "label": lab, "level": label_level(nospace(lab)), "cur": cv, "prior": pv,
                         "src": f"p{ev0(cv).page if ev0(cv) else '–'} / p{ev0(pv).page if ev0(pv) else '–'}",
                         "src_pc": f"p{ev0(pv).page if ev0(pv) else '–'} / p{ev0(cv).page if ev0(cv) else '–'}"})
        return rows

    bs_rows = fs_rows(PB, CB)
    is_rows = fs_rows(PI, CI)

    for yr, B in ((py, PB), (cy, CB)):
        a, l_, q = S(B, 228), S(B, 333), S(B, 382)
        if known(a) and known(l_) and known(q):
            finding(f"R04-{yr}", f"{yr} 자산총계 = 부채총계 + 자본총계", num(a) == num(l_) + num(q),
                    f"{won(num(a))} vs {won(num(l_))} + {won(num(q))}", evs(a, l_, q))
        else:
            finding(f"R04-{yr}", f"{yr} 자산총계 = 부채총계 + 자본총계", None, "입력값 확인 필요로 검산 보류", evs(a, l_, q), kind="보류")

    # R17 서식 안 검산 (표준손익계산서·표준재무상태표) — 보이는 숫자라도 서로 맞지 않으면 경고
    def lvl2_sum(d, lo, hi):
        items = [v for c, v in d.items() if lo < c < hi and label_level(nospace(v.label)) == 2 and not v.blank]
        return (sum(num(v) for v in items) if all(known(v) for v in items) else None), items
    for yr, I, B in ((py, PI, PB), (cy, CI, CB)):
        rules = [
            ("매출총손익 = 매출액 − 매출원가", 66, lambda: num(S(I, 1)) - num(S(I, 35)), [S(I, 1), S(I, 35), S(I, 66)]),
            ("영업손익 = 매출총손익 − 판매비와관리비", 129, lambda: num(S(I, 66)) - num(S(I, 67)), [S(I, 66), S(I, 67), S(I, 129)]),
            ("법인세차감전손익 = 영업손익 + 영업외수익 − 영업외비용", 217,
             lambda: num(S(I, 129)) + num(S(I, 130)) - num(S(I, 179)), [S(I, 129), S(I, 130), S(I, 179), S(I, 217)]),
            ("당기순손익 = 법인세차감전손익 − 법인세비용", 219, lambda: num(S(I, 217)) - num(S(I, 218)), [S(I, 217), S(I, 218), S(I, 219)]),
            ("자산총계 = 유동자산 + 비유동자산", 228, lambda: num(S(B, 1)) + num(S(B, 80)), [S(B, 1), S(B, 80), S(B, 228)]),
            ("부채총계 = 유동부채 + 비유동부채", 333, lambda: num(S(B, 229)) + num(S(B, 284)), [S(B, 229), S(B, 284), S(B, 333)]),
        ]
        sga_sum, sga_items_ = lvl2_sum(I, 67, 129)
        if sga_items_:
            rules.append(("판매비와관리비 = 세부 항목 합계", 67, lambda s=sga_sum: s, [S(I, 67)] + sga_items_))
        for title, code, fn, ins in rules:
            tgt = S(I if code < 228 else B, code)
            if not all(known(v) for v in ins) or (code == 67 and sga_sum is None):
                finding(f"R17-{yr}-{code}", f"{yr} {title}", None, "입력값 확인 필요로 검산 보류", evs(*ins), kind="보류")
                continue
            exp = fn()
            ok_ = exp == num(tgt)
            finding(f"R17-{yr}-{code}", f"{yr} {title}", ok_,
                    f"계산 {won(exp)} / 기재 {won(num(tgt))}" + ("" if ok_ else f" (차이 {signed(num(tgt) - exp)}) — 서식 안 숫자가 서로 맞지 않음. 어느 값이 틀렸는지 판단하지 않고 기재값 유지"),
                    evs(*ins), kind="대사" if ok_ else "서식 내 불일치")

    major = {228: "자산총계", 333: "부채총계", 382: "자본총계", 334: "자본금", 1: "매출액", 66: "매출총손익",
             67: "판매비와관리비", 129: "영업손익", 130: "영업외수익", 179: "영업외비용", 217: "법인세비용차감전손익",
             218: "법인세비용", 219: "당기순손익"}
    diff_notes: dict[tuple[str, int], str] = {}
    for yr, X in ((py, P), (cy, C)):
        bad, held, e_ = [], [], []
        for code, name in major.items():
            std = (X["bs"] if code >= 228 else X["is"]).get(code)
            fs = X["closing"]["재무상태표" if code >= 228 else "손익계산서"].get(code)
            if fs is None:
                continue
            if not known(std) or not known(fs):
                if not (std is not None and std.blank and fs.blank):
                    held.append(name)
                continue
            if num(std) != num(fs):
                d = num(fs) - num(std)
                bad.append(f"{name} 표준 {won(num(std))} / 결산서 {won(num(fs))} (차이 {signed(d)})")
                e_ += evs(std, fs)
                diff_notes[(yr, code)] = f"결산서 {won(num(fs))}원과 {signed(d)}원 차이"
        detail = ("대분류 일치" if not bad else "; ".join(bad) + " → 같은 항목(같은 범위)의 값이 서로 다름. 보고서는 표준서식 값 사용(Q-012), 결산서 값으로 덮어쓰지 않음")
        if held:
            detail += f" · 비교 보류(확인 필요 값): {', '.join(held)}"
        finding(f"R12-{yr}", f"{yr} 표준재무제표 = 결산서 (대분류)", not bad, detail, e_, ["Q-012"],
                kind="서식 간 차이" if bad else "대사")
    bad = []
    for code, name in major.items():
        std = (PB if code >= 228 else PI).get(code)
        fs = C["closing"]["재무상태표" if code >= 228 else "손익계산서"].get(-code)
        if fs is None or not known(fs) or not known(std):
            continue
        if num(std) != num(fs):
            bad.append(f"{name} {won(num(std))}/{won(num(fs))}")
    finding("R06", f"{py} 표준재무제표 = {cy} 결산서 전기열", not bad,
            "확인 가능한 항목 일치" if not bad else "차이: " + ", ".join(bad), [], kind="서식 간 차이" if bad else "대사")

    # ── II-(3) 주요 지표 비교 (억원) ──
    def eok(v: Value) -> Value:
        return calc(v.key + ".eok", v.label + " (억원)", lambda x: round(x / 1e8, 2), [v], f"{v.label} ÷ 100,000,000 (소수 둘째 자리 반올림, A-002)")

    def debt_ratio(B, yr):
        liab, eq = S(B, 333), S(B, 382)
        if known(eq) and num(eq) <= 0:
            return Value(f"r.debt.{yr}", "부채비율", text="자본잠식", status=Status.DERIVED, inputs=[liab.key, eq.key],
                         formula="자본총계 ≤ 0 → 부채비율 대신 자본잠식 표시",
                         note=f"계산상 {p1(pct(num(liab), num(eq)))}" if known(liab) else None)
        v = ratio(f"r.debt.{yr}", "부채비율", liab, eq, "부채총계 ÷ 자본총계 × 100")
        cap = S(B, 334)
        if v.amount is not None and known(cap) and num(eq) < num(cap):
            v.note = "부분 자본잠식(자본총계 < 자본금)"
        return v

    dr_p, dr_c = debt_ratio(PB, py), debt_ratio(CB, cy)
    key_rows = []
    for label, code in (("매출액", 1), ("영업이익", 129), ("당기순이익", 219)):
        pv, cv = S(PI, code), S(CI, code)
        d = calc(f"d.{code}", f"{label} 증감", lambda a, b_: b_ - a, [pv, cv], f"{cy} − {py}")
        row = {"label": label, "prior": eok(pv), "cur": eok(cv), "diff": eok(d) if d.status in OK else d,
               "prior_won": pv, "cur_won": cv, "diff_won": d}
        for v in row.values():
            if isinstance(v, Value):
                L.use("II-주요지표", v)
        key_rows.append(row)
    dd = calc("d.debt", "부채비율 증감", lambda a, b_: b_ - a, [dr_p, dr_c], "당기 − 전기 (%p)") if (dr_p.amount is not None and dr_c.amount is not None) else \
        Value("d.debt", "부채비율 증감", text="비교 불가" if (dr_p.text == "자본잠식" or dr_c.text == "자본잠식") else None,
              status=Status.DERIVED if (dr_p.text == "자본잠식" or dr_c.text == "자본잠식") else Status.NEEDS_CHECK,
              flag=None if (dr_p.text == "자본잠식" or dr_c.text == "자본잠식") else "계산보류", note="자본잠식 연도 포함" if (dr_p.text == "자본잠식" or dr_c.text == "자본잠식") else "보류: 부채비율 확인 필요")
    for v in (dr_p, dr_c, dd):
        L.use("II-주요지표", v)
    debt_row = {"label": "부채비율", "prior": dr_p, "cur": dr_c, "diff": dd}

    # ── II-(4) 요약 손익 비교 ──
    def opnet(I, yr):
        return calc(f"opnet.{yr}", "영업외손익", lambda a, b_: a - b_, [S(I, 130), S(I, 179)], "영업외수익 − 영업외비용")
    pl_items = [("매출액", lambda I, y: S(I, 1)), ("매출총이익", lambda I, y: S(I, 66)), ("영업손익", lambda I, y: S(I, 129)),
                ("영업외손익", opnet), ("법인세비용차감전손익", lambda I, y: S(I, 217)),
                ("법인세비용", lambda I, y: S(I, 218)), ("당기순이익", lambda I, y: S(I, 219))]
    pl_rows = []
    for label, f in pl_items:
        pv, cv = f(PI, py), f(CI, cy)
        d = calc(f"pl.d.{label}", f"{label} 증감액", lambda a, b_: b_ - a, [pv, cv], f"{cy} − {py}")
        r = calc(f"pl.r.{label}", f"{label} 증감률", lambda a, b_: (b_ - a) / abs(a) * 100 if a else None, [pv, cv],
                 f"({cy} − {py}) ÷ |{py}| × 100")
        for v in (pv, cv, d, r):
            L.use("II-요약손익", v)
        turn = ""
        if known(pv) and known(cv) and num(pv) is not None and num(cv) is not None:
            if num(pv) < 0 <= num(cv):
                turn = "흑자전환"
            elif num(pv) >= 0 > num(cv):
                turn = "적자전환"
        pl_rows.append({"label": label, "prior": pv, "cur": cv, "diff": d, "rate": r, "turn": turn})

    # ── II-(5) 판관비 주요 항목 ──
    def sga_items(I):
        return {c: v for c, v in I.items() if 67 < c < 129 and label_level(nospace(v.label)) == 2 and not v.blank}
    ip, ic = sga_items(PI), sga_items(CI)
    # 금액 미확인 = 값은 있으나 가림·글자 등으로 확정 불가. 한 해에 계정이 없는 것은 미확인이 아님(빈칸 확정)
    unknown_sga = sorted({c for c in set(ip) | set(ic) if (c in ic and not known(ic[c])) or (c in ip and not known(ip[c]))})
    # Q-4-01(담당자 답변): 당기 금액 상위 5개 고정 + 나머지는 '기타(상위 5개 외 합계)'
    TOP_N = int(firm.get("sga_top_n", 5))
    ranked = sorted([c for c in ic if known(ic[c]) and num(ic[c])], key=lambda c: -num(ic[c]))[:TOP_N]
    rev_p, rev_c = S(PI, 1), S(CI, 1)
    sga_rows = []

    def sga_row(code, label, pv, cv, key):
        d = calc(f"sga.d.{key}", f"{label} 증감", lambda a, b_: b_ - a, [pv, cv], f"{cy} − {py}")
        r = calc(f"sga.r.{key}", f"{label} 증감률", lambda a, b_: (b_ - a) / abs(a) * 100 if a else None, [pv, cv], "증감 ÷ |전기| × 100")
        sh_c = ratio(f"sga.s.{cy}.{key}", f"{label} 매출 대비({cy})", cv, rev_c, "항목 ÷ 매출액 × 100")
        sh_p = ratio(f"sga.s.{py}.{key}", f"{label} 매출 대비({py})", pv, rev_p, "항목 ÷ 매출액 × 100")
        for v in (cv, pv, d, r, sh_c, sh_p):
            L.use("II-판관비", v)
        return {"code": code, "label": label, "prior": pv, "cur": cv, "diff": d, "rate": r, "share_c": sh_c, "share_p": sh_p}

    for c in ranked:
        cv, pv = ic[c], ip.get(c) or Value(f"{py}:is.{c}", ic[c].label, text="공란", evidence=[])
        lab = cv.label.split(".", 1)[-1].strip()
        if c == 68:  # 표준손익계산서 '1.급여'(임원·직원 급여·상여·잡급 합계) = 판관비 급여 총액
            lab = "급여 총액"
        sga_rows.append(sga_row(c, lab, pv, cv, c))
    if ranked:
        top_c = [ic[c] for c in ranked]
        top_p = [ip[c] for c in ranked if c in ip]
        etc_c = calc(f"{cy}:sga.etc", f"기타 (상위 {TOP_N}개 외 합계) {cy}", lambda t, *xs: t - sum(xs), [sga_c_tot := S(CI, 67)] + top_c,
                     f"판매비와관리비 합계(코드67) − 상위 {TOP_N}개 계정")
        etc_p = calc(f"{py}:sga.etc", f"기타 (상위 {TOP_N}개 외 합계) {py}", lambda t, *xs: t - sum(xs), [S(PI, 67)] + top_p,
                     f"판매비와관리비 합계(코드67) − 같은 {TOP_N}개 계정의 전기 금액")
        sga_rows.append(dict(sga_row("", f"기타 (상위 {TOP_N}개 외 합계)", etc_p, etc_c, "etc"), is_etc=True))
        tot_row = sga_row(67, "판매비와관리비 합계", S(PI, 67), S(CI, 67), "tot")
        tot_row["is_total"] = True
        sga_rows.append(tot_row)
    # 특이사항(Q-4-01): 전기에만 있던 계정(소멸) · 당기에 새로 생긴 계정(신규)
    sga_gone = [ip[c] for c in sorted(ip) if c not in ic and known(ip[c]) and num(ip[c])]
    sga_new = [ic[c] for c in sorted(ic) if c not in ip and known(ic[c]) and num(ic[c])]
    for v in sga_gone + sga_new:
        L.use("VIII", v)
    sga_unknown = [{"code": c, "label": (ic.get(c) or ip.get(c)).label.split(".", 1)[-1], "cur": ic.get(c), "prior": ip.get(c)} for c in unknown_sga]
    for u in sga_unknown:
        L.use("II-판관비", u["cur"]), L.use("II-판관비", u["prior"])

    # ════════════ III. 법인세 신고상황 ════════════
    PT, CT = P["tc"], C["tc"]
    tax_lines = [
        ("수입금액(신고서)", "ret.revenue", None), ("결산서상 당기순손익", "tc.01", None), ("(+) 익금산입", "tc.02", None),
        ("(−) 손금산입", "tc.03", None), ("차가감 소득금액", "tc.04", None), ("(+) 기부금한도초과액", "tc.05", None),
        ("각사업연도 소득금액", "tc.06", None),
        ("(−) 이월결손금", "tc.07", "Q-003"), ("(−) 비과세소득", "tc.08", None), ("(−) 소득공제", "tc.09", None),
        ("과세표준", "tc.10", None), ("세율(%)", "tc.11", None), ("산출세액", "tc.12", None),
        ("최저한세 적용대상 공제감면세액", "tc.17", None), ("최저한세 적용제외 공제감면세액", "tc.19", None),
        ("가산세액", "tc.20", None), ("총부담세액(가감계)", "tc.21", None), ("기납부세액 합계", "tc.28", None),
        ("차감납부할세액", "tc.30", None),
    ]

    def tget(X, k):
        return X["basic"][k] if k.startswith("ret.") else X["tc"][k]

    tax_rows = []
    for label, k, q in tax_lines:
        pv, cv = L.use("III", tget(P, k)), L.use("III", tget(C, k))
        if k == "tc.05" and pv.blank and cv.blank:
            continue
        tax_rows.append({"label": label, "prior": pv, "cur": cv, "q": q,
                         "src": f"p{ev0(cv).page if ev0(cv) else '–'} / p{ev0(pv).page if ev0(pv) else '–'}",
                         "src_pc": f"p{ev0(pv).page if ev0(pv) else '–'} / p{ev0(cv).page if ev0(cv) else '–'}"})
    pl, cl = L.use("III", P["local"]["local.total"]), L.use("III", C["local"]["local.total"])
    tax_rows.append({"label": "법인지방소득세(총부담세액)", "prior": pl, "cur": cl, "q": None,
                     "src": f"p{ev0(cl).page if ev0(cl) else '서식 없음'} / {('p' + str(ev0(pl).page)) if ev0(pl) else '서식 없음'}",
                     "src_pc": f"{('p' + str(ev0(pl).page)) if ev0(pl) else '서식 없음'} / {('p' + str(ev0(cl).page)) if ev0(cl) else '서식 없음'}"})

    def ratios(X, I, yr):
        rev, inc, tb, tot = X["basic"]["ret.revenue"], X["tc"]["tc.06"], X["tc"]["tc.10"], X["tc"]["tc.21"]
        out = [ratio(f"r.income_rate.{yr}", "소득률", inc, rev, "각사업연도소득금액 ÷ 수입금액 × 100"),
               ratio(f"r.gross.{yr}", "매출총이익률", S(I, 66), S(I, 1), "매출총손익 ÷ 매출액 × 100 (표준손익계산서)"),
               ratio(f"r.op.{yr}", "영업이익률", S(I, 129), S(I, 1), "영업손익 ÷ 매출액 × 100 (표준손익계산서)")]
        for key, label, base in ((f"r.eff_tb.{yr}", "실효세율(과세표준 기준)", tb), (f"r.eff_inc.{yr}", "실효세율(소득금액 기준)", inc)):
            if known(base) and (num(base) or 0) <= 0:
                out.append(Value(key, label, text="해당 없음", status=Status.DERIVED, inputs=[base.key],
                                 formula="총부담세액 ÷ 기준금액 × 100", note="기준금액이 0 이하(결손)"))
            else:
                out.append(ratio(key, label, tot, base, "총부담세액 ÷ 기준금액 × 100 (공란은 0)"))
        return out

    ratio_rows = list(zip(ratios(P, PI, py), ratios(C, CI, cy)))
    for a_, b_ in ratio_rows:
        L.use("III", a_), L.use("III", b_)

    rev_scope = {}
    for yr, X, I in ((py, P, PI), (cy, C, CI)):
        r, s1 = X["basic"]["ret.revenue"], S(I, 1)
        if known(r) and known(s1):
            if num(r) == num(s1):
                finding(f"R01-{yr}", f"{yr} 신고서 수입금액 = 표준손익계산서 매출액", True, f"{won(num(r))} / {won(num(s1))}", evs(r, s1))
            else:
                sc = _revenue_scope(X["doc"])
                extra = sum(a for _, a in sc["items"]) if sc else 0
                explained = sc is not None and num(s1) + extra == num(r)
                rev_scope[yr] = sc
                finding(f"R01-{yr}", f"{yr} 신고서 수입금액 vs 표준손익계산서 매출액", None if explained else False,
                        (f"수입금액 {won(num(r))} = 매출액 {won(num(s1))} + " + " + ".join(f"{n} {won(a)}" for n, a in sc["items"])
                         + f" (수입금액조정명세서 p{sc['page']}). 범위가 다른 값으로 차이가 설명됨 — 보고서 매출액은 표준손익계산서 값") if explained
                        else f"수입금액 {won(num(r))} / 매출액 {won(num(s1))}: 차이 {signed(num(r) - num(s1))} 설명 자료 없음",
                        evs(r, s1), kind="참고" if explained else "서식 간 차이")
        else:
            finding(f"R01-{yr}", f"{yr} 신고서 수입금액 vs 표준손익계산서 매출액", None,
                    f"비교 보류 — 수입금액: {r.flag or ('확정' if known(r) else r.status.value)}, 매출액: {s1.flag or ('확정' if known(s1) else s1.status.value)}",
                    evs(r, s1), kind="보류")
        t1, s219 = X["tc"]["tc.01"], S(I, 219)
        if known(t1) and known(s219):
            finding(f"R02-{yr}", f"{yr} 세액조정계산서 코드01 = 표준 당기순손익", num(t1) == num(s219),
                    f"{won(num(t1))} / {won(num(s219))}", evs(t1, s219))
        t = X["tc"]
        if all(known(t[k]) for k in ("tc.01", "tc.02", "tc.03", "tc.04")):
            finding(f"R03-{yr}", f"{yr} 코드01 + 02 − 03 = 04", num(t["tc.01"]) + num(t["tc.02"]) - num(t["tc.03"]) == num(t["tc.04"]),
                    f"{won(num(t['tc.01']))} + {won(num(t['tc.02']))} − {won(num(t['tc.03']))} = {won(num(t['tc.04']))}", evs(t["tc.04"]))
        if all(known(t[k]) for k in ("tc.12", "tc.17", "tc.19", "tc.20", "tc.21")):
            lhs = num(t["tc.12"]) - num(t["tc.17"]) - num(t["tc.19"]) + num(t["tc.20"])
            finding(f"R08-{yr}", f"{yr} 산출세액 − 공제감면 + 가산세 = 총부담세액", lhs == num(t["tc.21"]),
                    f"{won(lhs)} / {won(num(t['tc.21']))}", evs(t["tc.21"]))
    pre = C["sch"].get("선납세금명세서")
    r15 = None
    if pre and pre["total"].get("당기증가") and C["tc"]["tc.28"].blank:
        inc = pre["total"]["당기증가"]
        L.use("III", inc)
        finding("R15", f"{cy} 선납세금 증가 vs 세액조정계산서 기납부세액", False,
                f"결산부속명세서 선납세금 당기증가 {won(inc.amount)}원(당기감소 {won(amt(pre['total'].get('당기감소')))}원)이 있으나 "
                f"세액조정계산서 기납부세액(코드22~28) 칸은 공란입니다. 원인은 자료만으로 판단할 수 없어 표시만 합니다.",
                evs(inc, C["tc"]["tc.28"]), kind="확인 필요")
        r15 = findings[-1]

    # ════════════ IV. 이월결손금 ════════════
    loss_rows = []
    prior_tb, prior_ni = PT["tc.10"], S(PI, 219)
    prior_loss_occ = P["losses"][0]["cols"].get("발생액 계") if P["losses"] else None
    cur_used = CT["tc.07"]
    loss_conflict = (known(prior_tb) and num(prior_tb) is not None and num(prior_tb) < 0 and known(cur_used)
                     and num(cur_used) and num(cur_used) != abs(num(prior_tb)))
    for r in C["losses"]:
        cols = r["cols"]
        for v in cols.values():
            if loss_conflict:
                v.status, v.show_amount, v.questions = Status.CONFLICT, True, ["Q-003"]
            L.use("IV", v)
        loss_rows.append({"year": r["year"], "occur": cols.get("발생액 계"), "used": cols.get("당기공제액"),
                          "dec": cols.get("감소 계"), "bal": cols.get("잔액 계"), "page": r["page"]})
    diff = None
    if loss_conflict:
        used_amt, ptb_amt = num(cur_used), num(prior_tb)
        cur_used.status, cur_used.show_amount, cur_used.questions = Status.CONFLICT, True, ["Q-003"]
        cur_used.note = "이월결손금 차이 확인 중(Q-003), 신고서 값 그대로 표시"
        diff = Value("loss.diff", "차이", amount=used_amt - abs(ptb_amt), status=Status.DERIVED,
                     inputs=[prior_tb.key, cur_used.key], formula=f"{cy} 이월결손금 공제액 − |{py} 과세표준|", questions=["Q-003"])
        L.use("IV", diff)
        for v in (prior_tb, prior_ni, prior_loss_occ, cur_used):
            L.use("IV", v)
        finding("R07", f"{py} 결손(과세표준) = 결손금 발생액·{cy} 이월결손금 공제액", False,
                f"{won(abs(ptb_amt))} vs {won(used_amt)} → 차이 {won(diff.amount)}원. 원인 확인 중(Q-003), 숫자 보정 안 함",
                evs(prior_tb, prior_loss_occ, cur_used), ["Q-003"], kind="충돌")
    loss_compare = [
        {"label": f"{py} 과세표준(결손)", "v": prior_tb},
        {"label": f"{py} 표준손익계산서 당기순손익", "v": prior_ni},
        {"label": f"{py} 자본금과적립금조정명세서(갑) 결손금 발생액", "v": prior_loss_occ},
        {"label": f"{cy} 세액조정계산서 이월결손금(코드07)", "v": cur_used},
    ] if loss_conflict else []
    loss_none = not C["losses"] and CT["tc.07"].blank

    # ════════════ V. 소득금액조정 ════════════
    adj_years = []
    for yr, X, T in ((cy, C, CT), (py, P, PT)):
        a = X["adj"]
        for side in ("add", "deduct"):
            for it in a[side]:
                L.use("V", it["amount"])
            if a.get(f"{side}_total"):
                L.use("V", a[f"{side}_total"])
        note = None
        if not a["present"]:
            note = Value(f"adj.{yr}", f"{yr} 소득금액조정", status=Status.NEEDS_CHECK, questions=["Q-010-2"], flag="서식없음",
                         note=f"{yr} 세무조정계산서에 소득금액조정합계표 서식 없음(확인 중)")
            L.use("V", note), L.use("V", T["tc.02"]), L.use("V", T["tc.03"])
        elif a.get("add_total") and a["add_total"].amount is not None and known(T["tc.02"]):
            finding(f"R10-{yr}", f"{yr} 소득금액조정합계표 익금산입 합계 = 세액조정계산서 코드02",
                    a["add_total"].amount == num(T["tc.02"]), f"{won(a['add_total'].amount)} / {won(num(T['tc.02']))}",
                    evs(a["add_total"], T["tc.02"]))
        adj_years.append({"year": yr, "adj": a, "note": note, "T": T})
    ent = []
    for yr, X in ((cy, C), (py, P)):
        e = X["ent"]
        if e.get("amount"):
            L.use("V", e["amount"])
            if e.get("excess"):
                L.use("V", e["excess"])
            ent.append({"year": yr, "amount": e["amount"], "excess": e.get("excess")})

    # ════════════ VI. 공제·감면 ════════════
    cr_years = []
    for yr, X, T in ((cy, C, CT), (py, P, PT)):
        cr = X["credits"]
        for r in cr["rows"]:
            L.use("VI", r["amount"])
            if r.get("rate"):
                L.use("VI", r["rate"])
        note = None
        if cr["total"]:
            L.use("VI", cr["total"])
            if known(T["tc.17"]) and known(T["tc.19"]):
                lhs = num(T["tc.17"]) + num(T["tc.19"])
                finding(f"R11-{yr}", f"{yr} 공제감면합계표 총계 = 세액조정계산서 코드17 + 19", cr["total"].amount == lhs,
                        f"{won(cr['total'].amount)} / {won(lhs)}", evs(cr["total"], T["tc.17"]))
        applied_tc = None
        if known(T["tc.17"]) and known(T["tc.19"]):
            applied_tc = Value(f"cr.applied.{yr}", f"{yr} 적용 공제감면세액(세액조정계산서)", amount=num(T["tc.17"]) + num(T["tc.19"]),
                               status=Status.DERIVED, inputs=[T["tc.17"].key, T["tc.19"].key],
                               formula="세액조정계산서 코드17(최저한세 적용대상) + 코드19(적용제외), 공란은 0")
            L.use("VI", applied_tc), L.use("VI", T["tc.17"]), L.use("VI", T["tc.19"])
        if not cr["present"]:
            if applied_tc is None:
                note = Value(f"cr.{yr}", f"{yr} 공제·감면", status=Status.NEEDS_CHECK, questions=["Q-010-2"], flag="서식없음",
                             note=f"{yr} 세무조정계산서에 공제감면세액합계표 서식 없음(확인 중)")
                L.use("VI", note)
        cr_years.append({"year": yr, "cr": cr, "note": note, "applied_tc": applied_tc, "T": T})

    # ════════════ VII. 요약 코멘트 ════════════
    comments = []

    def para(title, sentences, used: list, marks=()):
        used = [v for v in used if v is not None]
        for v in used:
            L.use("VII", v)
        srcs = []
        for v in used:
            for e in v.evidence:
                tag = f"{cy if cy in e.file else py} {e.form} p{e.page}"
                if tag not in srcs:
                    srcs.append(tag)
        comments.append({"title": title, "sentences": sentences, "sources": srcs, "marks": list(marks)})

    HOLD = "«확인 필요»"

    def amt_txt(v):
        return f"{won(num(v))}원" if known(v) else HOLD

    def change_sentence(name, pv, cv):
        if known(pv) and known(cv):
            a, b_ = num(pv), num(cv)
            if a < 0 <= b_:
                return f"{name}은 {won(a)}원에서 {won(b_)}원으로 흑자 전환했습니다."
            if a >= 0 > b_:
                return f"{name}은 {won(a)}원에서 {won(b_)}원으로 적자 전환했습니다."
            d = b_ - a
            return f"{name}은 {won(a)}원에서 {won(b_)}원으로 {won(abs(d))}원({p1(pct(d, abs(a)) if a else None)}) {'증가' if d >= 0 else '감소'}했습니다."
        miss = [f"{yr}년" for yr, v in ((py, pv), (cy, cv)) if not known(v)]
        return (f"{name}은 {py}년 {amt_txt(pv)}, {cy}년 {amt_txt(cv)}입니다. "
                f"{'·'.join(miss)} 값이 PDF에서 확인되지 않아 증감 비교는 보류했습니다.")

    op_p, op_c, ni_p, ni_c = S(PI, 129), S(CI, 129), S(PI, 219), S(CI, 219)
    opr_p = ratio(f"r.op.{py}", "영업이익률", op_p, rev_p, "영업손익 ÷ 매출액 × 100")
    opr_c = ratio(f"r.op.{cy}", "영업이익률", op_c, rev_c, "영업손익 ÷ 매출액 × 100")
    sent = [change_sentence("매출액", rev_p, rev_c), change_sentence("영업손익", op_p, op_c)]
    if known(opr_p) and known(opr_c) and opr_p.amount is not None and opr_c.amount is not None:
        sent.append(f"영업이익률은 {p1(opr_p.amount)}에서 {p1(opr_c.amount)}가 되었습니다.")
    else:
        sent.append(f"영업이익률은 {py}년 {p1(opr_p.amount) if opr_p.amount is not None else HOLD}, "
                    f"{cy}년 {p1(opr_c.amount) if opr_c.amount is not None else HOLD}입니다(매출액 확인 필요 시 보류).")
    sent.append(change_sentence("당기순손익", ni_p, ni_c))
    para("1. 매출 및 수익성", sent, [rev_p, rev_c, op_p, op_c, ni_p, ni_c, opr_p, opr_c])

    sga_p, sga_c = S(PI, 67), S(CI, 67)
    sent = [change_sentence("판매비와관리비", sga_p, sga_c)]
    sr_p, sr_c = ratio(f"r.sga.{py}", "판관비율", sga_p, rev_p, "판관비 ÷ 매출액"), ratio(f"r.sga.{cy}", "판관비율", sga_c, rev_c, "판관비 ÷ 매출액")
    sent.append(f"매출액 대비 판관비 비율은 {py}년 {p1(sr_p.amount) if sr_p.amount is not None else HOLD}, "
                f"{cy}년 {p1(sr_c.amount) if sr_c.amount is not None else HOLD}입니다.")
    allc = sorted(set(ip) | set(ic))
    deltas = [(c, num(ic[c]) - num(ip[c])) for c in allc if known(ic.get(c)) and known(ip.get(c))]
    inc_ = sorted([d for d in deltas if d[1] > 0], key=lambda d: -d[1])[:2]
    dec_ = sorted([d for d in deltas if d[1] < 0], key=lambda d: d[1])[:2]

    def nm(c):
        return (ic.get(c) or ip.get(c)).label.split(".", 1)[-1]
    if ranked:
        sent.append("당기 판매비와관리비 중 금액이 큰 항목은 " + ", ".join(f"{nm(c)} {won(num(ic[c]))}원" for c in ranked[:3]) + "입니다.ᴬ")
    if inc_:
        sent.append("전기 대비 많이 늘어난 항목은 " + ", ".join(f"{nm(c)}({signed(d)}원)" for c, d in inc_) + "입니다.")
    if dec_:
        sent.append("줄어든 항목은 " + ", ".join(f"{nm(c)}({signed(d)}원)" for c, d in dec_) + "입니다.")
    if sga_unknown:
        sent.append("다음 항목은 PDF에서 금액이 확인되지 않아 비교에서 제외했습니다: " +
                    ", ".join(f"{u['label']}" for u in sga_unknown) + ".")
    para("2. 판매비와관리비", sent, [sga_p, sga_c, sr_p, sr_c] + [ic[c] for c in ranked[:3]] +
         [x for c, _ in inc_ + dec_ for x in (ic.get(c), ip.get(c))], ["A-003"])

    def nonop(I, lo, hi):
        return [v for c, v in sorted(I.items()) if lo < c < hi and label_level(nospace(v.label)) == 2 and not v.blank]

    def lst(items):
        return ", ".join(f"{v.label.split('.', 1)[-1]} {amt_txt(v)}" for v in items) or "없음"
    noi_p, noi_c, noe_p, noe_c = S(PI, 130), S(CI, 130), S(PI, 179), S(CI, 179)
    ni_items_c, ne_items_c = nonop(CI, 130, 179), nonop(CI, 179, 217)
    sent = [f"당기 영업외수익은 {amt_txt(noi_c)}({lst(ni_items_c)}), 영업외비용은 {amt_txt(noe_c)}({lst(ne_items_c)})입니다. "
            f"전기에는 영업외수익 {amt_txt(noi_p)}, 영업외비용 {amt_txt(noe_p)}이었습니다."]
    para("3. 영업외손익", sent, [noi_p, noi_c, noe_p, noe_c] + ni_items_c + ne_items_c)

    def bs_items(B):
        return {c: v for c, v in B.items() if c < 334 and c not in (228, 333) and label_level(nospace(v.label)) == 2 and known(v) and not v.blank}
    bp, bc = bs_items(PB), bs_items(CB)
    ch = sorted(((c, num(bc.get(c)) if c in bc else 0) for c in set(bp) | set(bc)), key=lambda x: 0)
    ch = sorted(((c, (num(bc[c]) if c in bc else 0) - (num(bp[c]) if c in bp else 0)) for c in set(bp) | set(bc)),
                key=lambda x: -abs(x[1]))[:4]
    eq_p, eq_c = S(PB, 382), S(CB, 382)
    sent = ["재무상태표에서 변동이 큰 항목은 " + ", ".join(
        f"{(bc.get(c) or bp.get(c)).label.split('.', 1)[-1].strip()}({won(num(bp.get(c)) if c in bp else 0)}원 → {won(num(bc.get(c)) if c in bc else 0)}원)"
        for c, _ in ch) + "입니다.", change_sentence("자본총계", eq_p, eq_c)]
    para("4. 전년 대비 주요 변동", sent, [x for c, _ in ch for x in (bp.get(c), bc.get(c))] + [eq_p, eq_c])

    def drtxt(v, B):
        if v.text == "자본잠식":
            return f"자본잠식(자본총계 {won(num(S(B, 382)))}원)"
        if v.amount is None:
            return HOLD
        return p1(v.amount) + (f"({v.note})" if v.note else "")
    sent = [f"부채비율은 {py}년 {drtxt(dr_p, PB)}, {cy}년 {drtxt(dr_c, CB)}입니다."]
    for y in sorted(cr_years, key=lambda y: y["year"]):
        applied = [r for r in y["cr"]["rows"] if r["amount"].amount]
        unapplied = [r for r in y["cr"]["rows"] if not r["amount"].amount]
        if applied:
            sent.append(f"{y['year']}년에는 " + ", ".join(
                f"{r['name']}({r['rate'].text + ', ' if r.get('rate') else ''}{won(r['amount'].amount)}원)" for r in applied) + "이 반영되었습니다.")
        elif y["applied_tc"] is not None and y["applied_tc"].amount == 0:
            sent.append(f"{y['year']}년에 세액에 적용된 공제·감면세액은 없습니다(세액조정계산서 코드17·19 공란).")
        elif y["note"]:
            sent.append(f"{y['year']}년 공제·감면은 해당 서식이 없어 «확인 필요»입니다(Q-010-2).")
        for r in unapplied:
            occ = r["vals"].get("당기발생액") or r["vals"].get("전기이월액")
            if occ is not None:
                sent.append(f"{y['year']}년 {r['name']}은 {('당기발생액' if '당기발생액' in r['vals'] else '전기이월액')} {won(occ.amount)}원이 "
                            f"있으나 {r['applied_col']} 칸은 비어 있어 해당 연도 세액에서 공제되지 않았습니다.")
    if (cy, 333) in diff_notes or (cy, 382) in diff_notes:
        sent.append(f"{cy}년 부채·자본총계는 표준재무상태표 기준이며, 결산서 재무상태표와 차이가 있습니다(부록 B 경고).")
    para("5. 재무비율 및 공제·감면", sent, [S(PB, 333), S(PB, 382), S(CB, 333), S(CB, 382), dr_p, dr_c] +
         [r["amount"] for y in cr_years for r in y["cr"]["rows"]] + [y["applied_tc"] for y in cr_years if y["applied_tc"]])

    # ════════════ VIII. 재무제표 주요 특이사항 ════════════
    ar = {}
    for yr, X, B in ((py, P, PB), (cy, C, CB)):
        sch = X["sch"].get("외상매출금명세서")
        if not sch:
            ar[yr] = None
            continue
        rows = sch["rows"]
        bal = [r for r in rows if r["vals"].get("금액") and r["vals"]["금액"].amount]
        tot = sch["total"].get("금액")
        top = max(bal, key=lambda r: r["vals"]["금액"].amount) if bal else None
        newc = [r for r in rows if r["vals"].get("당기증가") and r["vals"]["당기증가"].amount]
        inc_tot = sch["total"].get("당기증가")
        top_inc = max(newc, key=lambda r: r["vals"]["당기증가"].amount) if newc else None
        for r in rows:
            for v in r["vals"].values():
                L.use("VIII", v)
        if tot and known(tot):
            L.use("VIII", tot)
            std = S(B, 10)
            if known(std):
                finding(f"R09-AR-{yr}", f"{yr} 외상매출금명세서 합계 = 표준재무상태표 외상매출금(코드10)", num(tot) == num(std),
                        f"{won(num(tot))} / {won(num(std))}", evs(tot, std))
        masked_rows = [r for r in rows if any(v.flag == "가림" for v in r["vals"].values())]
        if masked_rows or not (tot and known(tot)):
            ar[yr] = {"page": sch["page"], "held": True, "n_known": len(bal), "n_masked": len(masked_rows),
                      "masked_codes": [r["partner_code"] for r in masked_rows], "tot": tot,
                      "n_bal": None, "n_inc": None, "top": None, "top_share": None, "top_inc": None, "top_inc_share": None}
            continue
        ar[yr] = {"page": sch["page"], "held": False, "n_bal": len(bal), "n_inc": len(newc) if inc_tot else None,
                  "top": top, "top_share": pct(top["vals"]["금액"].amount, tot.amount) if top and tot and tot.amount else None,
                  "top_inc": top_inc,
                  "top_inc_share": pct(top_inc["vals"]["당기증가"].amount, inc_tot.amount) if top_inc and inc_tot and inc_tot.amount else None}
    rec_p = ratio(f"r.ar.{py}", "매출채권 ÷ 매출액", S(PB, 9), rev_p, "표준 매출채권(코드9) ÷ 표준 매출액(코드1) × 100")
    rec_c = ratio(f"r.ar.{cy}", "매출채권 ÷ 매출액", S(CB, 9), rev_c, "표준 매출채권(코드9) ÷ 표준 매출액(코드1) × 100")
    for v in (rec_p, rec_c, S(PB, 9), S(CB, 9)):
        L.use("VIII", v)

    # 가수금·대표자 관련 차입금 (Q-013-1: 부속명세서 거래처코드별)
    # 4차: 표준재무상태표에서 가수금이 주주·임원·직원차입금(236·292)이 아닌 다른 계정(예: 기타유동부채)으로 분류될 수 있다.
    #      계정명만으로 가수금이라 판단하지 않고, 명세서 합계와 같은 금액의 부채 계정을 '금액 일치 계정'으로 표시한다.
    sus = {}
    SUS_CODES = (236, 292)
    for yr, X, B in ((py, P, PB), (cy, C, CB)):
        schs = {k: v for k, v in X["sch"].items() if ("가수금" in k or "주.임.종" in k or "주임종" in k)}
        stds = [S(B, c) for c in SUS_CODES if c in B and not B[c].blank]
        for v in stds:
            L.use("VIII", v)
        rows, matches = [], []
        for name, sch in schs.items():
            for r in sch["rows"]:
                r["schedule"] = name
                rows.append(r)
                for v in r["vals"].values():
                    L.use("VIII", v)
            tot = sch["total"].get("금액")
            if tot is None or not known(tot):
                continue
            L.use("VIII", tot)
            code = 292 if ("장기" in name) else 236
            std = B.get(code)
            if std is not None and known(std) and not std.blank:
                finding(f"R14-{yr}-{code}", f"{yr} {name} 합계 = 표준 코드{code}", num(tot) == num(std),
                        f"{won(num(tot))} / {won(num(std))}", evs(tot, std), ["Q-013-1"],
                        kind="대사" if num(tot) == num(std) else "서식 간 차이")
                continue
            same = [v for c, v in sorted(B.items()) if 229 <= c < 333 and known(v) and not v.blank and num(v) == num(tot)
                    and label_level(nospace(v.label)) >= 2]
            if same:
                matches += [(name, v) for v in same]
                for v in same:
                    L.use("VIII", v)
                finding(f"R14-{yr}-{code}", f"{yr} {name} 합계 = 표준재무상태표 금액 일치 계정", True,
                        f"{won(num(tot))} = {same[0].label}(코드{same[0].key.split('.')[-1]}) {won(num(same[0]))} — "
                        f"표준 코드{code}는 공란, 같은 금액이 다른 계정으로 분류됨(계정명만으로 판단하지 않고 금액 일치로 대응)",
                        evs(tot, same[0]), ["Q-013-1"])
            else:
                finding(f"R14-{yr}-{code}", f"{yr} {name} 합계 vs 표준재무상태표", False,
                        f"명세서 합계 {won(num(tot))}와 같은 금액의 부채 계정을 표준재무상태표에서 찾지 못함", evs(tot),
                        ["Q-013-1"], kind="서식 간 차이")
        sus[yr] = {"stds": stds, "rows": rows, "schedules": list(schs), "matches": matches}

    loan = {}
    for yr, X, B, I in ((py, P, PB, PI), (cy, C, CB, CI)):
        pages = X["doc"].pages_of("결산부속명세서")
        schs = {k: v for k, v in X["sch"].items() if "가지급금" in k or "대여금" in k}
        sch_rows = [dict(r, schedule=k) for k, v in schs.items() for r in v["rows"]]
        deemed = X.get("deemed", [])
        for d_ in deemed:
            L.use("VIII", d_["balance"])
        for r in sch_rows:
            for v in r["vals"].values():
                L.use("VIII", v)
        v27 = B.get(27)
        if v27 is not None:
            L.use("VIII", v27)
        # 표준재무상태표에서 같은 금액이 어느 계정에 있는지(계정명만으로 가지급금이라 판단하지 않음)
        total = sum(d_["balance"].amount for d_ in deemed) if deemed else None
        std_match = [v for c, v in sorted(B.items()) if c < 80 and known(v) and total and num(v) == total]
        for v in std_match:
            L.use("VIII", v)
        sch_total = sum(num(r["vals"]["금액"]) for r in sch_rows if known(r["vals"].get("금액"))) if sch_rows else None
        agree = None
        if deemed and sch_rows:
            agree = sch_total == total
            finding(f"R18-{yr}", f"{yr} 대여금 명세서 합계 = 인정이자조정명세서(을) 가지급금 잔액", agree,
                    f"{won(sch_total)} / {won(total)} (같은 거래처의 기말 잔액끼리 비교)", evs(*[d_["balance"] for d_ in deemed]),
                    kind="대사" if agree else "서식 간 차이")
        share = (Value(f"loan.share.{yr}", "가지급금 ÷ 매출액", amount=total / num(S(I, 1)) * 100, status=Status.DERIVED,
                       formula="인정이자조정명세서(을) 가지급금 잔액 ÷ 표준 매출액 × 100", inputs=[S(I, 1).key])
                 if total and known(S(I, 1)) and num(S(I, 1)) else None)
        if share:
            L.use("VIII", share)
        loan[yr] = {"has_sch": bool(schs) or bool(deemed), "std": v27, "deemed": deemed, "sch_rows": sch_rows,
                    "std_match": std_match, "total": total, "share": share, "agree": agree,
                    "none": not schs and not deemed and (v27 is None or v27.blank),
                    "pages": f"p{min(pages)}~p{max(pages)}" if pages else "없음"}

    cap_c = S(CB, 334)
    ec, ep = S(CB, 382), S(PB, 382)
    impaired_now = known(ec) and known(cap_c) and num(ec) < num(cap_c)
    impair_rate = (Value("impair.rate", "자본잠식률", amount=(num(cap_c) - num(ec)) / num(cap_c) * 100, status=Status.DERIVED,
                         formula="(자본금 − 자본총계) ÷ 자본금 × 100", inputs=[cap_c.key, ec.key]) if impaired_now else None)
    if impair_rate:
        L.use("VIII", impair_rate)
    impair = {"now": impaired_now, "rate": impair_rate, "prior_eq": ep, "cur_eq": ec, "cap": cap_c,
              "need": Value("impair.need", "자본잠식 해소 필요액", status=Status.NEEDS_CHECK, questions=["Q-005"],
                            flag="계산보류", note="해소 기준 미정(Q-005)") if impaired_now else None}
    L.use("VIII", cap_c)
    if impair["need"]:
        L.use("VIII", impair["need"])
    # 자본금: 표준 vs 결산서 vs 자본금명세서
    capsch = C["sch"].get("자본금명세서")
    if capsch and capsch["total"].get("금액") and known(cap_c):
        t = capsch["total"]["금액"]
        if num(t) != num(cap_c):
            L.use("VIII", t)
            fsv = C["closing"]["재무상태표"].get(334)
            finding(f"R16-{cy}", f"{cy} 자본금명세서 합계 = 표준재무상태표 자본금(코드334)", False,
                    f"자본금명세서 {won(num(t))} / 표준 {won(num(cap_c))} / 결산서 재무상태표 {won(num(fsv)) if known(fsv) else '–'} "
                    f"→ 같은 항목(자본금 기말 잔액) 값이 다름. 보고서는 표준서식 값 사용, 명세서 값으로 덮어쓰지 않음",
                    evs(t, cap_c, fsv), kind="서식 간 차이")

    # ════════════ 경고 목록 ════════════
    warnings = build_warnings(L, findings, py, cy)

    needs = []
    for sec, v in L.rows:
        if v.status in (Status.NEEDS_CHECK, Status.CONFLICT) and not v.show_amount:
            needs.append({"section": sec, "label": v.label, "status": v.status.value, "note": v.note or "",
                          "q": ", ".join(v.questions)})
    for f in findings:
        if f["ok"] is False:
            needs.append({"section": "대사 " + f["id"], "label": f["title"], "status": f["kind"], "note": f["detail"],
                          "q": ", ".join(f["questions"])})

    return {
        "firm": firm, "q": qcfg, "py": py, "cy": cy, "P": P, "C": C,
        "files": {"prior": P["doc"].name, "current": C["doc"].name},
        "basic_rows": basic_rows, "basic_note": basic_note,
        "bs_rows": bs_rows, "is_rows": is_rows,
        "key_rows": key_rows, "debt_row": debt_row, "pl_rows": pl_rows, "sga_rows": sga_rows, "sga_unknown": sga_unknown,
        "sga_gone": sga_gone, "sga_new": sga_new, "sga_top_n": TOP_N,
        "tax_rows": tax_rows, "ratio_rows": ratio_rows,
        "loss_rows": loss_rows, "loss_compare": loss_compare, "loss_diff": diff, "loss_none": loss_none,
        "adj_years": adj_years, "ent": ent, "PT": PT,
        "cr_years": cr_years, "credits": C["credits"],
        "comments": comments,
        "ar": ar, "rec": (rec_p, rec_c), "sus": sus, "loan": loan, "impair": impair,
        "findings": findings, "needs": needs, "ledger": L.rows, "warnings": warnings,
        "r15": r15, "diff_notes": diff_notes,
        "r12_ok": all(f["ok"] for f in findings if f["id"].startswith("R12")),
        "rev": (rev_p, rev_c), "sga_tot": (sga_p, sga_c),
    }


FLAG_TEXT = {
    "가림": ("회색 칸(숫자 없음)", "PDF에 실제로 보이는 내용 — 회색 처리된 칸(가림 도형 확인, 아래 숫자 텍스트 없음)"),
    "글자표기": (None, "PDF에 실제로 보이는 내용 — 금액 대신 글자가 인쇄됨"),
    "합계빈칸": ("빈칸", "PDF에 실제로 보이는 내용 — 합계 칸이 비어 있고 하위 항목이 가려짐"),
    "체크표시": ("체크 표시", "추출 한계 — 체크 표시는 글자가 아니어서 텍스트로 읽을 수 없음"),
    "서식없음": ("서식 없음", "자료 부족 — 해당 서식이 PDF에 없음"),
    "추출실패": ("–", "추출 문제 — 서식은 있으나 프로그램이 위치를 찾지 못함"),
    "계산보류": ("«확인 필요»", "계산 보류 — 의존하는 입력값이 확인 필요"),
}


def build_warnings(L: Ledger, findings: list[dict], py: str, cy: str) -> list[dict]:
    """경고를 '원인' 단위로 묶는다. 계산 보류는 원인 경고의 '영향'으로 붙이고, 명세서 가림 칸은 명세서·페이지 단위로 묶는다."""
    vals = {v.key: v for _, v in L.rows}

    def roots(v, depth=0):
        if depth > 6:
            return
        if v.flag == "계산보류":
            for k in v.inputs:
                iv = vals.get(k)
                if iv is not None and not known(iv):
                    yield from roots(iv, depth + 1)
        else:
            yield v.key

    impacts: dict[str, list[str]] = {}
    for sec, v in L.rows:
        if v.flag == "계산보류":
            for r in set(roots(v)):
                lab = f"{v.label}"
                if lab not in impacts.setdefault(r, []):
                    impacts[r].append(lab)

    def yr_of(e):
        return cy if e and cy in e.file else (py if e else "")

    out, seen, groups = [], set(), {}
    for sec, v in L.rows:
        if v.status not in (Status.NEEDS_CHECK, Status.CONFLICT) or v.key in seen or v.flag == "계산보류":
            continue
        if ":loss." in v.key and v.status == Status.CONFLICT:  # 결손금 표 칸별 충돌은 대사 R07 한 건으로 묶음
            continue
        seen.add(v.key)
        e = v.evidence[0] if v.evidence else None
        if e and e.form.startswith("결산부속명세서") and v.flag == "가림":
            g = groups.setdefault((e.file, e.form, e.page), {"cells": [], "e": e, "sec": sec})
            g["cells"].append(e.item)
            continue
        shown, judge = FLAG_TEXT.get(v.flag or "", (None, None))
        if v.flag == "글자표기":
            shown = f"'{v.text}'"
        if v.status == Status.CONFLICT:
            shown, judge = (f"{v.amount:,}" if v.amount is not None else "–"), "서식 간 차이 — 원문 값 그대로 표시(지시: Q-003)"
        imp = impacts.get(v.key, [])
        out.append({
            "kind": "서식 간 차이" if v.status == Status.CONFLICT else "값 확인 필요", "section": sec,
            "file": e.file if e else "", "year": yr_of(e), "form": e.form if e else "", "page": e.page if e else "",
            "item": e.item if e else v.label, "shown": shown or "–", "judge": judge or v.status.value, "note": v.note or "",
            "action": ("값 자리 «확인 필요»" if v.status == Status.NEEDS_CHECK else "원문 값 표시 + 차이 표시")
            + (f"; 보류된 계산 {len(imp)}건: " + ", ".join(imp[:8]) + (" 등" if len(imp) > 8 else "") if imp else ""),
            "q": ", ".join(v.questions), "bbox": e.bbox if e else None, "key": v.key, "impacts": imp})
    for (f_, form, page), g in groups.items():
        codes = sorted({c.split()[0] for c in g["cells"]})
        cols = sorted({c.split()[-1] for c in g["cells"]})
        out.append({"kind": "값 확인 필요", "section": g["sec"], "file": f_, "year": yr_of(g["e"]), "form": form, "page": page,
                    "item": f"거래처코드 {', '.join(codes)} ({len(codes)}곳)의 {'·'.join(cols)} 칸",
                    "shown": "회색 칸(숫자 없음)", "judge": FLAG_TEXT["가림"][1], "note": MASK_GROUP_NOTE,
                    "action": "해당 칸 «확인 필요»; 거래처 수·최대 거래처 비중 등 이 명세서에 의존하는 집계 보류",
                    "q": "", "bbox": g["e"].bbox, "key": f"grp.{form}.{page}", "impacts": []})
    for sec, v in L.rows:  # 원인을 찾지 못한 계산 보류
        if v.flag == "계산보류" and not list(roots(v)) and v.key not in seen:
            seen.add(v.key)
            out.append({"kind": "계산 보류", "section": sec, "file": "", "year": "", "form": "", "page": "", "item": v.label,
                        "shown": "«확인 필요»", "judge": FLAG_TEXT["계산보류"][1], "note": v.note or "",
                        "action": "계산하지 않음", "q": ", ".join(v.questions), "bbox": None, "key": v.key, "impacts": []})
    for f in findings:
        if f["ok"] is False:
            e = f["evidence"][0] if f["evidence"] else None
            yr = f["id"].split("-")[1] if "-" in f["id"] and f["id"].split("-")[1].isdigit() else ""
            out.append({"kind": "서식 간 차이" if f["kind"] in ("서식 간 차이", "충돌") else f["kind"],
                        "section": "대사 " + f["id"], "file": e.file if e else "", "year": yr,
                        "form": " / ".join(sorted({x.form for x in f["evidence"]})) if f["evidence"] else "",
                        "page": ", ".join(sorted({f"p{x.page}" for x in f["evidence"]})), "item": f["title"],
                        "shown": "", "judge": "PDF에 실제로 보이는 숫자끼리 다름(같은 범위의 값인지 확인함)", "note": f["detail"],
                        "action": "표준서식 값 유지, 다른 서식 값으로 덮어쓰지 않고 경고로 남김", "q": ", ".join(f["questions"]),
                        "bbox": None, "key": f["id"], "impacts": []})
    for i, w in enumerate(out, 1):
        w["id"] = f"W{i:02d}"
    return out


MASK_GROUP_NOTE = "PDF에서 회색 처리(가림)된 칸 — 금액이 보이지 않음(0 아님)"

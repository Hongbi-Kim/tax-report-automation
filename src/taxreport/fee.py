"""조정료청구서 계산 (doc/05). 보수표는 config/fee_schedule.yaml."""
from __future__ import annotations

import math
from pathlib import Path

import yaml

from .models import Status, Value


def check(key, label, note, q="Q-002-2") -> Value:
    return Value(key, label, status=Status.NEEDS_CHECK, note=note, questions=[q])


def build_fee(ctx: dict, cfg_dir: str | Path) -> dict:
    cfg = yaml.safe_load((Path(cfg_dir) / "fee_schedule.yaml").read_text())
    C = ctx["C"]
    from .build import known, num
    rev, assets = C["is"].get(1), C["bs"].get(228)
    half = (Value("fee.half_assets", "자산총계 × 1/2", amount=num(assets) / 2, status=Status.DERIVED,
                  inputs=[assets.key], formula="표준재무상태표 자산총계(코드228) ÷ 2") if known(assets)
            else check("fee.half_assets", "자산총계 × 1/2", "자산총계 확인 필요", "-"))
    if not (known(rev) and known(half)):
        basis = check("fee.basis", "기준금액", "매출액 또는 자산총계가 확인되지 않아 기준금액을 정할 수 없음", "-")
        base_fee = check("fee.base", "기본보수", "기준금액 확인 필요로 계산 보류", "-")
        sup = check("fee.supply", "조정료 합계(공급가액)", "기본보수 보류")
        return {"rev": rev, "assets": assets, "half": half, "basis": basis, "bracket_no": None, "bracket": None,
                "base_fee": base_fee, "surcharges": [], "supply": sup, "vat": None, "total": check("fee.total", "청구 금액", "보류"),
                "cms": check("fee.cms", "CMS 출금일", "관리자 설정값 없음", "관리자 설정"),
                "fb": check("fee.fb", "피드백 요청일", "관리자 설정값 없음", "관리자 설정"), "brackets": cfg["brackets"]}
    use_rev = num(rev) >= half.amount
    basis = Value("fee.basis", "기준금액", amount=num(rev) if use_rev else half.amount, status=Status.DERIVED,
                  inputs=[rev.key, half.key], formula="max(매출액, 자산총계 × 1/2)",
                  note="매출액 적용" if use_rev else "자산총계의 1/2 적용")

    br = next(b for b in cfg["brackets"] if basis.amount >= b["from"] and (b["to"] is None or basis.amount < b["to"]))
    idx = cfg["brackets"].index(br) + 1
    if br.get("special"):
        base_fee = check("fee.base", "기본보수", f"기준금액 1,000억원 이상: 보수표상 '{br['special']}'", "Q-002-1")
    else:
        raw = br["base"] + (basis.amount - br["over"]) * br["rate"]
        amt = math.floor(raw)
        base_fee = Value("fee.base", "기본보수", amount=amt, status=Status.ASSUMED if amt != raw else Status.DERIVED,
                         inputs=[basis.key], questions=["Q-002-1"],
                         formula=(f"보수표 {idx}구간: {br['base']:,}원 + ({basis.amount:,.0f} − {br['over']:,}) × "
                                  f"{br['rate'] * 10000:g}/10,000 = {raw:,.2f}원"),
                         assumption="원 미만 절사(A-011)" if amt != raw else None)

    surcharges = []
    rate = cfg.get("credit_surcharge_rate")
    for r in ctx["credits"]["rows"]:
        if rate is None:
            surcharges.append({"row": r, "v": check(f"fee.sur.{r['code']}", f"{r['name']} 가산보수",
                                                    "공제·감면액 가산 비율 미정(Q-002-2)")})
        else:
            surcharges.append({"row": r, "v": Value(f"fee.sur.{r['code']}", f"{r['name']} 가산보수",
                                                    amount=math.floor(r["amount"].amount * rate), status=Status.DERIVED,
                                                    inputs=[r["amount"].key], formula=f"공제감면세액 × {rate}")})
    parts = [base_fee] + [s["v"] for s in surcharges]
    if any(p.status == Status.NEEDS_CHECK for p in parts):
        supply = check("fee.supply", "조정료 합계(공급가액)", "가산보수 미정으로 합계 확정 불가")
    else:
        supply = Value("fee.supply", "조정료 합계(공급가액)", amount=sum(p.amount for p in parts),
                       status=Status.ASSUMED if any(p.status == Status.ASSUMED for p in parts) else Status.DERIVED,
                       inputs=[p.key for p in parts], formula="기본보수 + 가산보수")
    vat = check("fee.vat", "부가가치세", "부가세 별도/포함 여부 미정(Q-002-2)") if cfg.get("vat") is None else None
    total = check("fee.total", "청구 금액", "합계·부가세 미정") if (vat or supply.status == Status.NEEDS_CHECK) else supply
    cms = (check("fee.cms", "CMS 출금일", "관리자 설정값 없음(요구사항 C29)", "관리자 설정")
           if not cfg.get("cms_withdrawal_date") else Value("fee.cms", "CMS 출금일", text=str(cfg["cms_withdrawal_date"])))
    fb = (check("fee.fb", "피드백 요청일", "관리자 설정값 없음(요구사항 C29)", "관리자 설정")
          if not cfg.get("feedback_due_date") else Value("fee.fb", "피드백 요청일", text=str(cfg["feedback_due_date"])))
    return {"rev": rev, "assets": assets, "half": half, "basis": basis, "bracket_no": idx, "bracket": br,
            "base_fee": base_fee, "surcharges": surcharges, "supply": supply, "vat": vat, "total": total,
            "cms": cms, "fb": fb, "brackets": cfg["brackets"]}

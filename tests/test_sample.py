"""샘플 PDF 기대값 테스트 (doc/03 §4). 실행: PYTHONPATH=src python3 -m pytest tests -q"""
from pathlib import Path

import pytest

from taxreport.build import build

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ctx():
    return build(ROOT / "tax/샘플컴퍼니_2024_세무조정계산서.pdf", ROOT / "tax/샘플컴퍼니_2025_세무조정계산서.pdf", ROOT / "config")


def test_standard_fs(ctx):
    P, C = ctx["P"], ctx["C"]
    assert C["is"][1].amount == 192_734_983 and P["is"][1].amount == 92_944_479
    assert C["is"][129].amount == 49_254_390 and P["is"][129].amount == -15_385_718
    assert C["is"][219].amount == 46_496_124 and P["is"][219].amount == -15_510_353
    assert C["bs"][228].amount == 62_210_447 and P["bs"][228].amount == 14_044_981
    assert C["bs"][382].amount == 40_985_771 and P["bs"][382].amount == -5_510_353
    assert C["bs"][236].amount == 1_088_470 and P["bs"][236].amount == 8_135_140


def test_tax(ctx):
    t = ctx["C"]["tc"]
    assert t["tc.06"].amount == 49_215_747 and t["tc.07"].amount == 15_640_153
    assert t["tc.10"].amount == 33_575_594 and t["tc.12"].amount == 3_021_803
    assert t["tc.17"].amount == 604_360 and t["tc.21"].amount == 2_417_443
    assert ctx["P"]["tc"]["tc.10"].amount == -15_510_353


def test_conflict_detected_not_corrected(ctx):
    r07 = next(f for f in ctx["findings"] if f["id"] == "R07")
    assert r07["ok"] is False and ctx["loss_diff"].amount == 129_800
    assert ctx["C"]["tc"]["tc.07"].amount == 15_640_153  # 보정하지 않음 (Q-003)


def test_reconciliations_pass(ctx):
    bad = {f["id"] for f in ctx["findings"] if f["ok"] is False}
    assert bad == {"R07", "R15"}


def test_schedules(ctx):
    sus = ctx["sus"]["2025"]["rows"][0]
    assert sus["partner_code"] == "00105" and sus["vals"]["금액"].amount == 1_088_470
    assert ctx["ar"]["2024"]["n_bal"] == 2 and ctx["ar"]["2025"]["n_bal"] == 4
    assert ctx["loan"]["2025"]["none"] and ctx["loan"]["2024"]["none"]


def test_check_marks_read(ctx):
    rows = {r["label"]: r["v"] for r in ctx["basic_rows"]}
    assert rows["법인구분"].text == "내국법인"
    assert rows["법인종류"].text.startswith("중소기업") and rows["중소기업 여부"].text == "해당"
    assert rows["중소기업 여부"].evidence[0].bbox is not None  # 판독한 동그라미 위치를 근거로 남김


def test_fee(ctx):
    from taxreport.fee import build_fee
    f = build_fee(ctx, ROOT / "config")
    assert f["basis"].amount == 192_734_983 and f["bracket_no"] == 2
    assert f["base_fee"].amount == 629_828  # 500,000 + 92,734,983 × 0.14% = 629,828.98 → 절사(A-011)
    assert f["total"].status.value == "확인 필요"  # 가산율·부가세 미정


def test_fee_brackets_continuous():
    import yaml
    bs = yaml.safe_load((ROOT / "config/fee_schedule.yaml").read_text())["brackets"]
    for a, b in zip(bs, bs[1:]):
        if b.get("special") or a.get("special"):
            continue
        assert a["base"] + (a["to"] - a["over"]) * a["rate"] == b["base"]

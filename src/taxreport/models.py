"""값·근거·상태 모델 (doc/02_데이터모델_근거추적.md)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    CONFIRMED = "확정"       # 문서에서 직접 읽음 (칸이 비어 있는 것도 '공란'으로 확정)
    DERIVED = "계산"         # 확정값만으로 계산
    ASSUMED = "가정"         # 담당자 답이 없어 가정 적용
    NEEDS_CHECK = "확인 필요"  # 값을 정할 수 없음
    CONFLICT = "충돌"        # 근거끼리 값이 다름


@dataclass
class Evidence:
    file: str
    form: str
    page: int
    item: str
    column: str | None = None
    raw: str = ""
    bbox: tuple | None = None  # (x0, x1, top, bottom) — 경고 캡처용

    def short(self) -> str:
        year = "2025" if "2025" in self.file else ("2024" if "2024" in self.file else self.file)
        col = f" [{self.column}]" if self.column else ""
        return f"{year} {self.form} p{self.page} · {self.item}{col}"


@dataclass
class Value:
    key: str
    label: str
    amount: int | float | None = None
    text: str | None = None
    status: Status = Status.CONFIRMED
    evidence: list[Evidence] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    formula: str | None = None
    assumption: str | None = None
    questions: list[str] = field(default_factory=list)
    note: str | None = None
    show_amount: bool = False  # 충돌이어도 원문 값을 그대로 보여 주라는 지시가 있을 때(Q-003)
    flag: str | None = None  # 경고 유형: 가림 / 글자표기 / 합계빈칸 / 서식없음 / 추출실패

    @property
    def blank(self) -> bool:
        """서식 칸이 비어 있음(확정 사실)."""
        return self.amount is None and self.text in (None, "공란") and self.status == Status.CONFIRMED


def derived(key: str, label: str, amount, inputs: list[Value], formula: str, **kw) -> Value:
    """계산값. 입력 상태가 전파된다(02문서 §3)."""
    sts = {v.status for v in inputs}
    if sts & {Status.NEEDS_CHECK, Status.CONFLICT} or any(v.amount is None and not v.blank for v in inputs):
        status = Status.NEEDS_CHECK
    elif Status.ASSUMED in sts:
        status = Status.ASSUMED
    else:
        status = Status.DERIVED
    qs = sorted({q for v in inputs for q in v.questions})
    return Value(key, label, amount if status != Status.NEEDS_CHECK else None, status=status,
                 inputs=[v.key for v in inputs], formula=formula, questions=qs + kw.pop("questions", []), **kw)

"""计算层（自研）：纯函数、无 IO、无全局可变状态。

输入数据序列由调用方传入，并携带 ``fingerprint.data_fingerprint`` 生成的数据指纹。
"""

from __future__ import annotations

CALC_VERSION = "0.1.0"

# 公开算子与类型；子模块必须在 CALC_VERSION 之后导入（fingerprint 依赖它）。
from ladderstock.calc import fingerprint, money, result  # noqa: E402
from ladderstock.calc.fingerprint import (  # noqa: E402
    calc_id,
    canonical_params,
    data_fingerprint,
)
from ladderstock.calc.money import CURRENCIES, UNIT_TO_YUAN, amount, convert  # noqa: E402
from ladderstock.calc.result import (  # noqa: E402
    BAD_ARGUMENT,
    NOT_MEANINGFUL,
    OK,
    RESULT_LIMITED,
    CalcResult,
    NonFiniteValueError,
    assert_finite,
    safe_range_ratio,
    safe_ratio,
)

__all__ = [
    "BAD_ARGUMENT",
    "CALC_VERSION",
    "CURRENCIES",
    "NOT_MEANINGFUL",
    "OK",
    "RESULT_LIMITED",
    "UNIT_TO_YUAN",
    "CalcResult",
    "NonFiniteValueError",
    "amount",
    "assert_finite",
    "calc_id",
    "canonical_params",
    "convert",
    "data_fingerprint",
    "fingerprint",
    "money",
    "result",
    "safe_range_ratio",
    "safe_ratio",
]

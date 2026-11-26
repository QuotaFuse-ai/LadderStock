"""估值计算器（E 包 / DEC-091 §4）：用户面换算工具。

边界：输入全部由调用方给出；不取数、不写库；输出恒带免责声明——
计算器结果**不进**研究管线估值段的 facts（DEC-025 红线原样保留）。
"""

from ladderstock.valuation import calculator

__all__ = ["calculator"]

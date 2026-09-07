"""熔断器（executor）单元测试：per-tool 分键 + 冷却 + 半开恢复。

行为契约：
  - 连续失败 _CIRCUIT_THRESHOLD 次 -> 该工具熔断（CIRCUIT_OPEN），其他工具不受影响。
  - 冷却期内一律拒绝；冷却结束放行一次半开试探（试探进行中其余调用仍拒绝）。
  - 试探成功 -> 完全复位；试探失败 -> 以新的冷却期重新熔断。
  - 熔断拒绝（CIRCUIT_OPEN）本身不计入失败次数。
"""
import time
import unittest

from app.agents import executor
from app.agents.executor import (_CIRCUIT_COOLDOWN_S, _CIRCUIT_THRESHOLD,
                                 _circuit_check, _circuit_record)
from app.core.tool_protocol import ErrorCode, ToolResult, err, ok


def _fail(tool: str) -> ToolResult:
    return err(tool, ErrorCode.TOOL_ERROR, "boom")


class TestCircuitBreaker(unittest.TestCase):
    def setUp(self):
        # 熔断器是模块级状态；每个用例前后清空，避免用例间串扰
        self._saved = (dict(executor._circuit_failures),
                       dict(executor._circuit_opened_at),
                       set(executor._circuit_half_open))
        executor._circuit_failures.clear()
        executor._circuit_opened_at.clear()
        executor._circuit_half_open.clear()

    def tearDown(self):
        executor._circuit_failures.clear()
        executor._circuit_failures.update(self._saved[0])
        executor._circuit_opened_at.clear()
        executor._circuit_opened_at.update(self._saved[1])
        executor._circuit_half_open.clear()
        executor._circuit_half_open.update(self._saved[2])

    def _trip(self, tool: str) -> None:
        for _ in range(_CIRCUIT_THRESHOLD):
            _circuit_record(tool, _fail(tool))

    def test_trips_after_threshold_and_is_per_tool(self):
        # 阈值前不熔断
        for _ in range(_CIRCUIT_THRESHOLD - 1):
            _circuit_record("tool_a", _fail("tool_a"))
        self.assertIsNone(_circuit_check("tool_a"))
        # 达到阈值 -> 熔断，且只影响 tool_a
        _circuit_record("tool_a", _fail("tool_a"))
        r = _circuit_check("tool_a")
        self.assertIsNotNone(r)
        self.assertEqual(r.error_code, ErrorCode.CIRCUIT_OPEN)
        self.assertIsNone(_circuit_check("tool_b"))

    def test_success_resets_failure_count(self):
        _circuit_record("tool_a", _fail("tool_a"))
        _circuit_record("tool_a", _fail("tool_a"))
        _circuit_record("tool_a", ok("tool_a", text="fine"))
        for _ in range(_CIRCUIT_THRESHOLD - 1):
            _circuit_record("tool_a", _fail("tool_a"))
        self.assertIsNone(_circuit_check("tool_a"))

    def test_circuit_open_rejection_not_counted(self):
        self._trip("tool_a")
        # 熔断拒绝不应把失败数继续推高或改变状态
        r = _circuit_check("tool_a")
        _circuit_record("tool_a", r)
        self.assertEqual(executor._circuit_failures["tool_a"], _CIRCUIT_THRESHOLD)

    def test_half_open_probe_success_resets(self):
        self._trip("tool_a")
        # 冷却期结束（把熔断时刻拨回过去）
        executor._circuit_opened_at["tool_a"] = time.time() - _CIRCUIT_COOLDOWN_S - 1
        self.assertIsNone(_circuit_check("tool_a"))  # 放行一次试探
        # 试探进行中，其余调用仍按熔断拒绝
        r = _circuit_check("tool_a")
        self.assertEqual(r.error_code, ErrorCode.CIRCUIT_OPEN)
        # 试探成功 -> 完全复位
        _circuit_record("tool_a", ok("tool_a", text="recovered"))
        self.assertIsNone(_circuit_check("tool_a"))
        self.assertNotIn("tool_a", executor._circuit_opened_at)
        self.assertEqual(executor._circuit_failures["tool_a"], 0)

    def test_half_open_probe_failure_retrips(self):
        self._trip("tool_a")
        executor._circuit_opened_at["tool_a"] = time.time() - _CIRCUIT_COOLDOWN_S - 1
        self.assertIsNone(_circuit_check("tool_a"))  # 试探放行
        _circuit_record("tool_a", _fail("tool_a"))   # 试探失败 -> 重新熔断
        r = _circuit_check("tool_a")
        self.assertEqual(r.error_code, ErrorCode.CIRCUIT_OPEN)
        # 新的冷却期重新计时
        self.assertGreater(time.time() - executor._circuit_opened_at["tool_a"], -1)
        self.assertLess(time.time() - executor._circuit_opened_at["tool_a"], 5)


if __name__ == "__main__":
    unittest.main()

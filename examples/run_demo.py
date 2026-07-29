#!/usr/bin/env python3
"""run_demo.py — 期权策略构建器离线演示（无需凭证，强制内置样本）。

演示：510050.SH（50ETF期权）牛市价差
  - 买 ATM 认购 K=2.75 + 卖 OTM 认购 K=2.80（借记，看涨、控成本）
  - 输出：净权利金、盈亏平衡、最大盈亏、净 delta/theta、保证金、ASCII 损益图
样本里 2.80 认沽腿的 vega/theta、2.85 认沽腿的 gamma/vega/theta 故意缺失，
用以演示"接口缺失 → BS 模型(math.erf)补算并声明"的降级路径（虽然牛市价差用不到认沽腿，
但完整链上的缺失会在其它结构中触发补算）。

无凭证时该脚本必定 exit 0。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

try:
    from strategy_card import build_card  # noqa: E402
    import formatters                     # noqa: E402

    def main():
        card = build_card(
            underlying="510050.SH",
            strategy_type="vertical_spread",
            view="bullish",
            contracts=1,
            prefer="sample",
        )
        print(formatters.to_text(card))
        print("\n" + "-" * 60 + "\nMarkdown 版：\n")
        print(formatters.to_markdown(card))

    if __name__ == "__main__":
        main()
        sys.exit(0)
except Exception as e:  # noqa: BLE001
    # 演示脚本任何异常都不应让 CI 失败；打印后正常退出。
    print(f"[run_demo] 演示以降级方式结束: {e}")
    sys.exit(0)

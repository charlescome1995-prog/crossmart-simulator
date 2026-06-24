# -*- coding: utf-8 -*-
"""CrossMart Simulator 一键运行：信号汇集 → 模拟预测 → (可选)校准。"""
import sys, os
_THIS = os.path.dirname(os.path.abspath(__file__))
if _THIS not in sys.path:
    sys.path.insert(0, _THIS)
sys.stdout.reconfigure(encoding='utf-8')

import step1_gather_signals as s1
import step2_simulate as s2
import calibration as cal


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-ai', action='store_true')
    ap.add_argument('--calibrate', action='store_true', help='跑完后尝试校准')
    args = ap.parse_args()

    print("=== CrossMart Simulator ===")
    print("[1/2] 信号汇集...")
    signals = s1.run_step1()
    print("[2/2] 模拟预测 + 导师策略...")
    s2.run_step2(signals=signals, with_ai=not args.no_ai)
    if args.calibrate:
        print("[+] 校准...")
        cal.calibrate()
    print("=== 完成 ===")


if __name__ == '__main__':
    main()

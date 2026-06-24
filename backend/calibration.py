# -*- coding: utf-8 -*-
"""
CrossMart Simulator — calibration: 自我进化校准器
=================================================
核心价值：让预测系统随真实数据不断变准，像导师一样越来越靠谱。

机制：
  1. 读 predictions_log.json 里 actual 字段已回填的历史预测
  2. 对比「预测净利/销量 vs 真实值」算误差
  3. 误差大 → 微调 weights.json 的模型参数（销量曲线/COGS等）和维度权重
  4. 每次调整在 weights.json 的 history 里记录【理由】，confidence 随样本量提升
  5. 可选：让 LLM 复盘误差，建议提示词/维度的调整方向

真实数据回填：由 fill_actual() 提供接口——当 Monitor 后续抓到某 ASIN 的真实
BSR/评论/销量时，调用它把 actual 写回对应预测条目。
"""
import sys, os, json
from datetime import datetime

_THIS = os.path.dirname(os.path.abspath(__file__))
if _THIS not in sys.path:
    sys.path.insert(0, _THIS)
sys.stdout.reconfigure(encoding='utf-8')

OUTPUT_DIR = os.path.join(_THIS, 'data', 'output')
WEIGHTS_PATH = os.path.join(_THIS, 'weights.json')
PRED_LOG = os.path.join(OUTPUT_DIR, 'predictions_log.json')


def _load(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fill_actual(at_timestamp, actual_monthly_sales=None, actual_net_profit=None):
    """把某次预测的真实结果回填（供 Monitor 数据回流时调用）。"""
    log = _load(PRED_LOG, [])
    hit = False
    for entry in log:
        if entry.get('at') == at_timestamp:
            entry['actual'] = {
                'monthly_sales': actual_monthly_sales,
                'net_profit': actual_net_profit,
                'filled_at': datetime.now().isoformat(),
            }
            hit = True
    if hit:
        _save(PRED_LOG, log)
        print(f"  ✅ 已回填真实数据: {at_timestamp}")
    else:
        print(f"  [warn] 未找到预测条目: {at_timestamp}")
    return hit


def _rel_error(pred, actual):
    if not pred or not actual or actual == 0:
        return None
    return abs(pred - actual) / abs(actual)


def calibrate(verbose=True):
    """对比预测 vs 真实，微调参数，记录理由。"""
    weights = _load(WEIGHTS_PATH, None)
    if weights is None:
        print("  [err] weights.json 缺失")
        return None
    log = _load(PRED_LOG, [])

    # 收集已回填真实值的样本
    samples = [e for e in log if e.get('actual') and e['actual'].get('monthly_sales')]
    if not samples:
        if verbose:
            print("  [info] 暂无已回填真实数据的样本，无法校准。")
            print("         等 Monitor 后续抓到真实销量后，用 fill_actual() 回填即可。")
        return {'calibrated': False, 'reason': 'no_actual_samples', 'sample_count': 0}

    # 计算销量预测的平均相对误差
    sales_errors = []
    for e in samples:
        pred = (e.get('predicted') or {}).get('monthly_sales')
        act = e['actual'].get('monthly_sales')
        err = _rel_error(pred, act)
        if err is not None:
            sales_errors.append(err)

    if not sales_errors:
        return {'calibrated': False, 'reason': 'no_comparable', 'sample_count': len(samples)}

    avg_err = sum(sales_errors) / len(sales_errors)
    n = len(sales_errors)

    # 校准动作：误差大就调整 review_rate / COGS（保守步长，避免震荡）
    se = weights['sales_estimation']
    adjustments = []
    if avg_err > 0.30:
        # 系统性高估或低估：检查方向
        over = sum(1 for e in samples
                   if (e.get('predicted') or {}).get('monthly_sales', 0) >
                   (e['actual'].get('monthly_sales') or 0))
        if over > n / 2:
            # 普遍高估销量 → 降低 review_rate（同样评论增量反推更少销量）
            old = se['review_rate_pct']
            se['review_rate_pct'] = round(min(5.0, old * 1.1), 2)
            adjustments.append(f"销量普遍高估(avg_err={avg_err:.0%}): review_rate_pct {old}→{se['review_rate_pct']}")
        else:
            old = se['review_rate_pct']
            se['review_rate_pct'] = round(max(0.5, old * 0.9), 2)
            adjustments.append(f"销量普遍低估(avg_err={avg_err:.0%}): review_rate_pct {old}→{se['review_rate_pct']}")

    # confidence 随样本量上升（误差越小越高）
    new_conf = round(min(0.95, (1 - min(avg_err, 1)) * (n / (n + 5))), 2)
    old_conf = weights.get('confidence')
    weights['confidence'] = new_conf
    weights['calibration_count'] = weights.get('calibration_count', 0) + 1
    weights['version'] = weights.get('version', 1) + 1
    weights['updated_at'] = datetime.now().isoformat()

    reason = (f"第{weights['calibration_count']}次校准: {n}个真实样本, 平均销量误差{avg_err:.0%}, "
              f"confidence {old_conf}→{new_conf}. " + ("; ".join(adjustments) if adjustments else "误差可接受,未调参数"))
    weights.setdefault('history', []).append({
        'version': weights['version'],
        'at': weights['updated_at'],
        'sample_count': n,
        'avg_sales_error': round(avg_err, 3),
        'reason': reason,
    })
    _save(WEIGHTS_PATH, weights)

    if verbose:
        print(f"  ✅ 校准完成（v{weights['version']}）")
        print(f"     {reason}")
    return {'calibrated': True, 'version': weights['version'], 'avg_error': avg_err,
            'confidence': new_conf, 'adjustments': adjustments, 'sample_count': n}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--fill', nargs=3, metavar=('AT', 'SALES', 'PROFIT'),
                    help='回填真实数据: --fill <时间戳> <月销量> <净利>')
    args = ap.parse_args()
    if args.fill:
        fill_actual(args.fill[0], float(args.fill[1]), float(args.fill[2]))
    calibrate()

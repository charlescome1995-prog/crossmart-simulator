# -*- coding: utf-8 -*-
"""
CrossMart Simulator — step1: 信号汇集
=====================================
从其他4站（Monitor / Ops / Selector / Listing）读取已有数据，
汇总成预测引擎的统一输入。不重新抓取，零额外成本。

输出: data/output/sim-signals.json
"""
import sys, os, json
from datetime import datetime

_THIS = os.path.dirname(os.path.abspath(__file__))
if _THIS not in sys.path:
    sys.path.insert(0, _THIS)
sys.stdout.reconfigure(encoding='utf-8')

# 各站数据文件（相对 workspace 根）
_WS = os.path.abspath(os.path.join(_THIS, '..', '..'))
MONITOR_DATA = os.path.join(_WS, 'crossmart-monitor', 'frontend', 'data', 'rawData.json')
OPS_DATA = os.path.join(_WS, 'crossmart-ops', 'frontend', 'data', 'ops-data.json')
SELECTOR_DATA = os.path.join(_WS, 'crossmart-selector', 'frontend', 'data', 'selection-data.json')

OUTPUT_DIR = os.path.join(_THIS, 'data', 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _load(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"  [warn] 读取失败 {os.path.basename(path)}: {str(e)[:60]}")
        return None


def gather_monitor():
    """竞品 listing 走向：价格/评分/评论数/BSR 变化。"""
    d = _load(MONITOR_DATA)
    if not d:
        return []
    items = d.get('items', []) if isinstance(d, dict) else []
    out = []
    for it in items:
        diff = it.get('diff', {}) or {}
        out.append({
            'asin': it.get('asin', ''),
            'title': (it.get('title', '') or '')[:120],
            'brand': it.get('brand', ''),
            'price': it.get('price'),
            'rating': it.get('rating'),
            'reviews': it.get('reviews'),
            'price_change': (diff.get('price', {}) or {}).get('pct'),
            'reviews_change': (diff.get('reviews', {}) or {}).get('change'),
            'source_keyword': it.get('source_keyword', ''),
        })
    return out


def gather_ops():
    """流量结构 + 广告竞争 + 评论VOC痛点。"""
    d = _load(OPS_DATA)
    if not d:
        return {}
    struct = d.get('traffic_structure', [])
    nat = sum(x.get('natural_kw', 0) or 0 for x in struct)
    ad = sum(x.get('ad_kw', 0) or 0 for x in struct)
    tot = sum(x.get('total_kw', 0) or 0 for x in struct)
    ri = d.get('review_insight', {}) or {}
    return {
        'natural_pct': round(nat / tot * 100, 1) if tot else 0,
        'ad_pct': round(ad / tot * 100, 1) if tot else 0,
        'ad_competition_summary': (d.get('ai_diagnosis', {}) or {}).get('summary', ''),
        'review_praise': ri.get('praise_points', [])[:5],
        'review_pain': ri.get('pain_points', [])[:5],
        'review_opportunities': ri.get('opportunities', [])[:4],
    }


def gather_selector():
    """市场需求/竞争度评分（取前若干高分项）。"""
    d = _load(SELECTOR_DATA)
    if not d:
        return []
    items = d.get('items', []) if isinstance(d, dict) else []
    out = []
    for it in items[:10]:
        out.append({
            'asin': it.get('asin', ''),
            'title': (it.get('title', '') or '')[:100],
            'market_score': it.get('market_score') or it.get('score'),
            'competition': it.get('competition') or it.get('competition_level'),
            'price': it.get('price'),
        })
    return out


def run_step1():
    signals = {
        'generated_at': datetime.now().isoformat(),
        'monitor_competitors': gather_monitor(),
        'ops': gather_ops(),
        'selector_market': gather_selector(),
    }
    out = os.path.join(OUTPUT_DIR, 'sim-signals.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(signals, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 信号汇集 → {out}")
    print(f"     竞品 {len(signals['monitor_competitors'])} / 市场项 {len(signals['selector_market'])}"
          f" / VOC痛点 {len(signals['ops'].get('review_pain', []))}")
    return signals


if __name__ == '__main__':
    run_step1()

# -*- coding: utf-8 -*-
"""
CrossMart Simulator — step2: 盈利预测 + 导师策略
================================================
1. 透明盈利模型：BSR/评论双重交叉估销量 → 营收/成本/净利/利润率
2. 6维加权机会分（权重来自 weights.json，可进化）
3. LLM 导师：给出有数据依据的实战策略 + 风险预警 + 置信度
4. 每次预测写入 predictions_log.json，供 calibration.py 回溯校准

输出: ../frontend/data/sim-data.json
"""
import sys, os, json
from datetime import datetime

_THIS = os.path.dirname(os.path.abspath(__file__))
if _THIS not in sys.path:
    sys.path.insert(0, _THIS)
sys.stdout.reconfigure(encoding='utf-8')

import llm_client
from llm_config import CHAT_MODEL

OUTPUT_DIR = os.path.join(_THIS, 'data', 'output')
FRONTEND_DATA = os.path.join(_THIS, '..', 'frontend', 'data')
os.makedirs(FRONTEND_DATA, exist_ok=True)
WEIGHTS_PATH = os.path.join(_THIS, 'weights.json')
PRED_LOG = os.path.join(OUTPUT_DIR, 'predictions_log.json')


def load_weights():
    with open(WEIGHTS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def estimate_sales_from_bsr(bsr, curve):
    """BSR → 月销量，分段线性插值。"""
    if not bsr or bsr <= 0:
        return None
    anchors = sorted(curve.get('anchors', []), key=lambda x: x[0])
    if not anchors:
        return None
    if bsr <= anchors[0][0]:
        return anchors[0][1]
    if bsr >= anchors[-1][0]:
        return anchors[-1][1]
    for i in range(len(anchors) - 1):
        x0, y0 = anchors[i]
        x1, y1 = anchors[i + 1]
        if x0 <= bsr <= x1:
            # log 空间插值更平滑
            import math
            t = (math.log(bsr) - math.log(x0)) / (math.log(x1) - math.log(x0)) if x1 > x0 else 0
            return round(y0 + t * (y1 - y0))
    return anchors[-1][1]


def estimate_sales_from_reviews(reviews_change, review_rate_pct):
    """评论月增量 → 月销量（约 review_rate_pct% 买家留评）。交叉校验用。"""
    try:
        delta = abs(float(str(reviews_change).replace('+', '').replace(',', '')))
        if delta <= 0:
            return None
        return round(delta / (review_rate_pct / 100.0))
    except Exception:
        return None


def estimate_sales_fallback(total_reviews, rating):
    """无 BSR / 无评论增量时的保守兑底：用评论总量规模粗估月销（低置信）。
    经验法：评论总数越大 → 累计销量越大，月销约为总评论数的 8%（成熟链接经验值）。"""
    try:
        tr = float(total_reviews or 0)
        if tr <= 0:
            return None
        base = round(tr * 0.08)
        # 高评分适度上调
        if rating and float(rating) >= 4.5:
            base = round(base * 1.15)
        return max(10, base)
    except Exception:
        return None


def profit_model(price, monthly_sales, pm):
    """透明盈利模型，返回营收/成本/净利/利润率。"""
    if not price or not monthly_sales:
        return None
    price = float(price)
    revenue = price * monthly_sales
    referral = revenue * pm['amazon_referral_fee_pct']
    fba = pm['fba_fee_per_unit'] * monthly_sales
    cogs = price * pm['cogs_pct_of_price'] * monthly_sales
    ad_cost = revenue * pm['default_acos']
    total_cost = referral + fba + cogs + ad_cost
    net = revenue - total_cost
    return {
        'monthly_sales': monthly_sales,
        'price': round(price, 2),
        'revenue': round(revenue, 2),
        'cost_breakdown': {
            'referral_fee': round(referral, 2),
            'fba_fee': round(fba, 2),
            'cogs': round(cogs, 2),
            'ad_cost': round(ad_cost, 2),
        },
        'total_cost': round(total_cost, 2),
        'net_profit': round(net, 2),
        'profit_margin_pct': round(net / revenue * 100, 1) if revenue else 0,
    }


def opportunity_score(signals, weights):
    """6维加权机会分（0-100）。每维给出原始分+依据。"""
    w = weights['dimension_weights']
    ops = signals.get('ops', {})
    comps = signals.get('monitor_competitors', [])
    mkt = signals.get('selector_market', [])

    dims = {}
    # 市场需求：竞品评论数中位规模越大需求越旺
    rv = sorted([c.get('reviews') or 0 for c in comps])
    med = rv[len(rv) // 2] if rv else 0
    dims['market_demand'] = min(100, med / 200) if med else 50
    # 竞争强度（反向：广告占比越高竞争越烈→机会越低）
    ad_pct = ops.get('ad_pct', 0) or 0
    dims['competition_intensity'] = max(0, 100 - ad_pct * 1.5)
    # Listing质量：占位（后续接 Listing 站评分），默认中性
    dims['listing_quality'] = 60
    # 站外潜力：占位（手动/估算），默认中性
    dims['offsite_potential'] = 50
    # 价格卡位：竞品价格离散度大→有卡位空间
    prices = [float(c['price']) for c in comps if c.get('price')]
    if len(prices) >= 2:
        spread = (max(prices) - min(prices)) / (sum(prices) / len(prices))
        dims['price_positioning'] = min(100, spread * 120)
    else:
        dims['price_positioning'] = 50
    # 评论壁垒（反向：竞品痛点越多→我方差异化壁垒机会越大）
    pain = len(ops.get('review_pain', []))
    dims['review_moat'] = min(100, 40 + pain * 10)

    total = sum(dims[k] * w.get(k, 0) for k in dims)
    return round(total, 1), {k: round(v, 1) for k, v in dims.items()}


def ai_mentor(signals, profit_summary, score, dims, weights):
    """LLM 导师：有数据依据的实战策略 + 风险预警。"""
    payload = {
        'opportunity_score': score,
        'dimension_scores': dims,
        'profit_forecast': profit_summary,
        'ops_signals': signals.get('ops', {}),
        'top_competitors': signals.get('monitor_competitors', [])[:5],
        'model_confidence': weights.get('confidence'),
        'calibration_count': weights.get('calibration_count'),
    }
    system = (
        "You are a veteran Amazon strategy mentor for cross-border sellers. "
        "You give EVIDENCE-BASED, actionable strategy like a real mentor: every recommendation "
        "must cite the specific data signal it is based on. Be direct about risks and uncertainty. "
        "Output STRICT JSON only, no markdown fences."
    )
    prompt = f"""Based on the simulation data below, act as a mentor and produce a strategy verdict.

DATA (JSON):
{json.dumps(payload, ensure_ascii=False, indent=2)}

Return STRICT JSON (all text in English, concise, every point evidence-backed):
{{
  "verdict": "GO / CAUTION / AVOID — one word + 1 sentence why",
  "profit_outlook": "2-3 sentence read on profitability potential, referencing the forecast numbers",
  "strategy": [
    "4-6 concrete moves; each MUST reference the data signal behind it (e.g. 'because competitor ad_pct is X%...')"
  ],
  "risks": ["3-4 risks/uncertainties worth watching"],
  "confidence_note": "1 sentence: how much to trust this given model_confidence and calibration_count"
}}
Do NOT invent numbers not present in the data."""
    try:
        raw = (llm_client.chat_openai(prompt, system=system, model=CHAT_MODEL,
                                      max_tokens=2000, temperature=0.4) or '').strip()
        if raw.startswith('```'):
            raw = raw.split('```', 2)[1] if '```' in raw else raw
            raw = raw.lstrip('json').strip().rstrip('`').strip()
        s, e = raw.find('{'), raw.rfind('}')
        if s >= 0 and e > s:
            raw = raw[s:e+1]
        return json.loads(raw)
    except Exception as ex:
        return {'verdict': f'(mentor unavailable: {str(ex)[:100]})',
                'profit_outlook': '', 'strategy': [], 'risks': [], 'confidence_note': ''}


def _append_prediction_log(entry):
    """把本次预测写入日志，供后续校准对比。"""
    log = []
    if os.path.exists(PRED_LOG):
        try:
            with open(PRED_LOG, 'r', encoding='utf-8') as f:
                log = json.load(f)
        except Exception:
            log = []
    log.append(entry)
    with open(PRED_LOG, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def run_step2(signals=None, with_ai=True):
    if signals is None:
        sig_path = os.path.join(OUTPUT_DIR, 'sim-signals.json')
        if not os.path.exists(sig_path):
            print("  [err] 找不到 sim-signals.json，先跑 step1")
            return None
        with open(sig_path, 'r', encoding='utf-8') as f:
            signals = json.load(f)

    weights = load_weights()
    pm = weights['profit_model']
    se = weights['sales_estimation']

    # 对每个竞品做盈利预测（取数据最全的做主预测）
    forecasts = []
    for c in signals.get('monitor_competitors', []):
        bsr = c.get('bsr')  # monitor 可能无 BSR，用评论交叉
        sales_bsr = estimate_sales_from_bsr(bsr, se['bsr_to_monthly_sales_curve']) if bsr else None
        sales_rev = estimate_sales_from_reviews(c.get('reviews_change'), se['review_rate_pct'])
        # 取两者可用值的均值
        cands = [x for x in (sales_bsr, sales_rev) if x]
        monthly = round(sum(cands) / len(cands)) if cands else None
        basis_note = 'bsr+review_delta'
        # 兜底：无 BSR 也无评论增量时，用评论总量规模粗估（低置信）
        if monthly is None:
            monthly = estimate_sales_fallback(c.get('reviews'), c.get('rating'))
            basis_note = 'fallback_total_reviews(low_confidence)'
        pf = profit_model(c.get('price'), monthly, pm) if monthly else None
        if pf:
            pf['asin'] = c.get('asin')
            pf['title'] = c.get('title')
            pf['sales_basis'] = {'from_bsr': sales_bsr, 'from_reviews': sales_rev,
                                 'method': basis_note}
            forecasts.append(pf)

    # 机会分
    score, dims = opportunity_score(signals, weights)

    # 主盈利摘要（取净利最高的一个做代表）
    profit_summary = max(forecasts, key=lambda x: x.get('net_profit', 0)) if forecasts else None

    ai = {}
    if with_ai:
        print("  [AI] 导师策略生成中...")
        ai = ai_mentor(signals, profit_summary, score, dims, weights)

    result = {
        'generated_at': datetime.now().isoformat(),
        'opportunity_score': score,
        'dimension_scores': dims,
        'dimension_weights': weights['dimension_weights'],
        'profit_forecasts': forecasts,
        'profit_summary': profit_summary,
        'mentor': ai,
        'model': {
            'version': weights.get('version'),
            'confidence': weights.get('confidence'),
            'calibration_count': weights.get('calibration_count'),
        },
    }
    out = os.path.join(FRONTEND_DATA, 'sim-data.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 写预测日志（供校准）
    _append_prediction_log({
        'at': result['generated_at'],
        'model_version': weights.get('version'),
        'opportunity_score': score,
        'predicted': profit_summary,
        'input_snapshot': {
            'competitor_count': len(signals.get('monitor_competitors', [])),
            'ops': signals.get('ops', {}),
        },
        'actual': None,  # 等真实数据回流后由 calibration.py 填
    })

    print(f"  ✅ 模拟完成 → {out}")
    print(f"     机会分 {score} / 盈利预测 {len(forecasts)} 项 / 置信度 {weights.get('confidence')}")
    return result


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-ai', action='store_true')
    args = ap.parse_args()
    run_step2(with_ai=not args.no_ai)

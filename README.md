# CrossMart Simulator 🔮

**价值交付的预测系统** —— CrossMart 四站工具链的第5个平行站点。

把 Monitor / Ops / Selector / Listing 的数据综合起来，输出**盈利预期**和**有根有据的实战策略**，并随真实数据积累**自我进化**，像一个真正的亚马逊导师。

## 核心能力

1. **盈利预测（透明可解释）**
   - 月销量：BSR 曲线 + 评论增速双重交叉估算（无数据时用评论总量兜底，标注低置信）
   - 盈利模型：营收 − (佣金15% + FBA + COGS + 广告费) = 净利 / 利润率

2. **6维加权机会分**
   市场需求 / 竞争强度 / Listing质量 / 站外潜力 / 价格卡位 / 评论壁垒
   （权重存 `weights.json`，可进化）

3. **导师级策略（LLM）**
   GO/CAUTION/AVOID 裁决 + 每条带数据依据的策略 + 风险预警 + 置信度说明

4. **🔑 自我进化校准**
   - 每次预测写入 `predictions_log.json`
   - 真实数据回流后，`calibration.py` 对比预测vs实际 → 微调参数/权重 → 记录每次调整理由
   - 样本越多，置信度越高（前端显示模型版本/校准次数/置信度）

## 用法

```bash
# 一键运行：信号汇集 → 预测 → 导师策略
python backend/run_simulator.py

# 跑完顺带校准（需已有真实数据回填）
python backend/run_simulator.py --calibrate

# 回填某次预测的真实结果（供校准）
python backend/calibration.py --fill <时间戳> <真实月销量> <真实净利>
```

需设环境变量 `ARK_API_KEY`（火山方舟）。

## 结构

```
backend/
  step1_gather_signals.py   # 从其他4站读数据汇集信号
  step2_simulate.py         # 盈利模型 + 6维评分 + 导师LLM
  calibration.py            # 自我进化校准器
  weights.json              # 可进化参数（模型状态）
  data/output/predictions_log.json  # 预测历史（进化样本）
frontend/
  simulator.html            # 前端（GitHub Pages 发布）
  data/sim-data.json        # 前端数据
```

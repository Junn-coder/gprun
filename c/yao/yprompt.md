Background:
- c/yao/yaolist.md — 30 妖股池 + 每日操作规则 (daily refresh via build_yao_pool.py)
- c/yao/up.md — 全市场涨停统计 (reference only; pool now comes from flat_yao data)
- c/ctool/yao_gate.py — 妖股专用闸门 (池内涨停数 + 指数极端崩盘检测)
- c/ctool/build_yao_pool.py — dynamic pool builder: scan flat_yao, rank by recent LU, output yaolist.md

⚠ CRITICAL — 数据刷新 (每次分析前必须执行):
  第零步: python c/ctool/build_yao_pool.py --months 6 --top 30  # DAILY pool rebuild
  第一步: python c/ctool/cn_stock.py --history <all 30 codes>  # 刷新缓存
  第二步: Sina API 获取实时昨收 + 现价 (用实时API判断涨停, 不用缓存文件!)

Tools (RUN them in order):
- c/ctool/yao_gate.py — 妖股专用闸门 (池内涨停数为主, 指数 >3% 暴跌为辅)
- c/ctool/cn_stock.py <all 30 codes> --history — MUST run first, refresh all price caches
- Sina API (sh/sz code) — 实时: 昨收 + 今开 + 现价 + 涨跌 (判断涨停用此源, 不用缓存)

Daily (step 0, before everything): python c/ctool/build_yao_pool.py --months 6 --top 30  # rebuild pool fresh each day

After running tools, write c/yao/yaotoday.md:

```
# 妖股今日 — YYYY-MM-DD (周X)

闸门: GREEN/AMBER/RED  (RED → 空仓, 今日不选)

## 扫描结果
| # | 代码 | 名称 | 现价 | 昨收 | 昨涨跌 | 昨涨停 | 今日开盘 | 动作 |
|---|------|------|------|------|--------|--------|----------|------|
| 1 | XXXXXX | XX | ¥X | ¥X | +X% | Y/N | ¥X | 买入/放弃 |

## 买入建议
> [code] [name]
> 入场: ¥X (T+1 开盘)  X股 ≈ ¥25,000
> 止损: ¥X (-5%)  止盈: ¥X (+10%)
> 理由: [昨日封板时间/换手/板块热度]

> 不建议 [code] [name]。理由: [一字板/炸板/跌停/ST]

## 持仓
| 代码 | 成本 | 现价 | 浮盈 | 持股天 | 动作 |
|------|------|------|------|--------|------|
| XXXXXX | ¥X | ¥X | ±X% | N天 | 持有/清仓 — 触发条件 |

> 合计: N只 ¥X,XXX  (仓位 ¥X,XXX / ¥50,000)
```

Rules:
- 闸门来自 yao_gate.py: RED = 指数>3%暴跌 → 写 "闸门 RED — 空仓" 然后停止
- AMBER = 池内0涨停 → 最多选1只; GREEN = 池内1-2涨停 → 最多选2只; GREEN(热) = 池内3+涨停 → 最多选3只
- 只选昨日涨停 + 非一字板 + 非 ST 的票
- 开盘一字板 (>+9.5%) → 放弃
- 最多 3 只, 每只 ¥5,000, 总仓 ≤ ¥15,000
- 出场: 3天无板 / -5%止损 / +10%止盈 → 次日开盘清仓
- 炸板不回封 → 次日清

Tone: terse, 代码+数字, no narrative.

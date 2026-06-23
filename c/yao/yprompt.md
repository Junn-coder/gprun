Background:
- c/yao/yaolist.md — 25 妖股池 + 每日操作规则
- c/yao/up.md — 全市场涨停统计 (>10次)
- c/framed.md — 主线框架 (共用闸门 + ATR)

Tools (RUN them):
- c/ctool/index.py — Layer-1 gate (GREEN/AMBER/RED)
- Sina API (sh/sz code) — 实时价格 + 昨收 + 涨停状态

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
- 闸门 RED → 写 "闸门 RED — 空仓" 然后停止
- 只选昨日涨停 + 非一字板 + 非 ST 的票
- 开盘一字板 (>+9.5%) → 放弃
- 最多 2 只, 每只 ¥5,000, 总仓 ≤ ¥10,00
- 出场: 3天无板 / -5%止损 / +10%止盈 → 次日开盘清仓
- 炸板不回封 → 次日清

Tone: terse, 代码+数字, no narrative.

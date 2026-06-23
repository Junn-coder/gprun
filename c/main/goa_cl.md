# Working rules (apply every turn)

The user is a senior engineer. Apply these silently as default behavior; do not recite them.

- **a — Don't assume.** State unknowns; don't fill gaps with guesses.
- **b — Don't hide confusion.** If something is unclear, say so plainly.
- **c — Surface tradeoffs.** Name the alternative and why it was rejected.
- **d — Goal-driven execution.** Keep the actual goal in view; don't drift.
- **e — Don't touch what you don't understand.** No edits to code/systems not yet understood.
- **f — Simple first.** Prefer the simplest approach before adding complexity.
- **g — Verify before trust.** Check against real code/output; don't assert from memory.
- **h — Batch questions.** Group clarifications into one round.
- **i — Reflect after each step.** Sanity-check before moving on.
- **j — Save my tokens.** Be concise; no filler.
- **k — Treat me like a senior.** Engineer-to-engineer; skip hand-holding and hype.

When depth is wanted: list what I'd check first; name the alternative and why I rejected it (c);
answer "what would change your answer?". Disclose answered-from-memory vs. read-the-code,
alternatives considered, known gaps, and whether the question is near the edge of training.

---

# framed.md 当前规则（2026-06-13 校准完毕）

## 核心参数

| 参数 | 值 |
|------|-----|
| 本金 | ¥75,000 |
| 仓位 | 3 个，每笔 ¥25,000 |
| 入场 | 只在 GREEN 日，T+1 开盘买入首板 |
| 市值 | ¥30 亿 – ¥500 亿自由流通市值 |
| 选股 | 按当日涨停最多板块排名，每板块取一只，分散到 3 个板块 |
| 时间止损 | 第 5 天收盘浮盈 < +2% → 次日开盘出 |
| 价格止损 | max(5%, 1.0× 10日 ATR)，封顶 10% |
| 止盈 | +8% 出一半，+15% 出另一半（固定目标，无移动止损） |
| 最长持仓 | 10 天 |
| 市场门槛 | 炸板率 <30% 暖市 / >40% 冻结空仓 |
| 排除 | 5 连板以上、T+1 一字板、ST/退市 |

## 回测收益（2024-05-28 → 2026-05-29，2 年）

| 指标 | 数值 |
|------|------|
| 交易笔数 | 124 |
| 胜率 | 56% |
| 净盈亏 | **+¥57,495** |
| 总收益率 | **+76.7%** |
| 单笔期望 | ~¥464 |

## 回测收益（最近 1 年：2025-06-13 → 2026-06-13）

| 指标 | 数值 |
|------|------|
| 交易笔数 | 69 |
| 胜率 | 64% |
| 净盈亏 | **+¥50,297** |
| 收益率 | **+67.1%** |

## 校准历史

| 日期 | 变更 | 效果 |
|------|------|------|
| 0611 | Phase B: ATR 1.0× 统一所有板块 | |
| 0611 | Phase C: 时间止损 day-3 → day-5 | +¥6,613 |
| 0613 | Phase D: 仓位 2→3，本金 50K→75K | +¥9,028 |
| 0613 | 移除 MA5 移动止损 | +¥11,878 |
| 0613 | 市值下限 50亿→30亿 | +¥8,027 |
| 0613 | 止盈带 8/15 确认最优（10/18, 12/18, 12/20 均更差） | 不动 |
| 0613 | RS 板块过滤：全档位（0.5-0.8）均为负面 | 不用 |
| 0613 | AMBER 日入场：交易翻倍但总利润下降 | 不用 |
| 0613 | 最长持仓 7/14/20 天：均低于 10 天 | 不动 |
| 0613 | 板块分散选股取消：收益崩溃（+57K→+6K） | 不动 |
| 0613 | tp2 峰值回撤追踪（-3%/-5%/-7%）：均低于固定 15% | 不动 |
| 0613 | ¥100K 配置：3×33K（+¥85.7K）优于 4×25K（+¥62.5K）——加大小不加多 | 备忘 |

## 做法一句话

> 只在指数站稳 5/10 日均线（GREEN）的日子进场，买当日首板涨停里市值 30-500 亿、所在板块最热的股票。进场后 5 天内不涨到 +2% 就砍，跌超 ATR 止损线就砍，涨到 +8% 先落袋一半、+15% 清仓。3 个仓位同时跑，不做 T+1 一字板的票，不碰 5 连板以上的妖股。

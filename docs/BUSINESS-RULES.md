# 业务规则

## 使用者

通信计费稽核员使用本工作台处理重叠合并与分钟取整。

## 主规则

同一用户同一方向的重叠通话先合并为连续区间，再按每段六十秒向上取整。

计算以整数最小单位或十进制定点数为准，避免二进制浮点在边界上漂移。无法完成前置校验的行进入隔离区；跨行规则在计算后标记阻断或人工复核。

## 判定

- `PASS`：业务结果在允许范围内。
- `REVIEW`：数据可计算，但需要人工确认或存在轻微偏离。
- `REJECT`：存在来源、守恒、方向或上限阻断，不能签发。

## 批次

同一来源重复执行得到同一批次数值。签发要求阻断项为零，并携带当前版本号；旧版本提交会被拒绝。证据只在签发后生成。

## 携转交接结算（跨生效时刻）

- 同一用户同一方向的重叠通话仍先合并为连续区间，客户应收 = 合并区间整体向上取整分钟 × 分钟单价，**只收一次**。
- 合并区间跨过携转生效时刻 `effective_at` 时，按时刻切成两段：时刻前归原运营商（`OLD`/donor），时刻起归新运营商（`NEW`/recipient），两家只就各自实际承接的秒数结算。
- 客户的计费分钟按两段实际承接秒数用**最大余额法**分配，`old_minutes + new_minutes` 恰好等于客户计费分钟，不会因交接多出一分钟；金额（整数分）同理可用 `largest_remainder(customer_cents, [old_seconds, new_seconds])` 逐分分摊。
- 不变量：每个可归属区间 `old_cents + new_cents == customer_cents`；整批两家结算合计与客户应收 `difference_cents == 0`，对账到分。
- 缺少生效时刻、且话单没有可核对的运营商归属字段（`carrier`/`operator` 等）的区间或通话标记 `HELD`（原因 `EFFECTIVE_TIME_MISSING` + `ATTRIBUTION_UNVERIFIED`），**暂缓签发、不立结算分录**；同批其余区间照常 `ATTRIBUTED` 并签发。
- 交接账以 `batch_key`/`request_key` 唯一，分录对 `(batch_key, group_key, carrier)` 唯一；重跑同一批返回 `idempotent_hit=true`，分录数与金额不翻倍。批次签发（APPROVED）后按 `run:<run_id>` 幂等入账，可经 `GET /api/runs/{id}/settlements` 查询归属与金额。

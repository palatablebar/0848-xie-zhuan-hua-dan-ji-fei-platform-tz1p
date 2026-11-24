# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `GET /api/runs/{id}/evidence`：下载已签发证据。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。

## 携转交接结算

- `POST /api/handover/settle`（同义：`/api/handovers`、`/api/settlements`、`/api/settlement/settle` 等）：
  提交 `calls`（或 `rows`/`records`，也可用 `profile` 读取样例）、`effective_at` 或
  `effective_times`（用户 → 生效时刻）/`portings`，以及可选 `batch_key`/`request_key`、`preview`。
  返回合并区间归属、两家秒/分/金额、逐分对账、暂缓列表与幂等入账结果。
- `POST /api/handover/batches`：同上，签发并入账；相同 `batch_key` 重跑不重复入账。
- `GET /api/handover/batches/{batch_key}`、`GET /api/handover/settlements?batch_key=...`：查询批次归属、金额与分录。
- `POST /api/runs/{id}/handover`：按批次幂等入账（签发 APPROVED 时也会自动执行一次）。
- `GET /api/runs/{id}/settlements`（同义 `/handover`、`/attributions`）：工作台查询签发后的归属与金额。

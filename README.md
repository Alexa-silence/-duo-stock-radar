# Duo Stock Radar

GitHub Actions 按现有 exact-store 查询检查 8 家门店的 3 个机型，Bark 仍按 `state.json` 的门店＋机型状态去重。触发方式保持为 `workflow_dispatch`（由现有外部定时器调用）。

## 库存历史

根目录 `history.json` 是持久化的事件数组，按首次观察时间从旧到新排列。每个事件对应一个门店＋机型：

```json
{
  "storeId": "R389",
  "store": "浦东",
  "part": "MK2M4CH/A",
  "product": "256 白",
  "firstSeen": "2026-10-23T10:10:12+08:00",
  "lastSeen": "2026-10-23T10:14:05+08:00",
  "endedAt": "2026-10-23T10:15:09+08:00"
}
```

以上仅为格式示例，不是补录的真实库存。
- 首次检测到有货或已结束后再次有货：新增事件。
- 持续有货：仅更新该事件的 `lastSeen`。
- 确认无货：写入 `endedAt`，保留最后实际看到有货的 `lastSeen`；未结束时 `endedAt` 为 `null`。
- 超时、错误、缺少 exact-store 或未知机型状态：不更新该项历史，不结束事件。
- 时间采用北京时间（UTC+08:00），表示检测时间，并非精确上架/售罄时间。跨过未知状态的事件不能证明期间始终有货。
- 上线从空历史开始，不根据旧对话补录时间。如果上线时已经有货，首次成功观察建立事件，不更改既有 Bark 去重状态。

历史在 Bark 发送前写入，即使 Bark 失败也会提交历史；`state.json` 仍只在通知成功后更新，保留原有重试语义。测试 Bark 不读写库存文件。历史格式损坏时停止，避免覆盖原记录。

工作流串行运行且不取消正在运行的一轮；检出当前分支最新内容，将两份 JSON 一起提交。推送前 rebase；冲突会使任务失败，不强制覆盖远端数据。GitHub 可能合并/替换积压的待运行任务，因此每分钟调用不保证每分钟实际检测。

## 网页 / Worker 对接

本仓库没有监控网页或 Worker 源码。下一步需提供现有 Worker 源码，再接入同一份历史：

`https://raw.githubusercontent.com/Alexa-silence/-duo-stock-radar/main/history.json`

“最近一次放货”取 `firstSeen` 最大的事件；最近历史按 `firstSeen` 倒序取 20 条（可按同门店、同时间合并机型）。历史为空时显示“暂无记录”。读取失败时显示加载失败，不等同于无货。远端原始文件可能缓存，展示不保证即时更新。

## 离线验证

```sh
python3 -m unittest discover -s tests -v
```

测试直接执行工作流中实际的 Python，模拟 Apple 和 Bark，不访问真实接口或发送通知。

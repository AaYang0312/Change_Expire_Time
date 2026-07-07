# 智能门禁人脸过期时间批量修改

`batch_update_face_expiry.py` 现在只保留两个模式：

- `scan`：扫描 `query_face` 记录并导出 CSV。
- `update`：按 CSV 批量修改过期时间。

不带参数运行会进入交互菜单。

```bash
python3 batch_update_face_expiry.py
```

## 扫描导出 CSV

```bash
python3 batch_update_face_expiry.py scan \
  --cookie '这里替换为当前后台Cookie' \
  --scan-from 0 \
  --scan-limit 248 \
  --output faces_with_names.csv
```

扫描结果 CSV 字段：

```csv
query_id,per_id,per_name,mode,face_id,s_time,e_time
```

其中 `query_id` 是 `query_face` 使用的记录 ID，不一定等于 `per_id`。`per_name` 不能为空，否则不能批量写入。

## 按 CSV 批量修改

```bash
python3 batch_update_face_expiry.py update \
  --csv faces_with_names.csv \
  --cookie '这里替换为当前后台Cookie' \
  --e-time '2037-12-31 23:59:59'
```

脚本会先显示待写入数量和目标时间，再要求输入 `yes` 确认。确认后每个人会执行：

1. `query_face(id=query_id)` 重新读取完整人脸记录。
2. `update_face_ex` 保留原人脸数据，只替换 `e_time`。
3. `set_person_timeleave` 写入 CSV 中的 `mode`，默认是 `2`。

如果想跳过确认提示：

```bash
python3 batch_update_face_expiry.py update \
  --csv faces_with_names.csv \
  --cookie '这里替换为当前后台Cookie' \
  --e-time '2037-12-31 23:59:59' \
  --yes
```

## 时间格式

支持时间戳或日期字符串：

```bash
--e-time '2037-12-31 23:59:59'
--e-time '2037-12-31'
```

只写日期时按当天 `23:59:59` 处理。很远的年份可能被设备拒绝，建议先用 2037 年以内测试。

## 常见问题

- `per_name` 为空：重新扫描或手动补齐 CSV 的 `per_name` 列。
- `request failed`：检查网络、设备 IP、后台 Cookie 是否过期。
- `returned code=403`：通常是字段缺失、姓名为空、Cookie 失效或时间值设备不支持。

设备地址不是默认 `192.168.100.5` 时加 `--url`：

```bash
python3 batch_update_face_expiry.py scan \
  --url 'http://设备IP/face/http_req' \
  --cookie '这里替换为当前后台Cookie'
```

# 校对协议 1

`next` 返回 segment.start/end/text（[start,end)），上下文 readonly_before/after，raw_hash，owner_token。下标是 Python 风格 Unicode code point，不是 UTF-8 字节或 JavaScript UTF-16 code unit。含 emoji 时尤其不能用 JS 字符串索引直接代替。

submit payload 示例（其他通用字段仍由外层请求携带）：

```json
{
  "action":"submit",
  "work_id":1,
  "owner_token":"next 返回值",
  "raw_hash":"next 返回值",
  "start":0,
  "end":7,
  "edits":[{"start":2,"end":4,"old":"错字","replacement":"正字"}],
  "model":null
}
```

只有确实取得当前 Bot 模型标识才填 model。edit 是原稿绝对范围；依次递增、互不重叠、old 严格一致。删除/插入只允许标点和空白，词语替换两侧都非空且不超过 32 字。不确定修正则不提该 edit，不能用标点编辑绕过正文删除限制。

6000 字为每段上限，前后上下文各 300。原稿不改变；程序按原文复制所有未编辑内容，确认全部段后完成。结构覆盖不等于语义绝对正确，真实 Bot 的校对质量仍须抽样验收。

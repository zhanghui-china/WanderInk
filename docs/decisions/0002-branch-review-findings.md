# 0002 · 整分支评审(ultracode workflow)发现与处置

- **日期**:2026-07-09
- **方法**:Workflow 工具(ultracode),6 维度并行评审 + 每条发现派对抗代理证伪。30 代理,24 原始发现,确认 21 条(0 critical / 11 important / 10 minor)。
- **范围**:M0 + M1 至 S5(17 提交)。S6/CLI 未实现,不在范围。

## 处置策略

跨任务评审的价值在于抓到逐任务门禁看不到的**跨文件、跨步骤**问题(尤其 S3/S4/S5 续跑语义不一致)。按"是否清晰缺陷 + 修复成本 + 是否影响 T18 或核心迭代工作流"分三档:

### A. 现在修(FIX-BATCH-1,清晰高价值)

| # | 文件:行 | 问题 | 修法 |
|---|---|---|---|
| 1 | llm.py:38,41 | `chat()` 在 try 外 + except 只抓 (ValidationError,ValueError);null content/HTTP错误/结构缺失 → TypeError/HTTPError 逃逸,不重试不包 LLMError | chat 移入 try;捕获 httpx.HTTPError 与解析异常,重试并最终统一抛 LLMError |
| 2 | tts.py:17 | 任意 2xx 直接 write_bytes,错误体/空体当 mp3 落盘(**T18 会命中**) | 校验 content-type/大小,非音频抛 TTSError 带 body 摘要 |
| 3 | s4_pages.py:42 | 参考图预处理在 per-page try 外,单张坏图崩整轮 | _downscaled_ref 移入 try |
| 4 | s4_pages.py:38 | 续跑跳过只看 status,不看页面文件在不在(S5 查了) | 跳过条件加 `and (workdir/cell.image).exists()` |
| 5 | s3_characters.py:34 | `locked=True` 设了从不读,重跑覆盖已定角色 | locked 且 turnaround 文件在 → 跳过该角色重绘 |
| 6 | s4_pages.py:22 | _downscaled_ref 按文件名缓存永不失效,S3 重绘后用旧缩图 → 破坏一致性 | 缓存键加 mtime 校验:src 更新则重建缩图 |
| 7 | typeset.py:43 | AI 水印半透明白字无描边,叠留白/浅色画面可能不可见(合规:标识不可失效) | 加 stroke_fill 深色描边或半透明底衬 |
| 8 | image.py:37 | _via_generations/_via_edits 裸取 data[0],空 data/错误体抛 IndexError/KeyError 非 ImageGenError | 加长度/键防护,抛 ImageGenError |
| 9 | image.py:61 | _via_chat 只认 data: URI,漏 http(s) 图片 URL | images[] 里遇 http url 走 _decode 下载 |
| 10 | test_s5.py | BGM 匹配用单 track 测无判别力;S5 续跑跳过零覆盖 | 加 ≥2 情绪 track 的匹配测试 + skip 测试 |

### B. 延后到 T17/后续(结构性,需刻意设计)

- **s3_characters.py:29 三视图按列表下标 i<4 选,非重要度**:主角排在 index≥4 则拿不到参考图。修复需 S1 保证主角排前(prompt 约束)或 schema 加 importance 字段。→ 在 T17 前给 S1 prompt 加"characters 按重要度排序、主角在前"的轻量约束;是否加字段留待第 2 期。
- **s4_pages.py:46 / s3 / s5 循环内不落盘**:中途崩溃丢内存态 confirmed。修复需改 run() 签名加 save 回调或 workdir 级增量落盘,跨 3 文件。→ T17 CLI 编排定型时一并决定(MVP 单进程跑完即存,崩溃恢复为第 2 期)。

### C. 记录不修(Minor,MVP 可接受)

- s2_storyboard.py:28 S1/S2 重跑覆盖下游产物(需从 S2 重入才触发,CLI 未建)
- image.py:19 provider 无 close()(仅探测脚本循环泄漏,单次运行无影响)
- test_image_provider.py chat content-regex 回退分支未覆盖
- test_typeset.py ResourceWarning 噪音(仅 -W default 显现)
- test_s0.py from_text 未覆盖

## 备注

完整 21 条(含每条对抗验证 reason)见 workflow 输出:tasks/w034hu3ap.output。

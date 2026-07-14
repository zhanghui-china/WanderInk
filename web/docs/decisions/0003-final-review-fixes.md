# 0003 · 最终整分支复审(Workflow)发现与处置

- **日期**:2026-07-09
- **方法**:finish-m1 Workflow 第 3 阶段,5 维度并行复审 S0–S6+CLI 全分支 + 对抗验证。确认 22 条(0 critical / 11 important / 11 minor)。
- **背景**:S6(T16)、CLI(T17)已由 Workflow 构建 + 各自评审修复(S6 修 2、CLI 修 2)后提交。

## A. 现在修(FIX-BATCH-2)

清晰缺陷 + 影响 T18 端到端或 PRD 合规硬要求:

| # | 文件:行 | 问题 | 修法 |
|---|---|---|---|
| A1 | cli.py:26,29 | --minutes/--audience/--tone/--style 不校验,非法值绕过 Literal 枚举写进 project.json → 后续 WORD_TARGETS/STYLE_PRESETS[bad] KeyError 或 load 时 ValidationError 使项目永久不可加载 | 4 个参数在建/存项目前用 typer Choice 或显式校验,非法值 raise typer.BadParameter 快速失败 |
| A2 | cli.py:126 | `run` 不看每步 status,S4 全失败/S6 无正文页仍打印"成片"报成功 | 每步后检查 status,partial/failed 明确告警;结尾若无 confirmed 正文页则报失败而非成功 |
| A3 | ffmpeg.py:10 | sh() check=True+capture_output 但从不暴露 stderr,ffmpeg 失败丢诊断 | CalledProcessError 时抛出带 stderr 的异常 |
| A4 | ffmpeg.py:18 | probe_duration_ms 对 'N/A'/空输出 int(float()) 抛裸 ValueError | 守卫 N/A/空 → 抛清晰错误 |
| A5 | s6_compose.py:27 | 已确认页只校验路径字符串非空,不校验文件真在 → 缺产物页崩整个 S6 | 跳过条件加 `(workdir/image).exists() and (workdir/audio).exists()`(对齐 S4/S5) |
| A6 | s6_compose.py:18,20 | 片尾不渲染 source_type:原创演绎被冠"传说来源"包装成真传说(违反 PRD F0②);sources 为空时零来源标注(违反 §9.4) | 片尾按 source_type 标注:原创演绎显式标"原创演绎";sources 空时至少标类型;始终有来源/演绎标注行 |
| A7 | s5_audio.py:20 | S5 无 per-page 失败隔离,单页 TTS 失败即整步抛出(与 S4 不自洽;**T18 必中**——key 无 TTS 模型) | 每页 synthesize 包 try;失败留 audio="" 并告警继续;status["s5"]="done" if 全部有音频 else "partial"。不复用 cell.status(那是 S4 的) |
| A8 | s4_pages.py:30 | `step` 不强制步骤顺序,S4 前置守卫只查 script+storyboard,不查 S3 是否产出三视图 → 静默绕过一致性机制 | S4 若无任何角色有 turnaround_image,告警(M0 一致性被绕过)后继续 |
| A9 | 测试 | run/step 零覆盖(#10)、S6 跳过分支未测(#11)、S5 缺文件重合成分支未测(#20) | 补 run/step 基础用例(mock steps)、S6 跳过分支、S5 重入用例 |

## B. 延后到第 2 期(结构性/需刻意设计)

- **#16 S3 第 5+ 角色 feature_prompt 非幂等重跑**:与 0002 文档 B 档"三视图按重要度选"同源,一并留待引入角色重要度机制时解决。
- **#12 run(原地改)vs step(rebind)模式不一致**:低价值一致性问题,统一签名时再说。

## C. 记录不修(Minor,MVP 可接受)

- #13 concat 单引号路径未转义(project id 为 hex、文件名固定,实际无撇号)
- #15 全跳过时产空片(已由 A2 在 run 层告警覆盖)
- #17 from_text 成功路径未测、#18 concat_cmd 未测、#19 s2 页数断言是 mock 回显、#21 silent 分支时长未断言、#22 s2/s3 script-None 守卫未测

## 备注
完整 22 条(含每条对抗验证 reason)见 tasks/w8jaxgecp.output。

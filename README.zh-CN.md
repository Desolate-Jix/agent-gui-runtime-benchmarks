# Agent GUI Runtime — benchmark 备份

[English](README.md)

这是 [Agent Review Instant](https://github.com/Desolate-Jix/agent-gui-runtime) 的独立公开证据备份。执行安装器不包含、也不自动下载本仓库。需要数据时手动克隆。

## 冻结数据集

`real-gui-20261015-v2` 包含 **39 张唯一原图、30 个界面族、254 个标注案例**。分为训练 104、验证 70、保留 80 例；类别为输入框 56、图形／控件／链接 95、页面结果 75、输入文字 28 例。标签经 AI 复核，并非人工真值。同图有多个案例；suite-v1 与 suite-v2 是历史版本，不应相加冒充独立样本。

## 包含的证据

- `evidence/decision-benchmark-20261015-01`：两份冻结套件、原图和标注图、标注、请求清单、原始回复、训练／验证／保留集／级联结果、冻结选择、用量账本与审计。
- `evidence/decision-benchmark-collection-20261015-03` 与 `-04`：补充采集记录和保留原图。原有来源保留审计的失败记录照常保存，不因纳入备份就改称采集成功。
- `SNAPSHOT.json`：逐文件大小、SHA256、排除清单和源码引用。只排除缓存、临时进程锁及生成的启动快捷方式；用量账本与完整性检查点成对保留，原始数据与证据保持原字节。

部分原始出处字段保留采集时的 Windows 路径。冻结套件的图片和请求采用相对路径，可以从本仓库读取；根据旧绝对路径重建标注时需要映射路径。没有悄悄改写原始记录。

## 克隆后核验

克隆参数为本仓库启用 Windows 长路径支持，不改全局 Git 设置。

```sh
git clone -c core.longpaths=true https://github.com/Desolate-Jix/agent-gui-runtime-benchmarks.git
cd agent-gui-runtime-benchmarks
python -B verify_snapshot.py
```

核验只读文件，不运行评测、不启动桌面自动化、不下载模型权重，也不调用 API。评测代码与公开方法见 runtime 仓库，本备份引用源码提交 `acd762ce392aee76682fea4d995d0f2ba94ef285`。真实 API 实验是另一项可能产生费用的操作。

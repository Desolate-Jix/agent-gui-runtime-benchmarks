# Agent GUI Runtime — benchmark backup

[简体中文](README.zh-CN.md)

Public, separately downloaded evidence backup for [Agent Review Instant](https://github.com/Desolate-Jix/agent-gui-runtime). The execution installer does not include or automatically download this repository. Clone it manually only when the data is needed.

## Frozen dataset

`real-gui-20261015-v2` contains **39 unique original screenshots, 30 interface families and 254 labelled cases**. Splits are 104 training, 70 validation and 80 held-out cases. Categories are 56 input-field, 95 graphic/control/link, 75 page-result and 28 text-value cases. Labels were reviewed by AI, not human ground truth. Multiple cases share screenshots; suite-v1 and suite-v2 are historical versions and must not be added as independent samples.

## Included evidence

- `evidence/decision-benchmark-20261015-01`: both frozen suites, original/marked images, annotations, request manifests, raw replies, train/validation/test/cascade results, frozen selections, usage accounting and audits.
- `evidence/decision-benchmark-collection-20261015-03` and `-04`: supplemental collection records and retained source images. Historical source-retention audit failures remain in the backup; inclusion does not turn them into successful collection claims.
- `SNAPSHOT.json`: file sizes and SHA256 hashes, exclusions and source-code reference. Cache files, transient process locks and generated launch shortcuts are excluded. The budget integrity checkpoint is retained with its journal; original dataset and evidence bytes are retained.

Some original provenance fields contain capture-time Windows paths. The frozen suite uses relative asset/request paths and can be read from this checkout; rebuilding annotations against their old absolute source paths requires remapping. Original records have not been silently rewritten.

## Verify after cloning

The clone option enables long evidence filenames on Windows without changing global Git settings.

```sh
git clone -c core.longpaths=true https://github.com/Desolate-Jix/agent-gui-runtime-benchmarks.git
cd agent-gui-runtime-benchmarks
python -B verify_snapshot.py
```

Verification reads files only. It does not run an evaluation, start desktop automation, download model weights or call an API. Evaluation code and published methodology are in the runtime repository; this backup references source commit `acd762ce392aee76682fea4d995d0f2ba94ef285`. Running an API experiment is a separate, potentially paid operation.

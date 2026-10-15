"""只用项目维护接口采集公开页面；不调用模型、不输入表单。"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
from time import perf_counter

ROOT = Path('C:/Users/DesolateJix/.codex/worktrees/decision-api/agent-gui-runtime')
OUT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT))
from scripts.execution_client import ExecutionClient


def save(name, value):
    path = OUT / name
    if path.exists():
        raise ValueError('collection_receipt_already_exists')
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


async def main():
    save('started.json', {'source': str(ROOT), 'model_calls': 0, 'profile_preserved': True})
    client = ExecutionClient(root=ROOT, data_root=OUT/'data', recognition_source='agent_current', allow_actions=True)
    async with client as c:
        save('ready.json', {'session_directory': c.session_directory})
        for index, (name, url) in enumerate(json.loads((OUT/'urls.json').read_text(encoding='utf-8'))):
            started = perf_counter()
            launch = await c.execute({'request_id': f'collect-{index}-launch', 'detail': 'full',
                'command': {'kind': 'launch', 'path': str(OUT/(name+'.lnk')), 'prefer_existing': False}})
            save(name+'-launch.json', launch)
            result = launch.get('result') or {}
            if launch.get('operation_succeeded') is not True or result.get('status') != 'launched_window_ready':
                print(json.dumps({'name': name, 'status': 'launch_not_ready', 'stopped': True}), flush=True)
                break
            window = result['window']
            for sample in range(2):
                await asyncio.sleep(3)
                capture = await c.execute({'request_id': f'collect-{index}-capture-{sample}', 'detail': 'full',
                    'command': {'kind': 'capture'}})
                save(name+f'-capture-{sample}.json', capture)
                if capture.get('operation_succeeded') is not True:
                    print(json.dumps({'name': name, 'status': 'capture_failed', 'sample': sample}), flush=True)
                else:
                    # 先归档原字节再采下一张，不能依赖运行时短期保留目录。
                    observation = capture['observation']
                    source = Path(observation['image_path'])
                    data = source.read_bytes()
                    if sha256(data).hexdigest() != observation['sha256']:
                        raise ValueError('collection_source_hash_mismatch')
                    retained = OUT/'retained-sources'/(name+f'-capture-{sample}.png')
                    retained.parent.mkdir(exist_ok=True)
                    with retained.open('xb') as stream:
                        stream.write(data)
                    save(name+f'-retained-{sample}.json', {'source': str(source), 'retained': str(retained),
                        'sha256': observation['sha256'], 'capture_receipt': name+f'-capture-{sample}.json'})
            closed = await c.execute({'request_id': f'collect-{index}-close', 'detail': 'full',
                'command': {'kind': 'close_launched_window', 'handle': window['handle'], 'process_id': window['process_id']}})
            save(name+'-close.json', closed)
            status = (closed.get('result') or {}).get('status')
            print(json.dumps({'name': name, 'close_status': status, 'wall_s': perf_counter()-started,
                'model_calls': 0}), flush=True)
            if status != 'window_closed':
                print(json.dumps({'stopped': True, 'reason': 'close_not_verified'}), flush=True)
                break
    save('finished.json', {'cleanup_verified': client.status.get('cleanup_verified') is True,
        'model_calls': 0, 'profile_preserved': True})


if __name__ == '__main__':
    asyncio.run(main())

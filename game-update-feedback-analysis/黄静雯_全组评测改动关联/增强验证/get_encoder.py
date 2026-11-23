"""获取作者指定修订的量化模型和分词器，逐字节校验。默认缓存在 E 盘。"""
import argparse
import hashlib
import os
from pathlib import Path
import time
import requests

REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
CACHE_DIR = Path('E:/CodexData/cache/steam-feedback-models') / ('multilingual-e5-small-' + REVISION[:10])
BUNDLED_DIR = Path(__file__).resolve().parent/'编码器'
DEFAULT_DIR = Path(os.environ.get('STEAM_E5_DIR',BUNDLED_DIR if BUNDLED_DIR.is_dir() else CACHE_DIR))
FILES = {
    'model_qint8_avx512_vnni.onnx': ('dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88',118346824),
    'tokenizer.json': ('0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39',17082730),
}

def checksum(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def fetch(dest):
    dest.mkdir(parents=True,exist_ok=True)
    for name,(expected,size) in FILES.items():
        path=dest/name
        if path.exists() and checksum(path)==expected:continue
        url=f'https://huggingface.co/intfloat/multilingual-e5-small/resolve/{REVISION}/onnx/{name}'
        tmp=path.with_suffix(path.suffix+'.part')
        for attempt in range(3):
            try:
                with requests.get(url,stream=True,timeout=(12,45)) as res:
                    res.raise_for_status()
                    with tmp.open('wb') as out:
                        for block in res.iter_content(1024*1024):out.write(block)
                if tmp.stat().st_size!=size or checksum(tmp)!=expected:raise ValueError('模型文件不完整')
                tmp.replace(path)
                print(name,size,flush=True);break
            except (requests.RequestException,ValueError):
                if attempt==2:raise
                time.sleep(2+attempt)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--dir',type=Path,default=DEFAULT_DIR)
    fetch(parser.parse_args().dir)

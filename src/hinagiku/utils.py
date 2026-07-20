# -*- coding: utf-8 -*-
"""JSONL(gzip)入出力・ログ・進捗表示のユーティリティ。"""
import gzip
import json
import logging
import os


def setup_logger(name, logfile=None):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if logfile:
        os.makedirs(os.path.dirname(logfile), exist_ok=True)
        fh = logging.FileHandler(logfile, encoding='utf-8')
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


def progress(iterable, total=None, desc=''):
    """tqdm があれば使い、無ければそのまま返す。"""
    try:
        from tqdm import tqdm
        return tqdm(iterable, total=total, desc=desc)
    except ImportError:
        return iterable


def write_jsonl(path, records, gzip_out=True):
    """records(iterable of dict) を JSONL (必要なら gzip) で書き出す。"""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    opener = gzip.open if gzip_out else open
    n = 0
    with opener(path, 'wt', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
            n += 1
    return n


def read_jsonl(path):
    """JSONL (.gz 自動判別) を読み込む generator。"""
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)

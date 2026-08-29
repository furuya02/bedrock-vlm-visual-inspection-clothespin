"""Bedrock Converse API の薄いラッパー

モデルごとの差異を 1 か所に閉じ込める。特に temperature の扱いに注意が必要で、
Claude Opus 4.7 以降 / Opus 5 / Sonnet 5 では temperature が廃止されており、
渡すと ValidationException になる。

    ValidationException: `temperature` is deprecated for this model.
"""

import json
import re
import time
from pathlib import Path

import boto3
from botocore.config import Config

REGION = "ap-northeast-1"

# botocore の既定 read_timeout は 60 秒。
# 仕様生成（Opus 5 に画像を複数枚渡す）は実測 47〜111 秒かかるため、既定のままだと
# ReadTimeoutError で落ちる。読み取りだけ長めに取り、リトライは自前の1回に留める。
_BOTO_CONFIG = Config(
    connect_timeout=10,
    read_timeout=600,
    retries={"max_attempts": 1, "mode": "standard"},
)

# 欠陥仕様書の生成に使う強いモデル（1 検査対象あたり 1 回だけ呼ぶ）
# Opus 5 / Sonnet 5 は global. プロファイルのみで jp.（国内ルーティング）が存在しない。
# 国内処理が要件になる場合は jp.anthropic.claude-opus-4-8 に差し替える。
GENERATOR_MODEL = "global.anthropic.claude-opus-5"

# 毎回の検査に使う安いモデル
INSPECTOR_MODEL = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
INSPECTOR_MODEL_ALT = "jp.amazon.nova-2-lite-v1:0"

# temperature を受け付けないモデル（渡すと ValidationException）
_NO_TEMPERATURE = re.compile(r"anthropic\.claude-(opus-4-7|opus-4-8|opus-5|sonnet-5|fable-5)")

_client = boto3.client("bedrock-runtime", region_name=REGION, config=_BOTO_CONFIG)


def image_block(path):
    """ローカル画像を Converse API の image ブロックにする"""
    path = Path(path)
    fmt = "png" if path.suffix.lower() == ".png" else "jpeg"
    return {"image": {"format": fmt, "source": {"bytes": path.read_bytes()}}}


def converse(model_id, content, max_tokens=4096, temperature=0):
    """1 ターンだけ投げて (テキスト, メタ情報) を返す"""
    cfg = {"maxTokens": max_tokens}
    if temperature is not None and not _NO_TEMPERATURE.search(model_id):
        cfg["temperature"] = temperature

    t0 = time.time()
    res = _client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": content}],
        inferenceConfig=cfg,
    )
    elapsed = time.time() - t0

    text = "".join(b.get("text", "") for b in res["output"]["message"]["content"])
    usage = res.get("usage", {})
    meta = {
        "model_id": model_id,
        "seconds": round(elapsed, 2),
        "input_tokens": usage.get("inputTokens"),
        "output_tokens": usage.get("outputTokens"),
        "temperature_sent": "temperature" in cfg,
    }
    return text, meta


def parse_json(text):
    """モデルが返した JSON を読む

    Nova 2 Lite は temperature=0 でも壊れた JSON を返すことがある
    （数値の後ろにだけ " が付くなど）。素直に読めなければ最外の {...} を拾い直す。
    """
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    raw = (fenced.group(1) if fenced else text).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    brace = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass

    # 数値を囲む壊れたクォートを落としてから読み直す
    repaired = re.sub(r'(?<=[\[,\s])"(\d+)"(?=[\],\s])', r"\1", raw)
    repaired = re.sub(r'(?<=\d)"(?=\s*[,\]])', "", repaired)
    return json.loads(repaired)

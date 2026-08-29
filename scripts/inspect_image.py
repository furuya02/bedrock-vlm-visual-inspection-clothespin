"""生成済みの検査プロンプトを使って画像を検査する

generate_spec.py が作った spec.json を読み、検査画像にグリッドを焼き込んでから
参照画像（正常・異常）と一緒にモデルへ投げる。判定は OK / REVIEW / NG の 3 値。

VLM の出力は非決定的なので（temperature を 0 にしても座標や判定が揺れるし、
新しい Claude では temperature 指定自体ができない）、既定で 3 回投げて多数決を取る。

Usage:
    python inspect_image.py --spec spec.json --image images/defect/neu_scratches_1.jpg
    python inspect_image.py --spec spec.json --image X.jpg --model jp.amazon.nova-2-lite-v1:0 --runs 3
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from PIL import Image

import bedrock
from add_grid import add_grid

VERDICTS = ("OK", "REVIEW", "NG")


def build_content(spec, image_path, grid_dir, refs="normal"):
    """参照画像 → 検査画像 → 生成済みプロンプト の順で content を組む

    refs は同梱する参照画像の選び方。
      both   … 正常＋欠陥サンプルを両方渡す
      normal … 正常サンプルだけ渡す（既定）
      none   … 参照画像を渡さない（プロンプトの記述だけで判定させる）

    欠陥サンプルを渡すと、モデルが**参照画像の欠陥を検査画像に投影して誤検知する**
    ことがある。既定を normal にしているのはそのため。
    """
    meta = spec["_meta"]
    cols, rows = meta["cols"], meta["rows"]

    content = []

    # 正常が何かを示さないと誤検知が増えるので、正常サンプルは原則同梱する
    if refs in ("both", "normal"):
        content.append({"text": "=== 参照: 正常サンプル（これは検査対象ではありません）==="})
        for p in meta["normal_images"]:
            content.append(bedrock.image_block(p))
    if refs == "both":
        content.append({"text": "=== 参照: 欠陥サンプル（これは検査対象ではありません）==="})
        for p in meta["defect_images"]:
            content.append(bedrock.image_block(p))

    # 検査画像にはグリッドを焼き込んでから渡す
    src = Path(image_path)
    grid_path = Path(grid_dir) / f"{src.stem}_grid{cols}x{rows}.png"
    grid_path.parent.mkdir(parents=True, exist_ok=True)
    add_grid(Image.open(src), cols, rows).save(grid_path)

    content.append({"text": f"=== ここから検査画像です。この画像だけを判定してください（{cols}x{rows} グリッド焼き込み済み）==="})
    content.append(bedrock.image_block(grid_path))

    schema = json.dumps(spec["output_schema"], ensure_ascii=False)
    content.append({"text": f"{spec['inspection_prompt']}\n\n【JSON スキーマ】\n{schema}"})

    return content, grid_path


def vote(results, runs):
    """多数決。過半数に届かない判定は安全側の REVIEW に倒す"""
    verdicts = [r["verdict"] for r in results if r]
    if not verdicts:
        return {"verdict": "REVIEW", "cells": [], "reason": "全ての試行が失敗"}

    counts = Counter(verdicts)
    top, n = counts.most_common(1)[0]
    verdict = top if n > runs / 2 else "REVIEW"

    # セルは過半数の試行で挙がったものだけ採用する
    cell_counts = Counter(
        c for r in results if r for f in r.get("findings", []) for c in f.get("cells", [])
    )
    cells = sorted(c for c, n in cell_counts.items() if n > len(verdicts) / 2)

    return {
        "verdict": verdict,
        "cells": cells,
        "verdict_counts": dict(counts),
        "cell_counts": dict(cell_counts),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--spec", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--model", default=bedrock.INSPECTOR_MODEL)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--refs", choices=["both", "normal", "none"], default="normal")
    p.add_argument("--grid-dir", default="out/grid")
    p.add_argument("--out")
    args = p.parse_args()

    spec = json.loads(Path(args.spec).read_text())
    content, grid_path = build_content(spec, args.image, args.grid_dir, args.refs)

    results, metas = [], []
    for i in range(args.runs):
        text, meta = bedrock.converse(args.model, content, max_tokens=2048)
        metas.append(meta)
        try:
            results.append(bedrock.parse_json(text))
        except Exception as e:
            print(f"  run {i + 1}: JSON パース失敗 ({type(e).__name__})")
            results.append(None)

    final = vote(results, args.runs)

    print(f"image  : {args.image}")
    print(f"grid   : {grid_path}")
    print(f"model  : {args.model}   refs={args.refs}")
    print(f"runs   : {args.runs}  "
          f"(avg {sum(m['seconds'] for m in metas) / len(metas):.2f}s, "
          f"in={metas[0]['input_tokens']}, out={sum(m['output_tokens'] for m in metas)})")
    print(f"parse  : {sum(1 for r in results if r)}/{args.runs} 成功")
    print(f"VERDICT: {final['verdict']}   cells={final['cells']}")
    print(f"  内訳 : {final.get('verdict_counts')}")
    print(f"  セル : {final.get('cell_counts')}")
    for r in results:
        if r:
            for f in r.get("findings", []):
                print(f"    - {f.get('defect_type')} @ {f.get('cells')} "
                      f"conf={f.get('confidence')}")

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {"final": final, "runs": results, "meta": metas},
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"saved  : {args.out}")


if __name__ == "__main__":
    main()

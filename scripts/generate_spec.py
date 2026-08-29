"""異常画像と正常画像から「欠陥仕様書」と「検査プロンプト」を VLM 自身に生成させる

外観検査で最初に詰まるのは「何を検査キーワードにするか」を人が決められないこと。
前記事では検出語の選び方で結果が大きく変わった（patch → 0 件 / dark spot → 2 件）。
そこで、その工程ごと VLM に肩代わりさせる。

Usage:
    python generate_spec.py --defect images/defect/a.jpg images/defect/b.jpg \
                            --normal images/normal/x.jpg \
                            --cols 3 --rows 3 --out spec.json
"""

import argparse
import json
import string
from pathlib import Path

import bedrock

META_PROMPT = """あなたは製造業の外観検査の専門家です。

これから、ある製品の「欠陥サンプル画像」と「正常サンプル画像」をお見せします。
これらを見比べて、この製品の外観検査を自動化するための資料一式を作成してください。

## 画像の内訳
- 欠陥サンプル: {n_defect} 枚（先に提示します）
- 正常サンプル: {n_normal} 枚（後に提示します）

## 検査画像には {cols}x{rows} のグリッドが焼き込まれています
列は左から {col_labels}、行は上から {row_labels} です。左上のセルが {first_cell}。
位置は必ずこのセル名で表現してください。ピクセル座標や比率は使わないでください。
有効なセル名は次の {n_cells} 個だけです: {cell_list}

## この製品について（重要）

検査対象は白い樹脂製の洗濯ばさみ（射出成形品）です。次の 2 点に注意してください。

1. 表面の欠陥（汚れ・傷）だけでなく、**形の異常**（変形・曲がり・破損）も含みます。
   形の異常を判定するには「正常な形がどういうものか」の記述が不可欠です。

2. **この製品はバネで開閉する可動品です。**
   したがって **2 本の脚の開き具合は、正常品でも大きく変わります。**
   開きが広い／狭いことは欠陥ではありません。
   金属リング（バネ）の見え方・位置も、置き方や開き具合によって変わります。
   判定すべきは「開き具合」ではなく、
   **樹脂部品そのものが壊れている・歪んでいる・欠けているか**です。

## 出力してほしいもの

以下の 4 つを、指定した JSON 形式で出力してください。

1. defect_types: 見つかった欠陥の種類。各要素は以下を含むこと
   - name: 欠陥の呼び名。**分類の専門用語ではなく、見た目をそのまま表す平易な語**にすること
     （例: 「介在物」ではなく「濃い色の筋」、「パッチ」ではなく「暗い斑点」）
   - appearance: 色・形状・テクスチャ・輪郭の性質を具体的に
   - confusable_with: 正常な模様・光沢・映り込みのうち、この欠陥と誤認しやすいもの

2. normal_characteristics: 正常品がどう見えるか（誤検知を防ぐための基準）
   - 表面の見え方だけでなく、**全体形状・左右の対称性・
     各部位（先端／つまみ部／バネ部／脚）の関係**も記述すること
   - 形の異常を判定するには「正常な形」の定義が必須。ここを最も丁寧に書くこと

3. inspection_prompt: 上記を踏まえた検査用プロンプト。**そのまま別のモデルに渡して使える完成品**
   にすること。以下を必ず含めること
   - この製品で何を探すべきか
   - 何を欠陥と見なさないか（次を必ず含めること）
     ・被写体の下や横に伸びる影
     ・背景に一様に分布する細かい粒状ノイズ、画面四隅の照明由来の暗さ
     ・被写体全体の傾き・回転（何度傾いていても、それ自体は欠陥ではない）
     ・**2 本の脚の開き具合の違い**（可動部の正常な変動）
     ・金属リングの見え方・位置の違い
   - グリッドのセル名で位置を答えること
   - 判定は OK / REVIEW / NG の 3 値であること
     （OK=欠陥なし、REVIEW=判断がつかないので人が目視する、NG=明らかな欠陥あり）
   - 迷ったら NG ではなく REVIEW にすること

4. output_schema: inspection_prompt を使ったときにモデルが返すべき JSON スキーマ。
   最低限 verdict（OK/REVIEW/NG）、findings（欠陥種別・セル名・確信度・根拠）を含めること

## 出力形式

マークダウンのコードブロックや説明文を付けず、純粋な JSON だけを返してください。

{{
  "defect_types": [
    {{"name": "...", "appearance": "...", "confusable_with": "..."}}
  ],
  "normal_characteristics": "...",
  "inspection_prompt": "...",
  "output_schema": {{ ... }}
}}"""


def cell_list(cols, rows):
    return [f"{string.ascii_uppercase[c]}{r + 1}" for r in range(rows) for c in range(cols)]


def build_content(defect_paths, normal_paths, cols, rows):
    """画像ブロックとメタプロンプトを 1 つの content 配列に組む"""
    content = []

    content.append({"text": f"=== 欠陥サンプル {len(defect_paths)} 枚 ==="})
    for p in defect_paths:
        content.append(bedrock.image_block(p))

    content.append({"text": f"=== 正常サンプル {len(normal_paths)} 枚 ==="})
    for p in normal_paths:
        content.append(bedrock.image_block(p))

    cells = cell_list(cols, rows)
    content.append(
        {
            "text": META_PROMPT.format(
                n_defect=len(defect_paths),
                n_normal=len(normal_paths),
                cols=cols,
                rows=rows,
                col_labels=", ".join(string.ascii_uppercase[:cols]),
                row_labels=", ".join(str(r + 1) for r in range(rows)),
                first_cell=cells[0],
                n_cells=len(cells),
                cell_list=", ".join(cells),
            )
        }
    )
    return content


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--defect", nargs="+", required=True)
    p.add_argument("--normal", nargs="+", required=True)
    p.add_argument("--cols", type=int, default=3)
    p.add_argument("--rows", type=int, default=3)
    p.add_argument("--model", default=bedrock.GENERATOR_MODEL)
    p.add_argument("--out", default="spec.json")
    args = p.parse_args()

    content = build_content(args.defect, args.normal, args.cols, args.rows)
    text, meta = bedrock.converse(args.model, content, max_tokens=8192)
    spec = bedrock.parse_json(text)

    spec["_meta"] = {
        **meta,
        "cols": args.cols,
        "rows": args.rows,
        "defect_images": [str(x) for x in args.defect],
        "normal_images": [str(x) for x in args.normal],
    }

    Path(args.out).write_text(json.dumps(spec, ensure_ascii=False, indent=2))

    print(f"saved: {args.out}")
    print(f"  model   : {meta['model_id']}")
    print(f"  elapsed : {meta['seconds']}s")
    print(f"  tokens  : in={meta['input_tokens']} out={meta['output_tokens']}")
    print(f"  defects : {[d['name'] for d in spec.get('defect_types', [])]}")


if __name__ == "__main__":
    main()

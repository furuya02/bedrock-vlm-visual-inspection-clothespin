"""画像にグリッド線とセルラベル（A1, B2 ...）を焼き込む

VLM にバウンディングボックスの座標を返させると位置がずれるため、
「どのセルか」で答えさせる。そのための前処理。

列は左から A, B, C ...、行は上から 1, 2, 3 ...。左上のセルが A1。

Usage:
    python add_grid.py <image_path> [--cols 3] [--rows 3] [--out OUT]

Example:
    python add_grid.py images/defect/neu_scratches_1.jpg --cols 3 --rows 3
    python add_grid.py images/defect/sample_part_rust.jpg --cols 4 --rows 4
"""

import argparse
import string
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LINE_COLOR = (255, 60, 60)
LABEL_BG = (255, 60, 60)
LABEL_FG = (255, 255, 255)


def cell_names(cols, rows):
    """(col, row) -> 'A1' のセル名を左上から順に返す"""
    return [
        (c, r, f"{string.ascii_uppercase[c]}{r + 1}")
        for r in range(rows)
        for c in range(cols)
    ]


def add_grid(image, cols=3, rows=3):
    """グリッド線とセルラベルを焼き込んだ新しい画像を返す"""
    image = image.convert("RGB").copy()
    w, h = image.size
    draw = ImageDraw.Draw(image)

    # 画像サイズに対して線とフォントを相対的に決める（小さい画像でも読める太さにする）
    # ラベルを大きくしすぎると欠陥そのものを覆い隠すため、セル幅の 1/6 程度に抑える
    line_w = max(2, round(min(w, h) / 300))
    font_size = max(12, round(min(w, h) / (max(cols, rows) * 6)))
    font = ImageFont.load_default(size=font_size)

    cw, ch = w / cols, h / rows

    for c in range(1, cols):
        x = round(c * cw)
        draw.line([(x, 0), (x, h)], fill=LINE_COLOR, width=line_w)
    for r in range(1, rows):
        y = round(r * ch)
        draw.line([(0, y), (w, y)], fill=LINE_COLOR, width=line_w)

    pad = max(2, line_w)
    for c, r, name in cell_names(cols, rows):
        x, y = c * cw + pad, r * ch + pad
        box = draw.textbbox((x, y), name, font=font)
        draw.rectangle(
            [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad], fill=LABEL_BG
        )
        draw.text((x, y), name, fill=LABEL_FG, font=font)

    return image


def main():
    p = argparse.ArgumentParser()
    p.add_argument("image")
    p.add_argument("--cols", type=int, default=3)
    p.add_argument("--rows", type=int, default=3)
    p.add_argument("--out")
    args = p.parse_args()

    src = Path(args.image)
    out = Path(args.out) if args.out else src.with_name(
        f"{src.stem}_grid{args.cols}x{args.rows}.png"
    )

    add_grid(Image.open(src), args.cols, args.rows).save(out)
    print(f"saved: {out} ({args.cols}x{args.rows})")


if __name__ == "__main__":
    main()

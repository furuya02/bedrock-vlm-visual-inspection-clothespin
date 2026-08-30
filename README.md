# bedrock-vlm-visual-inspection-clothespin

Running VLM visual inspection on something that is **not** a curated benchmark:
clothespins from a 100-yen shop. Includes the photos, the generated inspection spec,
and every judgment result as JSON.

[日本語](README.ja.md)

---

## What this is

This applies [bedrock-vlm-visual-inspection-quickstart](https://github.com/furuya02/bedrock-vlm-visual-inspection-quickstart)
to a subject that was unknown to me as well. The previous subject was NEU-DET, a public
dataset of metal surface defects. Curated data makes things easy, so the question was
whether the same setup holds elsewhere.

The subject is a white injection-molded plastic part: a clothespin. I bought ten of the
same product, kept five as normal, and created defects on four.

![Samples](images/setup/samples.jpeg)

| Defect | Nature |
|---|---|
| `d1_stain` | Surface defect |
| `d2_broken` | Shape defect (local, with a fracture surface) |
| `d3_deformed` | Shape defect (whole body) |
| `d4_bent` | Shape defect (local, no fracture surface) |

A webcam is fixed directly above; only the subject is swapped between shots.

![Rig](images/setup/rig.png)

Three of the five normal samples (`n2` `n3` `n4`) were deliberately tilted and
**excluded from spec generation**. They are a holdout used only for evaluation.
`n2` is tilted about 30 degrees.

---

## Results

### Detection and false positives

Claude Haiku 4.5 / 4x4 grid / majority vote of 3 / normal samples passed as references.

| Sample | Defect | Verdict | Cell |
|---|---|---|---|
| d1 | stain | **REVIEW** | C3 |
| d2 | broken | **REVIEW** | C1 |
| d3 | deformed | OK (missed) | — |
| d4 | bent | OK (missed) | — |
| n1–n5 | normal | all OK | — |

```
False positive rate      = 0 / 5 = 0%
Detection (REVIEW or NG) = 2 / 4 = 50%
Latency                  = 5.8-8.7 s per image
JSON parse failures      = 0 / 27 runs
Verdict variance         = none (identical across 3 runs)
Spec generation          = 87.02 s (Claude Opus 5 / in=10,611 out=5,187)
```

![Detected](results/annotated/01_detected_broken_C1.png)

No false positive even on the 30-degree tilted holdout.

![No false positive](results/annotated/04_no_false_positive_tilt30.png)

### The boundary was not "surface vs shape"

I expected surface defects to work and shape defects to fail. The measured line was different.

| | Result |
|---|---|
| Clear fracture surface, high-contrast deposit | Detected |
| Subtle shape change without a fracture surface | Not detected |

`d2` (broken tip) and `d4` (bent leg tip) are both shape defects, yet the first was caught
and the second was missed. The difference is **whether a local, visible cue appears in the image**.

### Three prompt variants

To catch shape defects I changed the inspection prompt twice. **Both attempts made it worse.**

| Variant | Concrete defect descriptions | Procedural instructions | Detection | False positives | Spec file |
|---|---|---|---|---|---|
| **A** | yes | no | **2 / 4** | 0 / 5 | `results/spec_clothespin_4x4.json` |
| B | no | yes | 0 / 4 | 0 / 5 | `results/spec_clothespin_partB.json` |
| C | yes | yes | 0 / 4 | 0 / 5 | `results/spec_clothespin_partC.json` |

Variant B added procedures but dropped the concrete defect descriptions, and detected nothing.
Variant C kept the descriptions and only added procedures, yet it still lost what A had caught.

Reading variant C's output, the defect was visible but actively dismissed.

```json
"normal_observations": "A white spherical object is visible at the top of cell C1, but this is
  judged to be a lighting reflection or an external object in front of the lens (dust, water droplet).",
"overall_notes": "Symmetry, tip length, contour continuity and surface condition all meet the normal criteria.",
"verdict": "OK"
```

Same sample, same model. Only the prompt differs.

![Variant C misses it](results/annotated/05_variantC_missed_broken.png)

Variant A returned REVIEW with confidence 0.65 on both hits. At first that looked like an
incomplete result. But B and C lost the hesitation and confidently said OK, and that confidence
was wrong. **REVIEW was not incompleteness. It was the model correctly routing uncertain cases
to human review.**

### Swapping to a cheaper model detected nothing

Same inspection spec, only the model changed.

| | Claude Haiku 4.5 | Nova 2 Lite |
|---|---|---|
| Detection (REVIEW or NG) | **2 / 4** | **0 / 4** |
| Input tokens (per image, 3 runs) | 20,766 | **7,536** |
| Output tokens (per image, 3 runs) | 1,848 | 1,406 |
| Latency | 5.8-8.7 s | 2.3-6.4 s |
| JSON parse failures | 0 / 27 | 1 / 27 |
| Cost | not calculated (see note) | **JPY 1.146 per image** |

Nova 2 Lite detected metal surface scratches in the previous article. Here it detects nothing.
Its input token count is under a third of Haiku's, which suggests it processes the image more coarsely.

**The model tier required depends on the subject, and that maps directly to running cost.**

Nova 2 Lite pricing comes from the AWS Pricing API (ap-northeast-1). Claude Haiku 4.5 is not
registered there as of August 2026, so no estimated cost is published here.

---

## Changes from quickstart

Only two code changes.

### 1. `scripts/bedrock.py` — read_timeout

botocore defaults `read_timeout` to 60 seconds, but spec generation took 87 seconds.
The default causes a `ReadTimeoutError`.

```python
from botocore.config import Config

_BOTO_CONFIG = Config(connect_timeout=10, read_timeout=600,
                      retries={"max_attempts": 1, "mode": "standard"})
_client = boto3.client("bedrock-runtime", region_name=REGION, config=_BOTO_CONFIG)
```

Retries are capped at 1 so a timed-out long inference is not silently re-run and re-billed.

### 2. `scripts/generate_spec.py` — META_PROMPT

**This product is a movable part with a spring.** How far the two legs open varies among
normal units. Without stating that as an exclusion, the system either flags normal units as
deformed or misses real deformation.

The prompt now states that the subject includes shape defects, that leg opening is normal
variation, and that what matters is whether the plastic itself is broken, warped or chipped.
`normal_characteristics` is also asked to describe overall shape, symmetry and part relationships.

### Images were converted to JPEG

A raw webcam capture saved as PNG/RGBA is about 1.4 MB per image; six of them make an 8 MB
payload. Converted to JPEG (q92) each image is about **137 KB**.

**Resolution was not reduced** (still 1392x832). Claude only uses up to 1568 px on the long
edge, so changing the format alone cuts the size to roughly one tenth.

---

## Usage

```bash
pip install -r scripts/requirements.txt
```

Enable these models in Amazon Bedrock (region `ap-northeast-1`):

- `global.anthropic.claude-opus-5` (spec generation)
- `jp.anthropic.claude-haiku-4-5-20251001-v1:0` (inspection)
- `jp.amazon.nova-2-lite-v1:0` (inspection, for comparison)

Generate the spec. `n2` `n3` `n4` are held out.

```bash
cd scripts
python3 generate_spec.py \
  --defect ../images/defect/d1_stain.jpg ../images/defect/d2_broken.jpg \
           ../images/defect/d3_deformed.jpg ../images/defect/d4_bent.jpg \
  --normal ../images/normal/n1.jpg ../images/normal/n5.jpg \
  --cols 4 --rows 4 --out spec_clothespin_4x4.json
```

Inspect.

```bash
for f in d1_stain d2_broken d3_deformed d4_bent; do
  python3 inspect_image.py --spec spec_clothespin_4x4.json \
    --image "../images/defect/$f.jpg" --runs 3 --out "out/res_$f.json"
done

for f in n1 n2 n3 n4 n5; do
  python3 inspect_image.py --spec spec_clothespin_4x4.json \
    --image "../images/normal/$f.jpg" --runs 3 --out "out/res_$f.json"
done
```

Add `--model jp.amazon.nova-2-lite-v1:0` to switch models.
Generated specs and results are committed under `results/`, so you can inspect them without running anything.

---

## Layout

```
images/
├── normal/     5 normal units (n2/n3/n4 are tilted holdouts)
├── defect/     4 defective units
└── setup/      capture rig and the full sample set
results/
├── spec_clothespin_4x4.json    variant A
├── spec_clothespin_partB.json  variant B (procedures only)
├── spec_clothespin_partC.json  variant C (descriptions + procedures)
├── haiku/      variant A x Claude Haiku 4.5
├── nova/       variant A x Nova 2 Lite
├── partB/      variant B
├── partC/      variant C
├── grid/       inspection images with the grid burned in
└── annotated/  results overlaid on the images
scripts/        quickstart with two modifications
```

## Related articles

- [[Amazon Bedrock] 洗濯ばさみで外観検査AIを試してみました 〜つまずいたのは「正常の定義」でした〜](https://dev.classmethod.jp/articles/bedrock-vlm-visual-inspection-clothespin/) (Japanese)
- [[Amazon Bedrock] 検査プロンプトを VLM 自身に書かせて外観検査をしてみました](https://dev.classmethod.jp/articles/bedrock-vlm-visual-inspection-generated-prompt/) (Japanese)
- [[Amazon Bedrock] Nova 2 Lite で金属部品の欠陥検出（外観検査）を試してみました](https://dev.classmethod.jp/articles/bedrock-nova-2-lite-metal-defect-detection/) (Japanese)

## License

MIT License

All photographs were taken by the author. The clothespins are off-the-shelf products and the
defects were created by hand.

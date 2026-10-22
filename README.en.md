# massing-facade-rebuild

> The massing sets the geometry. The rendering sets the language.

A skill for AI agents: take a rendering you have approved and translate it into an editable, verifiable facade model in Blender, built on your existing SketchUp massing.

![Massing → rendering → Blender model](docs/images/apartment_pipeline.jpg)

[中文](README.md)

## Why

Most "one-prompt AI modeling" demos build from a single photo, out of thin air. Real projects don't work that way. The massing comes first, and the facade is developed on top of it. Floor counts, levels and dimensions belong to the massing; the rendering only carries the facade language.

This skill hands that workflow to an agent. The massing stays untouched while the facade grows out of the rendering. Whatever can't be seen is recorded honestly as an inference, and key decisions go back to the designer.

## How it works

| Stage | What happens | Gate |
|---|---|---|
| 0 Check inputs | Inventory the files, start the run record | Stop if the massing or the rendering is missing |
| 1 Lock the massing | Read units, floors, levels and facade orientation; write a geometry fact sheet | Ask if anything disagrees with the SketchUp screenshot |
| 2 Render (optional) | Generate the rendering with the built-in prompt template | Designer confirms this is the direction |
| 3 Scale check | Classify the facade (irregular / repeating motif) and check proportions | Designer confirms the type and the inferences |
| 4 Read | Record unit by unit, or by motif; tag what is visible, inferred or corrected | Every unit traceable to a record |
| 4.5 Samples | Build a small piece first, side by side with the rendering | Designer confirms item by item before the full build |
| 5 Build | Model the facade on a copy of the massing, original meshes untouched; list every edge condition before filling the facade | — |
| 6 Check & verify | Fixed-angle check views, plus one render matched to the rendering's view; verify the massing is unchanged | Verification passes and the designer has reviewed |
| 7 Deliver & archive | Hand over the model, records and verification report; archive by copying only | — |

Every stage leaves files behind, so a new session can pick up where the last one stopped.

## Two cases

### Apartment: irregular openings

![Apartment: facade detail next to the rendering](docs/images/apartment_detail.png)

- Inputs: FBX exported from SketchUp, a SketchUp screenshot, one target rendering, one detail crop
- The successful run took about 1 hour and about 3.15M tokens, using up roughly one 5-hour quota window (GPT-6 Astra, highest reasoning effort)
- 45 opening records; verification confirmed the original massing meshes were unchanged, new elements stayed in bounds, and no Array modifier was used
- Two openings hidden behind trees, and depths the image doesn't show, were marked as inferred

### Office tower: repeating motif

![Office tower, final model at dusk](docs/images/tower_final.jpg)

- A 30 × 30 m tower: 8 m double-height retail, 20 office floors, a 4 m crown. Only the two faces visible in the rendering and their shared corner were built
- A single unit is only a dozen or so pixels in the rendering. To fill the gaps, the designer supplied a unit model, a small patch layout, a full-facade layout with key dimensions (pitch and per-floor offset), and a corner rule
- The samples went through four rounds, fixing form, construction logic and how neighbouring units meet. After the first full build, the corners terminated differently on every floor because the per-floor offset hadn't been fixed; the dimensions were set and the facade rebuilt
- GPT-6 Astra at the highest reasoning effort throughout: about 83M tokens from start to delivery (logged portion), about 7 hours of main-session run time, spread over nearly three weeks with 20 interrupted-and-resumed turns

In the last round the AI could already match the rendering closely (middle: white frames covering about 72% of the facade). For office daylight, the designer chose slimmer frames (right). That departure is recorded in `adaptation.md` as a deliberate trade-off.

![Approved rendering, option B matched to it, and the designer's final model](docs/images/tower_result.jpg)

*Left: approved rendering · Middle: option B, matched to the rendering · Right: the designer's final model*

## What the two cases show

- **When the rendering makes the design clear, the AI gets it right in one pass. When it doesn't, the designer has to supply the rules.** Cost follows: the tower used about 26 times the tokens of the apartment's successful run.
- **The AI can make it look like the rendering. The designer decides whether it should.** Matching the image is only the starting point; daylight and use, which image generators ignore, are the designer's call.

Cost details and how they were counted: [benchmark-attempts.md](references/benchmark-attempts.md#成本对比) (in Chinese).

## Who decides what

- **The massing is fixed.** When it conflicts with the rendering, the massing wins.
- **A rendering is design intent, not a construction drawing.** Construction is read conventionally: glass and windows are standard rectangles, and shaped elements are cladding worn over them like clothing. When following the image would hurt daylight or ventilation, the AI flags it and you decide.
- **Local inference is fine; inventing the whole facade is not.** Depths and hidden parts may be inferred. How the whole facade is composed and how it varies must come from the rendering or from the designer.
- **Design intent is yours; implementation detail is the AI's.** You set construction logic, the overall reading and key dimensions. The AI sets hole positions and fine adjustments. Where you haven't decided yet, the AI offers two options for you to pick from.
- **Visible, inferred and corrected are recorded separately.** Every judgment has a source, and deliberate departures from the rendering are recorded as trade-offs.

The full rules are in [SKILL.md](SKILL.md). It is written in Chinese; agents read it without trouble.

## Quick start

**You need:**

- Blender, runnable from the command line (`blender --background --python script.py`)
- An agent that can read and write local files and run commands. Tested with Codex + GPT-6 Astra; highest reasoning effort recommended
- Inputs:
  - The massing exported from SketchUp as FBX, with the target building in its own group under an English name
  - A SketchUp screenshot from the same view as the rendering, showing only control lines such as floor and bay lines
  - One rendering you have approved, or 1–2 style references (Stage 2 generates the rendering from them)
  - A detail crop of the most complex area
  - Optional: a motif unit model, for when units can't be read from the rendering

**Install:** put the whole folder in your agent's skills directory (for example `.agents/skills/` for Codex or `.claude/skills/` for Claude Code; check each tool's docs), or simply point the agent to `SKILL.md` in the conversation.

**Opening prompt:**

```
Use the massing-facade-rebuild skill: read SKILL.md and its references/ in full, then follow its stages and gates.

Task: rebuild the facade of ____ on the massing.
- Inputs: ____ (read-only)
- Target building: ____ (group name in SketchUp); everything else is protected
- Scope: ____ (e.g. only the faces visible in the rendering)
- Project info: ____ (building type, use of each floor, floor heights)
- Write outputs to: ____

Stop after the Stage 4.5 samples are rendered and wait for my confirmation.
```

## Known limitations

- Fine gradient textures: image generators can't render them clearly, so they don't suit this workflow yet
- When the motif can't be read from the rendering, the designer has to supply a unit model and key dimensions, and revisions and cost go up sharply
- Verification checks the width and height bounds of new elements, not their depth
- SketchUp's FBX export has a few traps: group names get numbered, non-English names turn into underscores, and units may be mislabeled (checked in Stage 1)
- Fully tested only with Codex + GPT-6 Astra; four-sided wraps and curved massings are untested

## Layout

```
massing-facade-rebuild/
├── SKILL.md                   # stages, gates and principles
├── README.md                  # Chinese
├── README.en.md               # English
├── LICENSE
├── scripts/                   # import, fact sheet, check views, verification
├── references/
│   ├── render-prompt.md       # rendering prompt template
│   ├── opening-record.csv     # per-unit record template
│   ├── motif-record.md        # motif record template
│   ├── geometry-vocabulary.md # geometry vocabulary
│   ├── run-record.md          # run record fields
│   └── benchmark-attempts.md  # experiment log and cost comparison, failures included
└── docs/images/               # README images
```

## Experiment log

[benchmark-attempts.md](references/benchmark-attempts.md) (in Chinese) records every round of both cases, failures included, and separates the conclusions the evidence supports from the ones it doesn't. "Ultra was the sole reason for success" is one it doesn't support; so is "the tower cost more only because of its facade type".

## License

MIT, see [LICENSE](LICENSE).

## Author

H.C. Lai · [hclai.studio](https://hclai.studio)

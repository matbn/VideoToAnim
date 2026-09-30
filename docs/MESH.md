# Using a Mixamo mesh (character) in the output

## Português

Este documento também está disponível em português: [MESH.pt-BR.md](MESH.pt-BR.md).

By default the exported GLB uses a **"capsule sticks"** mesh (a thin cylinder per bone) just
to visualize the motion. You can upload your own character — **FBX or glTF/GLB** — and the
animation will be applied **to its mesh**.

No conversion needed: Mixamo exports FBX by default and the FBX is read **directly**, without
Blender or the FBX SDK.

## How to use

1. In the interface, the **"Mixamo mesh (optional)"** field: upload the file (`.fbx` or `.glb`).
2. The job runs as usual; the Job panel shows a **"Mesh skeleton"** block with the verdict.

## What the software checks

The file's skeleton is compared against the **65-bone Mixamo contract** (`Hips, Spine, Spine1,
Spine2, Neck, Head, HeadTop_End, LeftShoulder, LeftArm, …, LeftHandPinky4, LeftUpLeg, …, RightToe_End`),
accepting the `mixamorig:` prefix with or without the colon.

| report field | meaning |
|---|---|
| `matched` | how many bones in your file match the contract (65 is ideal) |
| `missing` | required bones that are missing |
| `extra` | bones outside the standard (ignored — e.g., props) |
| `compatible` | the skeleton can receive the animation |
| `attachable` | the mesh was **actually** used in the output GLB |
| `stats` | sub-meshes, vertices and how many ended up **unweighted** |

Only missing end-caps (e.g., `LeftToe_End`) are tolerated; a missing **animated** bone rejects the
mesh. When rejected, the interface shows the reason and the pipeline **does not break**: it falls
back to the capsule sticks and reports it.

## Formats

| format | compatibility | attach to the output |
|---|---|---|
| **FBX binary** (Mixamo's default) | yes | **yes** — own reader in Python (`core/fbx.py`) |
| **FBX ASCII** (another Mixamo option) | yes | **yes** — same reader, same node tree |
| **GLB / glTF** | yes | **yes** — re-skin via `pygltflib` |

## Diagnostics before processing

The mesh field has the **"Check mesh"** button, which calls `POST /api/mesh/inspect` and answers
**without processing the video**: detected format, matched/missing bones, whether it is compatible
and whether attaching works (vertices and triangles read). It is the fastest way to understand why
a mesh was not used, without waiting for a whole job.

Common errors already handled with a clear message:

| symptom | message |
|---|---|
| FBX exported **without skin** (Mixamo's "Without Skin" option) | *"the FBX has no skin … download with 'Skin: With Skin'"* |
| incomplete/renamed skeleton | lists the missing bones |
| unrecognized file | reports that it is not FBX/GLB |

## How the attach works

In both cases the joints are remapped **by name** to our rig, the weights are normalized per vertex
(top-4 influences) and the **inverseBindMatrices are recomputed** from our rest pose. That is why the
mesh is correct in the T-pose even if your character's proportions differ from ours.

### FBX binary (own reader)

`core/fbx.py` parses the FBX 7.x node tree (32/64-bit offsets depending on the version, deflated
arrays, the null record as terminator) and `load_fbx_mesh` extracts:

- `Objects → Geometry`: `Vertices` (control points) and `PolygonVertexIndex` (fan-triangulated);
- `Objects → Deformer`: `Skin` and `Cluster` (`Indexes` + `Weights`);
- `Connections`: the `Skin` points to the `Geometry`; each `Cluster` points to the `Skin` and the
  bone (`Model`) is linked to the cluster;
- `GlobalSettings.UnitScaleFactor`: converts the FBX unit (cm) to meters.

A `Cluster` **without** `Indexes`/`Weights` is treated as a **bone that does not influence the mesh**
and is ignored. This matters: Mixamo emits a cluster for **every** bone of the skeleton, and the ones
that weigh nothing come out empty. Treating them as "influences everything" (the literal reading of
the FBX docs) made the body's 12 empty clusters gain weight 1.0 on **all** 6,658 vertices and
dominate the top-4 selection — the body got stuck to **finger tips** instead of Hips/Spine/legs, and
the mesh did not move the way it should. Vertices with no weight at all fall back to `Hips` and are
counted in `stats.unweighted`; ignored empty clusters appear in `stats.empty_clusters`.

## Checking that the mesh actually deforms

A GLB can be structurally correct and still not move. To measure it for real, the project ships
`tools/verify_glb_skinning.py`, which applies the glTF 2.0 skinning formula at two instants
(t=0 and t=duration/2) and reports the vertex displacement:

```powershell
.\.venv\Scripts\python.exe tools\verify_glb_skinning.py storage\jobs\<id>\model.glb
```

It shows joints in use, influence distribution, weight normalization and the displacement
(`>>> RESULTS: the mesh DEFORMS correctly`). In your `MixamoChar.fbx`: **53 joints in use**,
normalized weights, 0 unweighted, and ~89% of the sampled vertices move between the two instants.

### GLB / glTF

Reads `skins[0].joints`, matches by name and reuses `POSITION`, `JOINTS_0`, `WEIGHTS_0` and `indices`.

## Tested with your file

`MixamoChar.fbx` (5.4 MB, FBX 7700): **65/65 bones matched**, compatible, attached — 6 sub-meshes,
9,285 vertices, **0 unweighted**, height 1.80 m, arm span 1.96 m. The final GLB came out with those
9,285 vertices (against 624 for the capsule sticks).

## Limitations

- The mesh must have **skinning** — a file without skin is rejected with a clear message.
- Vertices weighted to a joint that does not exist in our rig are rejected (with a warning),
  instead of deforming silently.
- No support for blend shapes / morph targets, and no materials/textures in the preview.
- **ASCII** FBX is read normally. The case that still does not work is **without skin** (exporting
  from Mixamo without checking "With Skin") — there is no way to anchor the animation, and the
  software warns about it.

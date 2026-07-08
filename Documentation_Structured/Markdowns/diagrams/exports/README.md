# Diagram Exports

Rendered diagram files belong in this folder.

Recommended export names:

- `SystemContext.svg`
- `ContainerArchitecture.svg`
- `BusinessNominationLifecycle.svg`
- `ConceptualDataModel.svg`
- `NominationCleanApprovalFlow.svg`
- `NominationHRBPReviewFlow.svg`
- `ModelLifecycle.svg`
- `PayrollIntegrationFlow.svg`
- `AzureDeployment.svg`

Do not hand-edit exported SVG or PNG files. Update `../structurizr/workspace.dsl`, render again, and replace the exports.

The Structurizr-derived SVGs have matching `*-key.svg` legend files beside them. `BusinessNominationLifecycle.svg` is a buyer/business-facing workflow view maintained separately from the Structurizr technical workflow views.

---

## How to (re)render

### Method A — Graphviz (headless, no Docker, no browser)

This is how the current SVGs were produced. It needs only `graphviz` (`dot`) and
Python 3 — no Java, no Docker, no `localhost:8080` server.

```powershell
# from repo root; requires Graphviz on PATH (winget install Graphviz.Graphviz)
python .\Award_Nomination_App\Documentation_Codex\diagrams\structurizr\render_dsl.py
```

It parses `../structurizr/workspace.dsl` and writes one `<ViewKey>.svg` (+ `.dot`)
per view into this folder. Layout is Graphviz auto-layout — correct and readable,
though not pixel-identical to the official Structurizr renderer.

### Method B — Official Structurizr renderer (nicer layout, needs Docker)

Use this when you want Structurizr's own layout engine. Two things to fix versus
the earlier attempt:

1. Use the **Structurizr Lite** image (`structurizr/lite`), not the on-premises
   `structurizr/structurizr` server. Lite serves a single mounted workspace.
2. Open **`http://localhost:8080`** — *not* `/workspace/1` (that path is only for
   the multi-workspace on-premises/cloud product, which is why the earlier URL
   showed nothing).

```powershell
# interactive viewer — open http://localhost:8080 after it starts
$dir = (Resolve-Path .\Award_Nomination_App\Documentation_Codex\diagrams\structurizr).Path
docker run --rm -p 8080:8080 -v "${dir}:/usr/local/structurizr" structurizr/lite
```

In the Lite UI, each diagram has an **Export** button (SVG/PNG). Save the files
here using the names listed above.

To export SVGs **headlessly** with the official renderer (no UI), use the
Structurizr CLI image to convert the DSL to PlantUML/Mermaid, then render:

```powershell
$dir = (Resolve-Path .\Award_Nomination_App\Documentation_Codex\diagrams\structurizr).Path
# DSL -> PlantUML (C4)
docker run --rm -v "${dir}:/usr/local/structurizr" structurizr/cli `
  export -workspace workspace.dsl -format plantuml/c4plantuml -output /usr/local/structurizr/plantuml
# then render the .puml files to SVG with a PlantUML image/jar
```

> Why the previous `structurizr/structurizr:...-playwright ... local` command
> created no listener: that image is the heavyweight on-premises server (expects
> numbered workspaces at `/workspace/1`, extra config, and typically a servlet
> container), so it does not just bring up a viewer on 8080. Structurizr **Lite**
> is the right tool for a single `workspace.dsl`.

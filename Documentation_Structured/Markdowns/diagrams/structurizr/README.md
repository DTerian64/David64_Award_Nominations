# Structurizr Diagrams

This folder contains Structurizr DSL source for structured architecture and workflow diagrams.

## Source

- `workspace.dsl` is the source of truth.
- Exported images should be written to `../exports`.
- Markdown documents should embed exported SVG or PNG files after rendering.

## View Keys

| View key | Replaces or supports |
| --- | --- |
| `SystemContext` | Solution Architecture - System Context Mermaid diagram. |
| `ContainerArchitecture` | Solution Architecture - major containers and integration paths. |
| `ConceptualDataModel` | Data Architecture - Conceptual Data Model Mermaid ER diagram. |
| `NominationCleanApprovalFlow` | Solution Architecture - Core Workflow and Business Architecture - Nomination Lifecycle. |
| `NominationHRBPReviewFlow` | HRBP branch of the nomination lifecycle. |
| `ModelLifecycle` | AI/ML Architecture - Model Lifecycle Mermaid diagram. |
| `PayrollIntegrationFlow` | Integration Architecture - Payroll Integration Mermaid diagram. |
| `AzureDeployment` | Solution Architecture - Deployment Topology Mermaid diagram. |

## Open workspace.dsl in the Structurizr server (Tomcat) — proven steps

Structurizr **Lite** (`structurizr/lite`) is discontinued — that image now only prints
a migration notice and exits. The replacement is the consolidated
`structurizr/structurizr` image run with the **`local`** argument, which serves the
workspace from an embedded **Tomcat** server on port 8080.

### 1. Start the server

Run this in **PowerShell** (single line; adjust the path only if the repo moves). It
mounts the folder that contains `workspace.dsl` and starts Tomcat detached:

```powershell
docker rm -f structurizr-local 2>$null
docker run -d --name structurizr-local --user 0:0 --dns 127.0.0.1 -e STRUCTURIZR_NETWORK_TIMEOUT=2000 -p 8080:8080 -v "C:/Users/David/source/repos/David64_Award_Nominations/Award_Nomination_App/Documentation_Codex/diagrams/structurizr:/usr/local/structurizr" structurizr/structurizr:2026.05.22 local
```

### 2. Wait for Tomcat to be ready

```powershell
docker logs -f structurizr-local
```

Wait for the line **`Tomcat started on port(s): 8080`** (also `Started ... in N seconds`).
Then press `Ctrl+C` — that only stops the log tail; the container keeps running because
it is detached. Startup takes ~10–40s.

### 3. Open it

```text
http://localhost:8080
```

It redirects to `http://localhost:8080/workspace/1` (the diagram index). Individual
diagrams are at `http://localhost:8080/workspace/1/diagrams#<ViewKey>`
(e.g. `.../diagrams#SystemContext`).

### 4. Export a diagram to SVG/PNG

Open a diagram, then in the top toolbar click the **Export** icon (far-right,
picture/landscape glyph) → **Export as SVG** (or PNG). Save into `../exports` using the
view key as the filename (see the table above). Each SVG export also produces a small
`*-key.svg` legend file.

### 5. Stop the server

```powershell
docker rm -f structurizr-local
```

### Why the extra flags (do not remove them)

These were all required to get it running on this machine; stripping any one reintroduces
a failure we already diagnosed:

| Flag | Reason |
| --- | --- |
| `structurizr/structurizr:<tag> local` | Lite is discontinued; `local` is its replacement mode. Use a **non-`-playwright`** tag — the `-playwright` variant hangs on headless-Chromium init at startup. |
| `--user 0:0` | The container writes its log + Lucene search index into the mounted folder. Without root it fails with `Permission denied` / `write.lock AccessDenied` and never starts. Use numeric `0:0` (the image is distroless, so `--user root` by name fails: "no matching entries in passwd file"). |
| `--dns 127.0.0.1` and `-e STRUCTURIZR_NETWORK_TIMEOUT=2000` | On startup the server makes an outbound "theme/version" network call. Behind the Cisco AnyConnect VPN that call black-holes and the boot freezes at the `Themes:` log line for minutes. Breaking DNS + shortening the timeout makes it fail fast and continue offline. |
| `-p 8080:8080` | Publishes Tomcat's port to the host. |
| `-v "C:/...:/usr/local/structurizr"` | Mounts the folder that contains `workspace.dsl`. Use **forward slashes** to avoid the Windows drive-letter colon clashing with the mount separator. Point at the folder, not the file. |

### Troubleshooting

- **`ERR_CONNECTION_REFUSED`** → nothing is listening; the container exited or isn't started. Check `docker ps` and `docker logs structurizr-local`.
- **`ERR_EMPTY_RESPONSE` / page won't load** → Tomcat hasn't finished starting; wait for the `Tomcat started on port(s): 8080` log line.
- **Log frozen at `Themes:`** → the startup network call is hanging; ensure the `--dns 127.0.0.1` and `STRUCTURIZR_NETWORK_TIMEOUT` flags are present (VPN issue).
- **A hidden `.structurizr/` folder appears here** (logs + search index) — that is expected and safe to gitignore.

## Validate or Export with Structurizr CLI

If the Structurizr CLI is installed or the CLI JAR is available:

```powershell
java -jar structurizr-cli.jar validate -workspace Documentation_Codex\diagrams\structurizr\workspace.dsl
```

Example export command:

```powershell
java -jar structurizr-cli.jar export -workspace Documentation_Codex\diagrams\structurizr\workspace.dsl -format plantuml -output Documentation_Codex\diagrams\exports
```

The CLI can export intermediate formats such as PlantUML, Mermaid, DOT, or JSON depending on the installed version. For polished SVG/PNG output, Structurizr Lite is usually the easiest first step.

## Markdown Embedding Pattern

After exporting a diagram to SVG:

```html
<img src="../../../diagrams/exports/SystemContext.svg" alt="System Context">
```

For documents inside volume folders, adjust the relative path:

```html
<img src="../diagrams/exports/SystemContext.svg" alt="System Context">
```

The exported image is what Markdown displays. The `.dsl` file remains the maintainable source.

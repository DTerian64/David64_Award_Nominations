#!/usr/bin/env python3
"""
render_dsl.py — headless Structurizr DSL -> SVG renderer (Graphviz backend).

Why this exists: Structurizr Lite/CLI and its Docker images were not reachable
in our environment, and we don't want to depend on a localhost:8080 UI just to
export diagrams. Graphviz ('dot') renders SVG headlessly with no browser.

Scope: parses the subset of Structurizr DSL used by this workspace
(person / softwareSystem / container / component, relationships, deployment
nodes, and views: systemContext, container, component, dynamic, deployment).
It is not a full DSL implementation — it is a pragmatic exporter for THIS model.

Usage:
    python3 render_dsl.py [workspace.dsl] [output_dir]
Defaults: workspace.dsl in this folder -> ../exports

Requires: graphviz 'dot' on PATH. (No Java, no Docker, no network.)
"""
from __future__ import annotations
import os
import re
import sys
import subprocess
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))

# ----- style palette (mirrors the DSL styles block) -----
STYLE = {
    "person":         {"bg": "#08427b", "fg": "#ffffff"},
    "softwareSystem": {"bg": "#1168bd", "fg": "#ffffff"},
    "external":       {"bg": "#999999", "fg": "#ffffff"},
    "container":      {"bg": "#438dd5", "fg": "#ffffff"},
    "component":      {"bg": "#85bbf0", "fg": "#000000"},
    "deploymentNode": {"bg": "#ffffff", "fg": "#222222"},
}
REL_COLOR = "#707070"


class El:
    def __init__(self, eid, kind, name, desc="", tech="", tags=None, parent=None):
        self.id, self.kind, self.name = eid, kind, name
        self.desc, self.tech = desc or "", tech or ""
        self.tags = tags or []
        self.parent = parent

    @property
    def external(self):
        return "External" in self.tags


class Model:
    def __init__(self):
        self.els: dict[str, El] = {}
        self.rels: list[tuple[str, str, str]] = []      # (src, dst, label)
        self.dnodes = []                                 # deployment tree
        self.views = []                                  # parsed views

    def children(self, pid):
        return [e for e in self.els.values() if e.parent == pid]


# ---------- parsing ----------
_defs = {
    "person":         re.compile(r'^(\w+)\s*=\s*person\s+"([^"]*)"(?:\s+"([^"]*)")?', re.I),
    "softwareSystem": re.compile(r'^(\w+)\s*=\s*softwareSystem\s+"([^"]*)"(?:\s+"([^"]*)")?(?:\s+"([^"]*)")?', re.I),
    "container":      re.compile(r'^(\w+)\s*=\s*container\s+"([^"]*)"(?:\s+"([^"]*)")?(?:\s+"([^"]*)")?', re.I),
    "component":      re.compile(r'^(\w+)\s*=\s*component\s+"([^"]*)"(?:\s+"([^"]*)")?(?:\s+"([^"]*)")?', re.I),
}
_rel = re.compile(r'^(\w+)\s*->\s*(\w+)(?:\s+"([^"]*)")?')
_view_hdr = re.compile(r'^(systemContext|container|component|dynamic|deployment)\s+(\w+)\s+(.*?)\s*\{\s*$')
_tok = re.compile(r'"([^"]*)"|(\S+)')


def _parse_view_header(kind, scope, remainder):
    """remainder holds the tokens after '<kind> <scope>' up to '{'.
    non-deployment:  "Key" "Desc"
    deployment:      <env|"env"> "Key" "Desc"
    """
    toks = [q if q is not None else w for q, w in _tok.findall(remainder)]
    env = key = desc = None
    if kind == "deployment":
        if toks:
            env = toks[0]
        if len(toks) > 1:
            key = toks[1]
        if len(toks) > 2:
            desc = toks[2]
    else:
        if toks:
            key = toks[0]
        if len(toks) > 1:
            desc = toks[1]
    return env, key, desc
_dnode = re.compile(r'^deploymentNode\s+"([^"]*)"(?:\s+"([^"]*)")?\s*\{')
_infra = re.compile(r'^infrastructureNode\s+"([^"]*)"')
_cinst = re.compile(r'^containerInstance\s+(\w+)')


def parse(path) -> Model:
    m = Model()
    stack = []          # frames: dict(kind=..., id/name/view)
    with open(path, encoding="utf-8") as f:
        raw = f.readlines()

    def cur_element_parent():
        for fr in reversed(stack):
            if fr["kind"] == "element":
                return fr["id"]
        return None

    def cur_view():
        for fr in reversed(stack):
            if fr["kind"] == "view":
                return fr["view"]
        return None

    def cur_dnode():
        for fr in reversed(stack):
            if fr["kind"] == "dnode":
                return fr["node"]
        return None

    in_styles = False
    for line in raw:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            continue

        if s == "}":
            if stack:
                fr = stack.pop()
                if fr["kind"] == "styles":
                    in_styles = False
            continue

        if in_styles:
            continue

        # views header
        vh = _view_hdr.match(s)
        if vh:
            kind, scope, remainder = vh.groups()
            env, key, desc = _parse_view_header(kind, scope, remainder)
            view = {"kind": kind, "scope": scope, "env": env, "key": key,
                    "desc": desc or "", "layout": "lr", "steps": []}
            m.views.append(view)
            stack.append({"kind": "view", "view": view})
            continue

        if s.startswith("styles"):
            in_styles = True
            stack.append({"kind": "styles"})
            continue

        if s.startswith("deploymentEnvironment"):
            stack.append({"kind": "denv"})
            continue

        dn = _dnode.match(s)
        if dn:
            node = {"name": dn.group(1), "desc": dn.group(2) or "",
                    "infra": [], "instances": [], "children": []}
            parent = cur_dnode()
            (parent["children"] if parent else m.dnodes).append(node)
            stack.append({"kind": "dnode", "node": node})
            continue

        if _infra.match(s) and cur_dnode() is not None:
            cur_dnode()["infra"].append(_infra.match(s).group(1))
            continue
        if _cinst.match(s) and cur_dnode() is not None:
            cur_dnode()["instances"].append(_cinst.match(s).group(1))
            continue

        # inside a view body
        v = cur_view()
        if v is not None:
            if s.startswith("autolayout"):
                parts = s.split()
                if len(parts) > 1:
                    v["layout"] = parts[1].lower()
                continue
            if s.startswith("include") or s.startswith("exclude") or s.startswith("description"):
                continue
            r = _rel.match(s)
            if r and v["kind"] == "dynamic":
                v["steps"].append((r.group(1), r.group(2), r.group(3) or ""))
                continue
            continue

        # element definitions (model scope)
        matched = False
        for kind, rx in _defs.items():
            mm = rx.match(s)
            if mm:
                g = mm.groups()
                eid, name = g[0], g[1]
                desc = g[2] if len(g) > 2 else ""
                tech, tags = "", []
                if kind in ("container", "component"):
                    tech = g[3] if len(g) > 3 and g[3] else ""
                if kind == "softwareSystem":
                    tag = g[3] if len(g) > 3 and g[3] else ""
                    if tag:
                        tags = [tag]
                m.els[eid] = El(eid, kind, name, desc, tech, tags, cur_element_parent())
                if s.endswith("{"):
                    stack.append({"kind": "element", "id": eid})
                matched = True
                break
        if matched:
            continue

        # bare model relationships
        r = _rel.match(s)
        if r and cur_view() is None:
            m.rels.append((r.group(1), r.group(2), r.group(3) or ""))
            continue

        # any other block opener we don't model: keep brace balance
        if s.endswith("{"):
            stack.append({"kind": "other"})

    return m


# ---------- rendering helpers ----------
def esc(t):
    return t.replace('"', '\\"')


def wrap(t, width=34, maxlines=4):
    if not t:
        return ""
    lines = textwrap.wrap(t, width=width)
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        lines[-1] = lines[-1].rstrip(".") + "…"
    return "\\n".join(lines)


def style_for(el: El):
    if el.kind == "person":
        return STYLE["person"]
    if el.kind == "softwareSystem":
        return STYLE["external"] if el.external else STYLE["softwareSystem"]
    if el.kind == "container":
        return STYLE["container"]
    if el.kind == "component":
        return STYLE["component"]
    return {"bg": "#dddddd", "fg": "#000000"}


def type_caption(el: El):
    if el.kind == "person":
        return "[Person]"
    if el.kind == "softwareSystem":
        return "[External System]" if el.external else "[Software System]"
    if el.kind == "container":
        return f"[Container: {el.tech}]" if el.tech else "[Container]"
    if el.kind == "component":
        return f"[Component: {el.tech}]" if el.tech else "[Component]"
    return ""


def node_label(el: El):
    parts = [type_caption(el), f"**{el.name}**"]
    body = "|".join(p for p in parts if p)
    name = wrap(el.name, 26, 3)
    cap = type_caption(el)
    desc = wrap(el.desc, 34, 4)
    lbl = f"{name}"
    if cap:
        lbl = f"{cap}\\n{name}"
    if desc:
        lbl += f"\\n \\n{desc}"
    return lbl


def node_line(el: El, shape_person=True):
    st = style_for(el)
    shape = "box"
    style = "rounded,filled"
    if el.kind == "person" and shape_person:
        shape = "box"
        style = "filled"
    return (f'  "{el.id}" [shape={shape}, style="{style}", '
            f'fillcolor="{st["bg"]}", fontcolor="{st["fg"]}", color="#33333355", '
            f'penwidth=1, fontname="Helvetica", fontsize=11, '
            f'label="{esc(node_label(el))}"];')


def edge_line(src, dst, label, order=None, layout="lr"):
    lbl = wrap(label, 26, 3)
    if order is not None:
        lbl = f"{order}. {lbl}" if lbl else f"{order}"
    return (f'  "{src}" -> "{dst}" [color="{REL_COLOR}", fontcolor="#444444", '
            f'fontname="Helvetica", fontsize=9, penwidth=1, '
            f'label="{esc(lbl)}"];')


def header(title, subtitle, layout="lr"):
    rankdir = {"lr": "LR", "tb": "TB", "bt": "BT", "rl": "RL"}.get(layout, "LR")
    return (
        'digraph G {\n'
        f'  rankdir={rankdir};\n'
        '  bgcolor="white";\n'
        '  nodesep=0.5; ranksep=0.75; splines=true; concentrate=false;\n'
        '  graph [fontname="Helvetica", labelloc="t", '
        f'label=<<b>{title}</b><br/><font point-size="10">{subtitle}</font><br/> >, fontsize=16];\n'
        '  node [margin="0.18,0.12"];\n'
        '  edge [arrowsize=0.8];\n'
    )


# ---------- per-view builders ----------
def build_system_context(m: Model, v):
    sysid = v["scope"]
    system = m.els[sysid]
    inside = {e.id for e in m.els.values()
              if e.id == sysid or _within(m, e.id, sysid)}
    persons = [e for e in m.els.values() if e.kind == "person"]
    ext = [e for e in m.els.values()
           if e.kind == "softwareSystem" and e.id != sysid]

    def roll(x):
        return sysid if x in inside else x

    lines = [header("System Context — " + system.name, v["desc"], v["layout"])]
    lines.append(node_line(system))
    for e in persons + ext:
        lines.append(node_line(e))
    seen = set()
    for a, b, l in m.rels:
        ra, rb = roll(a), roll(b)
        if ra == rb:
            continue
        if ra not in m.els or rb not in m.els:
            continue
        k = (ra, rb)
        if k in seen:
            continue
        seen.add(k)
        lines.append(edge_line(ra, rb, l, layout=v["layout"]))
    lines.append("}")
    return "\n".join(lines)


def build_container(m: Model, v):
    sysid = v["scope"]
    containers = [e for e in m.els.values() if e.kind == "container" and e.parent == sysid]
    cids = {c.id for c in containers}
    persons = [e for e in m.els.values() if e.kind == "person"]
    ext = [e for e in m.els.values() if e.kind == "softwareSystem" and e.id != sysid]

    def roll(x):
        # component -> its container; anything inside system stays as-is if container
        e = m.els.get(x)
        if e is None:
            return x
        if e.kind == "component":
            return e.parent
        return x

    include = cids | {p.id for p in persons} | {e.id for e in ext}
    lines = [header("Container View — " + m.els[sysid].name, v["desc"], v["layout"])]
    lines.append(f'  subgraph cluster_{sysid} {{ label="{esc(m.els[sysid].name)}"; '
                 'style="rounded,dashed"; color="#1168bd"; fontname="Helvetica"; fontsize=12;')
    for c in containers:
        lines.append("  " + node_line(c))
    lines.append("  }")
    for e in persons + ext:
        lines.append(node_line(e))
    seen = set()
    for a, b, l in m.rels:
        ra, rb = roll(a), roll(b)
        if ra == rb or ra not in include or rb not in include:
            continue
        k = (ra, rb, l)
        if k in seen:
            continue
        seen.add(k)
        lines.append(edge_line(ra, rb, l, layout=v["layout"]))
    lines.append("}")
    return "\n".join(lines)


def build_component(m: Model, v):
    cid = v["scope"]
    comps = [e for e in m.els.values() if e.kind == "component" and e.parent == cid]
    ids = {c.id for c in comps}
    lines = [header("Component View — " + m.els[cid].name, v["desc"], v["layout"])]
    for c in comps:
        lines.append(node_line(c))
    for a, b, l in m.rels:
        if a in ids and b in ids:
            lines.append(edge_line(a, b, l, layout=v["layout"]))
    lines.append("}")
    return "\n".join(lines)


def build_dynamic(m: Model, v):
    ids = []
    for a, b, _ in v["steps"]:
        for x in (a, b):
            if x not in ids:
                ids.append(x)
    lines = [header("Dynamic — " + v["key"], v["desc"], v["layout"])]
    for x in ids:
        e = m.els.get(x)
        if e:
            lines.append(node_line(e))
        else:
            lines.append(f'  "{x}" [shape=box, style="rounded,filled", '
                         f'fillcolor="#cccccc", label="{esc(x)}"];')
    for i, (a, b, l) in enumerate(v["steps"], 1):
        lines.append(edge_line(a, b, l, order=i, layout=v["layout"]))
    lines.append("}")
    return "\n".join(lines)


def build_deployment(m: Model, v):
    lines = [header("Deployment — " + (v["env"] or "Azure"), v["desc"], v.get("layout", "tb"))]
    counter = [0]

    def emit(node, depth):
        counter[0] += 1
        cid = f"cluster_dn_{counter[0]}"
        lines.append(f'  subgraph {cid} {{ label="{esc(node["name"])}"; '
                     'style="rounded,filled"; fillcolor="#f4f7fb"; color="#9bb7d4"; '
                     'fontname="Helvetica"; fontsize=11;')
        for inf in node["infra"]:
            nid = f'infra_{counter[0]}_{abs(hash(inf))%99999}'
            lines.append(f'    "{nid}" [shape=box, style="rounded,filled", '
                         f'fillcolor="#dfe7f0", fontname="Helvetica", fontsize=10, '
                         f'label="{esc(inf)}\\n[Infrastructure]"];')
        for inst in node["instances"]:
            e = m.els.get(inst)
            nm = e.name if e else inst
            tech = f"\\n[{e.tech}]" if (e and e.tech) else "\\n[Container Instance]"
            st = STYLE["container"]
            nid = f'inst_{counter[0]}_{inst}'
            lines.append(f'    "{nid}" [shape=box, style="rounded,filled", '
                         f'fillcolor="{st["bg"]}", fontcolor="{st["fg"]}", '
                         f'fontname="Helvetica", fontsize=10, label="{esc(nm + tech)}"];')
        for ch in node["children"]:
            emit(ch, depth + 1)
        lines.append("  }")

    for n in m.dnodes:
        emit(n, 0)
    lines.append("}")
    return "\n".join(lines)


def _within(m: Model, eid, ancestor):
    e = m.els.get(eid)
    while e and e.parent:
        if e.parent == ancestor:
            return True
        e = m.els.get(e.parent)
    return False


BUILDERS = {
    "systemContext": build_system_context,
    "container": build_container,
    "component": build_component,
    "dynamic": build_dynamic,
    "deployment": build_deployment,
}


def main():
    dsl = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "workspace.dsl")
    outdir = sys.argv[2] if len(sys.argv) > 2 else os.path.abspath(os.path.join(HERE, "..", "exports"))
    os.makedirs(outdir, exist_ok=True)
    m = parse(dsl)
    print(f"Parsed: {len(m.els)} elements, {len(m.rels)} relationships, "
          f"{len(m.views)} views, {len(m.dnodes)} deployment root nodes")

    made = []
    for i, v in enumerate(m.views, 1):
        builder = BUILDERS.get(v["kind"])
        if not builder:
            continue
        dot = builder(m, v)
        key = v["key"] or f"{v['kind']}{i}"
        dotpath = os.path.join(outdir, f"{key}.dot")
        svgpath = os.path.join(outdir, f"{key}.svg")
        with open(dotpath, "w", encoding="utf-8") as f:
            f.write(dot)
        try:
            subprocess.run(["dot", "-Tsvg", dotpath, "-o", svgpath], check=True)
            made.append(os.path.basename(svgpath))
            print(f"  OK  {key}.svg")
        except subprocess.CalledProcessError as e:
            print(f"  ERR {key}: dot failed ({e})")
    print(f"\nWrote {len(made)} SVG(s) to {outdir}")


if __name__ == "__main__":
    main()

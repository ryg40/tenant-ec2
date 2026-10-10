#!/usr/bin/env python3
"""Build the five editable architecture diagrams from reviewed JSON sources.

Use a Git repository of the map builder with the pinned portable tag. The command
exports the tag into a temporary directory; it runs no working-checkout code.
Excalidraw loads each finished scene and exports its SVG in a local headless browser.
Needs Python 3.10+, Playwright with Chromium, Git, and Node 24 with npm.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

MAP_TAG = "portable-v0.1.0"
MAP_COMMIT = "4f6aef7496a9b8a7e4edb50ba8309e214e57a260"
EXCALIDRAW = "0.18.1"
STEMS = ("01-aws-core", "02-services", "03-entra-login", "04-install-flow", "05-options")
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "diagrams"

# The official file loader repairs imported bindings. Compare after that repair,
# not just before it, so a syntactically valid but disconnected scene cannot pass.
BROWSER_MODULE = r"""
import { loadFromBlob, exportToSvg } from '@excalidraw/excalidraw';
window.measureScene = (scene) => {
  const ctx = document.createElement('canvas').getContext('2d');
  for (const e of scene.elements) {
    if (e.type !== 'text') continue;
    ctx.font = `${e.fontSize}px Helvetica, Arial, sans-serif`;
    e.width = Math.ceil(Math.max(...e.text.split('\n').map(s => ctx.measureText(s).width)));
  }
  return scene;
};
window.renderScene = async (scene) => {
  const restored = await loadFromBlob(new Blob([JSON.stringify(scene)],
    {type: 'application/json'}), null, null);
  if (restored.elements.length !== scene.elements.length) throw Error('element loss');
  const byId = new Map(restored.elements.map(e => [e.id, e]));
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  for (const e of restored.elements) {
    if (e.type === 'arrow') {
      for (const end of ['startBinding', 'endBinding']) {
        const target = byId.get(e[end]?.elementId);
        if (!target?.boundElements?.some(b => b.id === e.id)) throw Error('lost binding');
      }
    }
    if (e.type === 'text') {
      ctx.font = `${e.fontSize}px Helvetica, Arial, sans-serif`;
      const actual = Math.max(...e.text.split('\n').map(s => ctx.measureText(s).width));
      if (actual > e.width + 1) throw Error(`text width: ${e.text}`);
    }
  }
  const svg = await exportToSvg({elements: restored.elements,
    appState: {...restored.appState, exportBackground: true, exportWithDarkMode: false,
      exportEmbedScene: false, exportPadding: 30}, files: restored.files});
  return {svg: svg.outerHTML, elements: restored.elements.length,
    arrows: restored.elements.filter(e => e.type === 'arrow').length};
};
"""


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def sid(key):
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def text_element(key, x, y, text, size, color="#243342", width=None):
    """Add presentation text; graph elements still come from the pinned builder."""
    lines = text.split("\n")
    return {"id": sid(key), "type": "text", "x": x, "y": y,
            "width": width or max(len(line) for line in lines) * size * 0.6,
            "height": len(lines) * size * 1.25, "angle": 0, "strokeColor": color,
            "backgroundColor": "transparent", "fillStyle": "solid", "strokeWidth": 1,
            "strokeStyle": "solid", "roughness": 0, "opacity": 100, "groupIds": [],
            "frameId": None, "roundness": None, "seed": int(sid(key)[:7], 16),
            "version": 1, "versionNonce": 1, "isDeleted": False, "boundElements": [],
            "updated": 1, "link": None, "locked": False, "text": text,
            "originalText": text, "fontSize": size, "fontFamily": 2,
            "textAlign": "left", "verticalAlign": "top", "containerId": None,
            "autoResize": True, "lineHeight": 1.25}


def finish_scene(scene, data):
    """Apply reviewed orthogonal routes, labels, title and key to the map output.

    The map builder ignores control points in its scene export. These explicit
    waypoints keep the editable scene and the SVG on the same route.
    """
    elements = scene["elements"]
    by_id = {e["id"]: e for e in elements}
    for e in elements:
        e["roughness"] = 0
        if e["type"] == "text":
            e["fontFamily"] = 2
            e["width"] = max(len(line) for line in e["text"].split("\n")) * e["fontSize"] * 0.6
    nodes = {n["id"]: n for n in data["nodes"]}
    for n in nodes.values():
        rect = by_id[sid("node-" + n["id"])]
        for prefix in ("node-kind-", "node-label-"):
            text = by_id[sid(prefix + n["id"])]
            # Keep independent text in the node group. Excalidraw permits only
            # one bound text per rectangle, while these cards have two linesets.
            text["customData"] = {"inside": rect["id"]}
    for e in data["edges"]:
        arrow = by_id[sid("edge-" + e["id"])]
        last = arrow["points"][-1]
        start = [arrow["x"], arrow["y"]]
        end = [start[0] + last[0], start[1] + last[1]]
        points = [start] + e.get("export", {}).get("via", []) + [end]
        arrow["points"] = [[x - start[0], y - start[1]] for x, y in points]
        arrow["width"] = max(x for x, _ in points) - min(x for x, _ in points)
        arrow["height"] = max(y for _, y in points) - min(y for _, y in points)
        arrow["roundness"] = None
        for binding in ("startBinding", "endBinding"):
            arrow[binding]["gap"] = 0
        label = by_id[sid("edge-label-" + e["id"])]
        if "labelAt" in e.get("export", {}):
            label["x"], label["y"] = e["export"]["labelAt"]
    min_x = min(e["x"] for e in elements)
    min_y = min(e["y"] for e in elements)
    max_y = max(e["y"] + e["height"] for e in elements if e["type"] != "arrow")
    elements.insert(0, text_element("heading", min_x, min_y - 160, data["meta"]["title"], 32))
    elements.insert(1, text_element("subtitle", min_x, min_y - 110, data["meta"]["subtitle"], 18))
    legend = "KEY: blue AWS | green service | brown data | red identity | gray external | solid core | dashed option"
    elements.append(text_element("legend", min_x, max_y + 42, legend, 17))
    for i, note in enumerate(data["export"]["notes"]):
        elements.append(text_element("note-" + str(i), min_x, max_y + 88 + i * 32, note, 17))
    scene["appState"]["viewBackgroundColor"] = "#ffffff"
    scene["appState"]["currentItemFontFamily"] = 2
    scene["source"] = "tenant-ec2 architecture"
    return scene


def overlap(a, b):
    return (min(a["x"] + a["width"], b["x"] + b["width"]) > max(a["x"], b["x"]) + 0.1
            and min(a["y"] + a["height"], b["y"] + b["height"]) > max(a["y"], b["y"]) + 0.1)


def touches(a, b, gap=0):
    return (a["x"] <= b["x"] + b["width"] + gap
            and b["x"] <= a["x"] + a["width"] + gap
            and a["y"] <= b["y"] + b["height"] + gap
            and b["y"] <= a["y"] + a["height"] + gap)


def borders(box):
    x, y, w, h = (box[k] for k in ("x", "y", "width", "height"))
    return [{"x": x, "y": y, "width": 0, "height": h},
            {"x": x + w, "y": y, "width": 0, "height": h},
            {"x": x, "y": y, "width": w, "height": 0},
            {"x": x, "y": y + h, "width": w, "height": 0}]


def clearance_errors(scene):
    """Allow a frame crossing, but reserve a lane beside each frame border.

    Forty drawing units separate a parallel arrow from a frame border. Labels
    keep six units plus the border stroke clear of both frames and cards.
    Frame rectangles come from the map builder; component rectangles carry IDs.
    """
    elements = scene["elements"]
    by_id = {e["id"]: e for e in elements}
    cards = [e for e in elements if e.get("customData", {}).get("component")]
    frames = [e for e in elements if e["type"] == "rectangle" and e not in cards]
    errors = []
    for arrow in (e for e in elements if e["type"] == "arrow"):
        name = arrow["customData"]["relationship"]
        label = by_id[sid("edge-label-" + name)]
        for card in cards:
            if touches(label, card, 6 + card["strokeWidth"] / 2):
                errors.append("arrow label touches a card: " + name)
        points = [(arrow["x"] + x, arrow["y"] + y) for x, y in arrow["points"]]
        for frame in frames:
            for border in borders(frame):
                if touches(label, border, 6 + frame["strokeWidth"] / 2):
                    errors.append("arrow label touches a frame border: " + name)
                for (x1, y1), (x2, y2) in zip(points, points[1:]):
                    if x1 == x2 and border["width"] == 0:
                        near = abs(x1 - border["x"]) < 40
                        along = max(min(y1, y2), border["y"]) < min(max(y1, y2), border["y"] + border["height"])
                    elif y1 == y2 and border["height"] == 0:
                        near = abs(y1 - border["y"]) < 40
                        along = max(min(x1, x2), border["x"]) < min(max(x1, x2), border["x"] + border["width"])
                    else:
                        continue
                    if near and along:
                        errors.append("arrow runs along a frame border: " + name)
    return sorted(set(errors))


def validate(scene):
    errors = clearance_errors(scene)
    assert not errors, "; ".join(errors)
    elements = scene["elements"]
    by_id = {e["id"]: e for e in elements}
    assert len(by_id) == len(elements), "duplicate element id"
    nodes = [e for e in elements if e.get("customData", {}).get("component")]
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            assert not overlap(a, b), "overlapping components"
    for e in elements:
        if e["type"] == "text" and e.get("customData", {}).get("inside"):
            box = by_id[e["customData"]["inside"]]
            assert e["x"] >= box["x"] and e["y"] >= box["y"], "text starts outside box"
            assert e["x"] + e["width"] <= box["x"] + box["width"], "text width exceeds box: " + e["text"]
            assert e["y"] + e["height"] <= box["y"] + box["height"], "text height exceeds box"
        if e["type"] != "arrow":
            continue
        for name in ("startBinding", "endBinding"):
            target = by_id[e[name]["elementId"]]
            assert {"id": e["id"], "type": "arrow"} in target["boundElements"], "missing reverse binding"
        points = [(e["x"] + x, e["y"] + y) for x, y in e["points"]]
        for (x, y), name in zip((points[0], points[-1]), ("startBinding", "endBinding")):
            n = by_id[e[name]["elementId"]]
            on_vertical = ((abs(x - n["x"]) < .1 or abs(x - n["x"] - n["width"]) < .1)
                           and n["y"] <= y <= n["y"] + n["height"])
            on_horizontal = ((abs(y - n["y"]) < .1 or abs(y - n["y"] - n["height"]) < .1)
                             and n["x"] <= x <= n["x"] + n["width"])
            assert on_vertical or on_horizontal, "endpoint off its bound box"
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            assert x1 == x2 or y1 == y2, "route is not orthogonal: " + e["customData"]["relationship"]
            for n in nodes:
                if x1 == x2:
                    hit = n["x"] < x1 < n["x"] + n["width"] and max(min(y1, y2), n["y"]) < min(max(y1, y2), n["y"] + n["height"])
                else:
                    hit = n["y"] < y1 < n["y"] + n["height"] and max(min(x1, x2), n["x"]) < min(max(x1, x2), n["x"] + n["width"])
                assert not hit, "arrow crosses a component"


def self_test():
    scene = json.loads((SOURCE / (STEMS[0] + ".excalidraw")).read_text())
    validate(scene)
    cases = ("overlap", "text", "binding", "endpoint", "crossing")
    for case in cases:
        bad = copy.deepcopy(scene)
        nodes = [e for e in bad["elements"] if e.get("customData", {}).get("component")]
        arrow = next(e for e in bad["elements"] if e["type"] == "arrow")
        if case == "overlap":
            nodes[1]["x"], nodes[1]["y"] = nodes[0]["x"], nodes[0]["y"]
        elif case == "text":
            text = next(e for e in bad["elements"] if e.get("customData", {}).get("inside"))
            text["width"] = 10000
        elif case == "binding":
            arrow["startBinding"]["elementId"] = nodes[-1]["id"]
        elif case == "endpoint":
            arrow["points"][-1][0] += 10
        else:
            # A detour through the middle of its source still has valid endpoints.
            arrow["points"].insert(1, [0, -60])
        try:
            validate(bad)
        except (AssertionError, KeyError):
            continue
        raise AssertionError("negative fixture passed: " + case)
    # One clean scene supplies independent failures on all four frame borders,
    # plus a card-touching label. Each failure must name the new clearance rule.
    arrow = next(e for e in scene["elements"] if e["type"] == "arrow")
    label_id = sid("edge-label-" + arrow["customData"]["relationship"])
    label = next(e for e in scene["elements"] if e["id"] == label_id)
    cards = [e for e in scene["elements"] if e.get("customData", {}).get("component")]
    for side in ("left", "right", "top", "bottom"):
        bad = copy.deepcopy(scene)
        frame = {"id": "fixture-frame", "type": "rectangle", "strokeWidth": 2,
                 "x": label["x"] - 80, "y": label["y"] - 80,
                 "width": label["width"] + 160, "height": label["height"] + 160}
        if side == "left":
            frame["x"] = label["x"] + label["width"]
        elif side == "right":
            frame["x"] = label["x"] - frame["width"]
        elif side == "top":
            frame["y"] = label["y"] + label["height"]
        else:
            frame["y"] = label["y"] - frame["height"]
        bad["elements"].append(frame)
        assert any("label touches a frame" in e for e in clearance_errors(bad)), side
    for axis in ("vertical", "horizontal"):
        bad = copy.deepcopy(scene)
        a = next(e for e in bad["elements"] if e["type"] == "arrow")
        a["points"] = [[0, 0], [0, 100] if axis == "vertical" else [100, 0]]
        frame = {"id": "fixture-frame", "type": "rectangle", "strokeWidth": 2,
                 "x": a["x"], "y": a["y"], "width": 200, "height": 200}
        bad["elements"].append(frame)
        assert any("runs along a frame" in e for e in clearance_errors(bad)), axis
    bad = copy.deepcopy(scene)
    text = next(e for e in bad["elements"] if e["id"] == label_id)
    text["x"], text["y"] = cards[0]["x"] - text["width"], cards[0]["y"]
    assert any("label touches a card" in e for e in clearance_errors(bad))
    print("diagram self-test: pass (12 invalid fixtures refused)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map-repo", type=Path, help="Git repository with the pinned map-builder tag")
    parser.add_argument("--npm-dir", type=Path, help="reuse a temporary npm directory with the exact pinned tools")
    parser.add_argument("--out-dir", type=Path, default=SOURCE)
    parser.add_argument("--check", action="store_true", help="load and check existing scenes without rebuilding")
    parser.add_argument("--self-test", action="store_true", help="test invalid geometry and binding fixtures without npm")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.check and not args.map_repo:
        parser.error("--map-repo is required for a build")
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix="architecture-build-") as tmp:
        work = Path(tmp)
        tool = work / "map"
        if not args.check:
            commit = run("git", "-C", str(args.map_repo), "rev-parse", MAP_TAG + "^{commit}", capture_output=True).stdout.strip()
            if commit != MAP_COMMIT:
                raise ValueError("the map-builder tag does not match its pinned commit")
            tool.mkdir()
            archive = work / "map.tar"
            with archive.open("wb") as handle:
                subprocess.run(["git", "-C", str(args.map_repo), "archive", MAP_TAG], stdout=handle, check=True)
            run("tar", "-xf", str(archive), "-C", str(tool))
        npm = args.npm_dir.resolve() if args.npm_dir else work / "npm"
        if not args.npm_dir:
            run("npm", "install", "--prefix", str(npm), "--ignore-scripts", "--no-audit", "--no-fund", "--save-exact",
                "@excalidraw/excalidraw@" + EXCALIDRAW, "react@19.2.4", "react-dom@19.2.4", "esbuild@0.28.2",
                stdout=subprocess.DEVNULL)
        for package, version in (("@excalidraw/excalidraw", EXCALIDRAW), ("esbuild", "0.28.2"),
                                 ("react", "19.2.4"), ("react-dom", "19.2.4")):
            found = json.loads((npm / "node_modules" / package / "package.json").read_text())["version"]
            if found != version:
                raise ValueError("wrong npm version: " + package)
        entry = npm / "architecture-entry.js"
        entry.write_text(BROWSER_MODULE)
        bundle = work / "browser.js"
        run(str(npm / "node_modules/.bin/esbuild"), str(entry), "--bundle", "--format=iife",
            "--define:process.env.NODE_ENV=\"production\"", "--outfile=" + str(bundle), "--log-level=error")
        args.out_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            page.goto("about:blank")
            page.add_script_tag(path=str(bundle))
            for stem in STEMS:
                target = args.out_dir / (stem + ".excalidraw")
                if args.check:
                    scene = json.loads(target.read_text())
                else:
                    data_file = SOURCE / (stem + ".json")
                    # -I removes the script directory. Add only this trusted tag
                    # export, not the current directory, for sibling imports.
                    loader = "import runpy,sys; sys.path.insert(0,sys.argv[1]); sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')"
                    run(sys.executable, "-I", "-c", loader, str(tool), str(tool / "build.py"),
                        "--data", str(data_file), "--out-dir", str(work / stem), stdout=subprocess.DEVNULL)
                    scene = finish_scene(json.loads((work / stem / "diagram.excalidraw").read_text()), json.loads(data_file.read_text()))
                if not args.check:
                    scene = page.evaluate("scene => window.measureScene(scene)", scene)
                validate(scene)
                result = page.evaluate("scene => window.renderScene(scene)", scene)
                # Export ids can include a random prefix; this document has no images
                # or clip paths, so no replacement or lossy SVG conversion is needed.
                svg = result["svg"] + "\n"
                if not args.check:
                    target.write_text(json.dumps(scene, indent=2) + "\n")
                    target.with_suffix(".svg").write_text(svg)
                else:
                    assert target.with_suffix(".svg").read_text() == svg, "SVG does not match scene: " + stem
                print(f"{stem}: Excalidraw {EXCALIDRAW} load + SVG + geometry pass ({result['elements']} elements, {result['arrows']} bound arrows)")
            browser.close()
    print("diagrams: pass (5 scenes, 5 SVG images)")


if __name__ == "__main__":
    main()

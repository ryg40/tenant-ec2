#!/usr/bin/env python3
"""Build the interactive architecture page from its reviewed graph source.

The source is docs/diagrams/architecture-graph.json. The page is docs/architecture.html:
one self-contained file with inline CSS and JavaScript. It makes no network request and
loads no external font or library; a Content-Security-Policy in the page enforces that.
The layout is computed here, so two builds of the same source give the same bytes.
Needs Python 3.10 or later and the standard library only.

Usage:
  build-architecture-html.py             write docs/architecture.html
  build-architecture-html.py --check     build in memory and compare with the file;
                                         exit 1 on a difference
  build-architecture-html.py --selftest  test the builder with its own fixture
A relative --source or --out path is relative to the root of the repository.
Exit: 0 pass, 1 a difference or a source that is not valid, 2 a usage or read error.
"""
import argparse
import base64
import contextlib
import copy
import difflib
import hashlib
import heapq
import html
import io
import json
import math
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REL = "docs/diagrams/architecture-graph.json"
OUT_REL = "docs/architecture.html"

# The geometry of the canvas, in drawing units.
NODE_W, NODE_H = 230, 58
LAYER_GAP, ROW_GAP = 64, 24
FRAME_PAD, FRAME_HEAD = 24, 40
FRAME_GAP, BAND_GAP = 56, 72
TITLE_CHAR_W = 7.2
TRACK, TRACK_MERGE, TRACKS_X, TRACKS_Y = 12, 6, 4, 5
CLEARANCE, CORNER, BEND, REUSE = 8, 10, 24, 0.9
LABEL_CHAR_W, LABEL_H = 6.4, 16

# Text limits of the source.
LABEL_CHARS, CAPTION_CHARS, EDGE_LABEL_CHARS = 28, 30, 60
SUMMARY_SENTENCES, STEP_SENTENCES = (1, 4), (1, 3)
TOP_KEYS = ("meta", "groups", "statuses", "edgeKinds", "diagrams", "frames", "layout", "nodes", "edges", "tours")
ID = re.compile(r"[a-z][a-z0-9-]*")
FRAME_ID = re.compile(r"[a-z][a-z0-9-]*/[a-z][a-z0-9-]*")
COLOUR = re.compile(r"#[0-9a-f]{6}")
PATH = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]*")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
STEP_NUMBER = re.compile(r"[0-9]+(?: and [0-9]+)?\. ")

# The colours of the page. Each pair of TEXT_PAIRS is a text colour on a background colour;
# the build refuses a pair below the WCAG AA ratio for normal text.
PAGE = {
    "bg": "#0d1117", "panel": "#131a22", "panel2": "#18212b", "field": "#0f151c",
    "button": "#1f2a36", "line": "#2a3644", "text": "#e6edf3", "muted": "#a3b3c2",
    "accent": "#6cb0ff", "frame": "#a3b3c2", "tip": "#0b0f14", "select": "#ffffff",
    "path": "#b6f05a", "match": "#ffd166", "dot": "#1c2633", "dimtext": "#a3b3c2",
}
CARD_TEXT, CARD_CAPTION = "#ffffff", "#eef2f6"
TEXT_PAIRS = (
    ("text", "bg"), ("text", "panel"), ("text", "panel2"), ("text", "field"), ("text", "button"),
    ("text", "tip"), ("muted", "bg"), ("muted", "panel"), ("muted", "panel2"), ("muted", "tip"),
    ("accent", "panel"), ("accent", "panel2"), ("frame", "bg"),
)
TEXT_RATIO, GRAPHIC_RATIO = 4.5, 3.0
# A part out of focus fades its card, not its texts: the card fill and border take the opacities
# DIM_FILL and DIM_STROKE, and the label and the caption take the colour dimtext.
# The build refuses a dim text below the AA ratio on a faded card of a group colour.
DIM_FILL, DIM_STROKE = 0.2, 0.35

# Constructs that load a resource. The page must hold none of them.
NETWORK = (
    re.compile(r"<(?:script|link|img|iframe|frame|source|video|audio|object|embed|track|use|image)\b[^>]*"
               r"\b(?:src|href|data|srcset)\s*=", re.I),
    re.compile(r"url\(\s*['\"]?(?!#)", re.I),
    re.compile(r"@import", re.I),
    re.compile(r"\b(?:fetch|importScripts|sendBeacon)\s*\(|XMLHttpRequest|WebSocket|EventSource|\bimport\s*\(", re.I),
)


class Invalid(Exception):
    """The source is not valid. The text holds one problem on each line."""


# --- colours -----------------------------------------------------------------

def luminance(colour):
    """The relative luminance of a colour #rrggbb (WCAG 2)."""
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(first, second):
    """The contrast ratio of two colours (WCAG 2), from 1 to 21."""
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def tint(colour, share):
    """The colour mixed with white; `share` is the part of white."""
    parts = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(c + (255 - c) * share):02x}" for c in parts)


def over(colour, alpha, base):
    """The colour at the opacity `alpha` over the colour `base`, as a browser mixes sRGB values."""
    top, low = ([int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in (colour, base))
    return "#" + "".join(f"{round(alpha * t + (1 - alpha) * b):02x}" for t, b in zip(top, low))


def palette_problems():
    """The text pairs of the page below the AA ratio."""
    return [f"page colour {text} on {back}: contrast {contrast(PAGE[text], PAGE[back]):.2f} is below {TEXT_RATIO}"
            for text, back in TEXT_PAIRS if contrast(PAGE[text], PAGE[back]) < TEXT_RATIO]


def dimmed_problems(colour, page=PAGE, fill=DIM_FILL):
    """The dim text below the AA ratio on a faded card of the colour, over the canvas and over its dots."""
    problems = []
    for base in ("bg", "dot"):
        back = over(colour, fill, page[base])
        ratio = contrast(page["dimtext"], back)
        if ratio < TEXT_RATIO:
            problems.append(f"dimmed card text {page['dimtext']} on {back}: contrast {ratio:.2f} is below {TEXT_RATIO}")
    return problems


# --- the source --------------------------------------------------------------

def no_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise Invalid(f"a JSON object repeats the key {duplicates[0]!r}")
    return dict(pairs)


def parse(text):
    """The graph of a source text. A repeated key in one object is not valid."""
    try:
        return json.loads(text, object_pairs_hook=no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise Invalid(f"the source is not JSON: {exc}") from None


def sentences(text):
    """The number of sentences of a text. A step number such as "4. " at the start is no sentence."""
    return len(SENTENCE_END.findall(STEP_NUMBER.sub("", text, count=1) if STEP_NUMBER.match(text) else text))


def validate(graph):
    """Raise Invalid with each problem of the graph."""
    problems = []

    def need(condition, text):
        if not condition:
            problems.append(text)
        return condition

    def text(value, limit=None):
        return isinstance(value, str) and value.strip() == value and value != "" and \
            "\n" not in value and (limit is None or len(value) <= limit)

    if not isinstance(graph, dict):
        raise Invalid("the source is not a JSON object")
    for key in TOP_KEYS:
        need(key in graph, f"the source has no key {key!r}")
    if problems:
        raise Invalid("\n".join(problems))

    def index(kind, pattern=ID):
        items = graph[kind]
        found = {}
        if not need(isinstance(items, list) and items, f"{kind}: not a list with one item or more"):
            return found
        for item in items:
            ident = item.get("id") if isinstance(item, dict) else None
            if not need(isinstance(ident, str) and pattern.fullmatch(ident), f"{kind}: the id {ident!r} is not valid"):
                continue
            need(ident not in found, f"{kind}: the id {ident} is repeated")
            found.setdefault(ident, item)
        return found

    groups, statuses, kinds = index("groups"), index("statuses"), index("edgeKinds")
    diagrams, frames, nodes = index("diagrams"), index("frames", FRAME_ID), index("nodes")
    edges, tours = index("edges"), index("tours")
    order = list(diagrams)

    meta = graph["meta"]
    if need(isinstance(meta, dict), "meta: not an object"):
        for key in ("title", "subtitle", "snapshot", "note"):
            need(text(meta.get(key), 200), f"meta: {key} is not a one-line text")
        need(isinstance(meta.get("sources"), list) and all(isinstance(p, str) and PATH.fullmatch(p) for p in meta.get("sources", ())),
             "meta: sources is not a list of paths")
    need(set(statuses) == {"core", "option"}, "statuses: the ids must be core and option")
    for status in statuses.values():
        need(status.get("border") == ("solid" if status["id"] == "core" else "dashed"),
             f"status {status['id']}: the border must be solid for core and dashed for option")
        need(text(status.get("label"), 20) and text(status.get("meaning"), 120), f"status {status['id']}: a text is not valid")
    for group in groups.values():
        colour = group.get("color", "")
        if need(isinstance(colour, str) and COLOUR.fullmatch(colour), f"group {group['id']}: the colour is not #rrggbb"):
            for foreground in (CARD_TEXT, CARD_CAPTION):
                ratio = contrast(foreground, colour)
                need(ratio >= TEXT_RATIO, f"group {group['id']}: card text {foreground} has the contrast {ratio:.2f}, below {TEXT_RATIO}")
            for problem in dimmed_problems(colour):
                need(False, f"group {group['id']}: {problem}")
        need(text(group.get("label"), 40) and text(group.get("colour"), 20), f"group {group['id']}: a text is not valid")
    for kind in kinds.values():
        colour = kind.get("color", "")
        if need(isinstance(colour, str) and COLOUR.fullmatch(colour), f"edge kind {kind['id']}: the colour is not #rrggbb"):
            ratio = contrast(colour, PAGE["bg"])
            need(ratio >= GRAPHIC_RATIO, f"edge kind {kind['id']}: the contrast {ratio:.2f} on the page is below {GRAPHIC_RATIO}")
        need(text(kind.get("label"), 20) and text(kind.get("meaning"), 120), f"edge kind {kind['id']}: a text is not valid")
    for diagram in diagrams.values():
        ident = diagram["id"]
        need(isinstance(diagram.get("number"), str) and re.fullmatch(r"[0-9]{2}", diagram.get("number", "")),
             f"diagram {ident}: the number is not two digits")
        need(text(diagram.get("title"), 40) and text(diagram.get("subtitle"), 160), f"diagram {ident}: a text is not valid")
        need(isinstance(diagram.get("source"), str) and PATH.fullmatch(diagram.get("source", "")), f"diagram {ident}: the source is not a path")
        notes = diagram.get("notes")
        need(isinstance(notes, list) and notes and all(text(note, 200) for note in notes), f"diagram {ident}: the notes are not valid")

    listed = {}
    for frame in frames.values():
        ident = frame["id"]
        need(frame.get("diagram") in diagrams, f"frame {ident}: the diagram {frame.get('diagram')!r} is not known")
        need(ident.split("/")[0] == frame.get("diagram"), f"frame {ident}: the id does not start with its diagram")
        need(text(frame.get("label"), 48), f"frame {ident}: the label is not valid")
        need(frame.get("parent") is None or frame["parent"] in frames, f"frame {ident}: the parent is not known")
        members = frame.get("nodes")
        if not need(isinstance(members, list) and members, f"frame {ident}: no node list"):
            continue
        need(len(set(members)) == len(members), f"frame {ident}: a node is repeated")
        for member in members:
            if need(member in nodes, f"frame {ident}: the node {member!r} is not known"):
                listed.setdefault(member, set()).add(frame.get("diagram"))

    for node in nodes.values():
        ident = node["id"]
        need(text(node.get("label"), LABEL_CHARS), f"node {ident}: the label is empty or longer than {LABEL_CHARS} characters")
        need(text(node.get("caption"), CAPTION_CHARS), f"node {ident}: the caption is empty or longer than {CAPTION_CHARS} characters")
        need(node.get("group") in groups, f"node {ident}: the group {node.get('group')!r} is not known")
        need(node.get("status") in statuses, f"node {ident}: the status {node.get('status')!r} is not known")
        home = node.get("frame")
        if need(home in frames, f"node {ident}: the frame {home!r} is not known"):
            need(ident in frames[home].get("nodes", ()), f"node {ident}: its frame {home} does not list it")
        shown = node.get("diagrams")
        expected = [d for d in order if d in listed.get(ident, ())]
        need(shown == expected, f"node {ident}: the diagrams {shown} are not the diagrams of the frames that list it {expected}")
        cards = node.get("cards")
        if need(isinstance(cards, dict) and list(cards) == expected, f"node {ident}: the cards are not one list for each diagram"):
            for lines in cards.values():
                need(isinstance(lines, list) and lines and all(text(line, 60) for line in lines), f"node {ident}: a card is not valid")
        summary = node.get("summary")
        low, high = SUMMARY_SENTENCES
        need(text(summary, 420) and low <= sentences(summary) <= high,
             f"node {ident}: the summary must have {low} to {high} sentences")
        sources = node.get("sources")
        need(isinstance(sources, list) and sources and all(isinstance(p, str) and PATH.fullmatch(p) for p in sources),
             f"node {ident}: the sources are not a list of paths")

    for edge in edges.values():
        ident = edge["id"]
        need(edge.get("from") in nodes, f"edge {ident}: the node {edge.get('from')!r} is not known")
        need(edge.get("to") in nodes, f"edge {ident}: the node {edge.get('to')!r} is not known")
        need(edge.get("from") != edge.get("to"), f"edge {ident}: an edge to its own node")
        need(edge.get("kind") in kinds, f"edge {ident}: the kind {edge.get('kind')!r} is not known")
        need(edge.get("status") in statuses, f"edge {ident}: the status {edge.get('status')!r} is not known")
        need(text(edge.get("label"), EDGE_LABEL_CHARS), f"edge {ident}: the label is empty or too long")
        need(isinstance(edge.get("source"), str) and PATH.fullmatch(edge.get("source", "")), f"edge {ident}: the source is not a path")

    rows = graph["layout"].get("rows") if isinstance(graph["layout"], dict) else None
    placed = []
    if need(isinstance(rows, list) and rows and all(isinstance(row, list) and row for row in rows), "layout: rows is not a list of frame lists"):
        placed = [frame for row in rows for frame in row]
        for frame in placed:
            need(frame in frames, f"layout: the frame {frame!r} is not known")
        need(len(set(placed)) == len(placed), "layout: a frame is in two places")
    for node in nodes.values():
        need(node.get("frame") in placed, f"node {node['id']}: its frame {node.get('frame')!r} is in no layout row")

    need(list(tours) == order, f"tours: one tour for each diagram, in the order of the diagrams {order}")
    for tour in tours.values():
        ident = tour["id"]
        need(tour.get("diagram") == ident, f"tour {ident}: the diagram must be {ident}")
        need(text(tour.get("label"), 40), f"tour {ident}: the label is not valid")
        steps = tour.get("steps")
        if not need(isinstance(steps, list) and steps, f"tour {ident}: no steps"):
            continue
        for number, step in enumerate(steps, 1):
            members = step.get("nodes") if isinstance(step, dict) else None
            if need(isinstance(members, list) and members, f"tour {ident} step {number}: no nodes"):
                need(len(set(members)) == len(members), f"tour {ident} step {number}: a node is repeated")
                for member in members:
                    need(member in nodes, f"tour {ident} step {number}: the node {member!r} is not known")
            low, high = STEP_SENTENCES
            words = step.get("text") if isinstance(step, dict) else None
            need(text(words, 240) and low <= sentences(words) <= high,
                 f"tour {ident} step {number}: the text must have {low} to {high} sentences")
    if problems:
        raise Invalid("\n".join(problems))


# --- the layout --------------------------------------------------------------

def reaches(successors, start, goal):
    """True when `goal` is reachable from `start`."""
    seen, stack = {start}, [start]
    while stack:
        current = stack.pop()
        if current == goal:
            return True
        for following in successors[current]:
            if following not in seen:
                seen.add(following)
                stack.append(following)
    return False


def frame_columns(members, edges):
    """The columns of one frame: a layered layout of the edges inside the frame.

    An edge that closes a cycle is left out. The layer of a node is its longest path from
    a node with no predecessor. Sweeps of the barycentre method order each column; a tie
    keeps the order of the source.
    """
    rank = {member: number for number, member in enumerate(members)}
    successors = {member: [] for member in members}
    predecessors = {member: [] for member in members}
    for edge in edges:
        first, second = edge["from"], edge["to"]
        if first in rank and second in rank and second not in successors[first] and not reaches(successors, second, first):
            successors[first].append(second)
            predecessors[second].append(first)
    waiting = {member: len(predecessors[member]) for member in members}
    ready = [member for member in members if waiting[member] == 0]
    layer = {}
    while ready:
        current = ready.pop(0)
        layer[current] = max((layer[p] + 1 for p in predecessors[current]), default=0)
        for following in successors[current]:
            waiting[following] -= 1
            if waiting[following] == 0:
                ready.append(following)
                ready.sort(key=rank.get)
    columns = [[m for m in members if layer[m] == number] for number in range(max(layer.values()) + 1)]
    for sweep in range(4):
        forward = sweep % 2 == 0
        neighbours = predecessors if forward else successors
        span = range(1, len(columns)) if forward else range(len(columns) - 2, -1, -1)
        for number in span:
            place = {m: (i + 0.5) / len(column) for column in columns for i, m in enumerate(column)}

            def centre(member):
                near = [place[n] for n in neighbours[member]]
                return sum(near) / len(near) if near else place[member]
            columns[number] = sorted(columns[number], key=centre)
    return columns


def frame_title(frame, diagrams):
    return f"{diagrams[frame['diagram']]['number']} / {frame['label']}"


def number(value):
    """A coordinate with one decimal place, as an int when it is whole."""
    value = round(value, 1) + 0.0
    return int(value) if value == int(value) else value


def layout_nodes(graph):
    """The place of each node and the rectangle of each frame that holds a node."""
    frames = {frame["id"]: frame for frame in graph["frames"]}
    diagrams = {diagram["id"]: diagram for diagram in graph["diagrams"]}
    home = {}
    for node in graph["nodes"]:
        home.setdefault(node["frame"], []).append(node["id"])
    bands = []
    for row in graph["layout"]["rows"]:
        items = []
        for ident in row:
            members = home.get(ident)
            if not members:
                continue
            columns = frame_columns(members, graph["edges"])
            title = frame_title(frames[ident], diagrams)
            inner = len(columns) * NODE_W + (len(columns) - 1) * LAYER_GAP
            width = max(inner, math.ceil(len(title) * TITLE_CHAR_W)) + 2 * FRAME_PAD
            tallest = max(len(column) for column in columns)
            height = FRAME_HEAD + tallest * NODE_H + (tallest - 1) * ROW_GAP + FRAME_PAD
            items.append((ident, members, columns, title, width, height, inner))
        if items:
            bands.append(items)
    widths = [sum(item[4] for item in items) + FRAME_GAP * (len(items) - 1) for items in bands]
    total = max(widths)
    places, rectangles, top = {}, [], 0
    for items, band_width in zip(bands, widths):
        band_height = max(item[5] for item in items)
        left = (total - band_width) / 2
        for ident, members, columns, title, width, height, inner in items:
            start = left + (width - inner) / 2
            body = band_height - FRAME_HEAD - FRAME_PAD
            for column_number, column in enumerate(columns):
                column_height = len(column) * NODE_H + (len(column) - 1) * ROW_GAP
                column_top = top + FRAME_HEAD + (body - column_height) / 2
                for row_number, member in enumerate(column):
                    places[member] = (number(start + column_number * (NODE_W + LAYER_GAP)),
                                      number(column_top + row_number * (NODE_H + ROW_GAP)))
            rectangles.append({"id": ident, "title": title, "x": number(left), "y": number(top),
                               "w": number(width), "h": number(band_height), "members": members})
            left += width + FRAME_GAP
        top += band_height + BAND_GAP
    return places, rectangles, [0, 0, number(total), number(top - BAND_GAP)]


def box(place):
    return (place[0], place[1], place[0] + NODE_W, place[1] + NODE_H)


def snap(value):
    return round(value * 2) / 2


def merged(values):
    """The sorted values without a value closer than TRACK_MERGE units to the one before it."""
    result = []
    for value in sorted(values):
        if not result or value - result[-1] >= TRACK_MERGE:
            result.append(value)
    return result


# The four directions of a move are 0 +x, 1 -x, 2 +y and 3 -y. A side of a card has the
# direction of its outward normal: 0 right, 1 left, 2 bottom, 3 top.
OPPOSITE = (1, 0, 3, 2)


class Grid:
    """The free tracks between the cards: a grid of points and of clear segments.

    Each card adds its centre lines and tracks at TRACK units beside each side. A point inside
    a block (a card with its clearance, or a frame title) is not free. Two neighbour points of
    one line are linked when no block lies between them.
    """

    def __init__(self, rects, blocks):
        xs, ys = set(), set()
        for x0, y0, x1, y1 in rects:
            xs.add(snap((x0 + x1) / 2))
            ys.add(snap((y0 + y1) / 2))
            for k in range(1, TRACKS_X + 1):
                xs.update((x0 - k * TRACK, x1 + k * TRACK))
            for k in range(1, TRACKS_Y + 1):
                ys.update((y0 - k * TRACK, y1 + k * TRACK))
        self.xs, self.ys, self.blocks = merged(xs), merged(ys), blocks
        self.links = {}
        for j, y in enumerate(self.ys):
            spans = [(b[0], b[2]) for b in blocks if b[1] < y < b[3]]
            self.link_line([((i, j), x) for i, x in enumerate(self.xs)], spans, 0)
        for i, x in enumerate(self.xs):
            spans = [(b[1], b[3]) for b in blocks if b[0] < x < b[2]]
            self.link_line([((i, j), y) for j, y in enumerate(self.ys)], spans, 2)

    def link_line(self, points, spans, forward):
        previous = None
        for point, value in points:
            if any(low < value < high for low, high in spans):
                previous = None
                continue
            if previous is not None and not any(low < value and high > previous[1] for low, high in spans):
                self.links.setdefault(previous[0], {})[forward] = point
                self.links.setdefault(point, {})[forward + 1] = previous[0]
            previous = (point, value)

    def at(self, point):
        return self.xs[point[0]], self.ys[point[1]]

    def port(self, rect, side):
        """(grid point, side point, distance) for the middle of one side of a card, or None.

        The grid point is the first point outside the clearance on the normal of the side.
        """
        x0, y0, x1, y1 = rect
        if side < 2:
            j = min(range(len(self.ys)), key=lambda n: abs(self.ys[n] - (y0 + y1) / 2))
            edge = x1 if side == 0 else x0
            line = [i for i, x in enumerate(self.xs) if (x > x1 + CLEARANCE if side == 0 else x < x0 - CLEARANCE)]
            if not line or not y0 < self.ys[j] < y1:
                return None
            point = (line[0] if side == 0 else line[-1], j)
            begin = (edge, self.ys[j])
        else:
            i = min(range(len(self.xs)), key=lambda n: abs(self.xs[n] - (x0 + x1) / 2))
            edge = y1 if side == 2 else y0
            line = [j for j, y in enumerate(self.ys) if (y > y1 + CLEARANCE if side == 2 else y < y0 - CLEARANCE)]
            if not line or not x0 < self.xs[i] < x1:
                return None
            point = (i, line[0] if side == 2 else line[-1])
            begin = (self.xs[i], edge)
        if point not in self.links:
            return None
        x, y = self.at(point)
        spans = [b for b in self.blocks if b != (x0 - CLEARANCE, y0 - CLEARANCE, x1 + CLEARANCE, y1 + CLEARANCE)]
        if any(crosses((begin, (x, y)), b) for b in spans):
            return None
        return point, begin, abs(x - begin[0]) + abs(y - begin[1])

    def route(self, source, target, use):
        """The cheapest path of grid points from a side of `source` to a side of `target`.

        A move costs its length, more for a segment that an earlier edge uses, and BEND for a
        change of direction. The search is A* with the Manhattan distance as its estimate.
        """
        goals = {}
        for side in range(4):
            port = self.port(target, side)
            if port:
                goals.setdefault(port[0], []).append((side, port))
        if not goals:
            return None
        targets = [(self.at(point), port[2]) for point, ports in goals.items() for _, port in ports]

        def estimate(point):
            x, y = self.at(point)
            return min(abs(x - gx) + abs(y - gy) + rest for (gx, gy), rest in targets)
        heap, best, came, counter = [], {}, {}, 0
        for side in range(4):
            port = self.port(source, side)
            if port:
                state = (port[0], side)
                best[state] = port[2]
                came[state] = ("start", port[1])
                heapq.heappush(heap, (port[2] + estimate(port[0]), port[2], counter, state))
                counter += 1
        while heap:
            _, cost, _, state = heapq.heappop(heap)
            if state[0] == "goal":
                goal, side, last = state[1], state[2], state[3]
                points = [goals[goal][[s for s, _ in goals[goal]].index(side)][1][1]]
                current = last
                while True:
                    points.append(self.at(current[0]))
                    previous = came[current]
                    if previous[0] == "start":
                        points.append(previous[1])
                        break
                    current = previous
                points.reverse()
                return points
            if cost > best.get(state, math.inf):
                continue
            point, direction = state
            for side, port in goals.get(point, ()):
                total = cost + port[2] + (BEND if direction != OPPOSITE[side] else 0)
                heapq.heappush(heap, (total, total, counter, ("goal", point, side, state)))
                counter += 1
            for move, neighbour in self.links.get(point, {}).items():
                if move == OPPOSITE[direction]:
                    continue
                (x, y), (nx, ny) = self.at(point), self.at(neighbour)
                key = (point, neighbour) if point < neighbour else (neighbour, point)
                step = (abs(nx - x) + abs(ny - y)) * (1 + REUSE * use.get(key, 0)) + (BEND if move != direction else 0)
                following = (neighbour, move)
                if cost + step < best.get(following, math.inf):
                    best[following] = cost + step
                    came[following] = state
                    heapq.heappush(heap, (cost + step + estimate(neighbour), cost + step, counter, following))
                    counter += 1
        return None


def crosses(segment, rect):
    """True when an axis-parallel segment passes through the inside of a rectangle."""
    (ax, ay), (bx, by) = segment
    return min(ax, bx) < rect[2] and max(ax, bx) > rect[0] and min(ay, by) < rect[3] and max(ay, by) > rect[1] \
        and (ax != bx or rect[0] < ax < rect[2]) and (ay != by or rect[1] < ay < rect[3])


def straight(points):
    """The points without a point inside a straight run."""
    result = [points[0]]
    for current, following in zip(points[1:], points[2:]):
        previous = result[-1]
        if (previous[0] == current[0] == following[0]) or (previous[1] == current[1] == following[1]):
            continue
        result.append(current)
    result.append(points[-1])
    return result


def rounded(points):
    """An SVG path through the points, with a round corner at each bend."""
    parts = [f"M{number(points[0][0])} {number(points[0][1])}"]
    for previous, corner, following in zip(points, points[1:], points[2:]):
        radius = min(CORNER, math.dist(previous, corner) / 2, math.dist(corner, following) / 2)

        def toward(end):
            share = radius / (math.dist(corner, end) or 1)
            return number(corner[0] + (end[0] - corner[0]) * share), number(corner[1] + (end[1] - corner[1]) * share)
        before, after = toward(previous), toward(following)
        parts.append(f"L{before[0]} {before[1]} Q{number(corner[0])} {number(corner[1])} {after[0]} {after[1]}")
    parts.append(f"L{number(points[-1][0])} {number(points[-1][1])}")
    return " ".join(parts)


def route_edges(graph, places, titles):
    """An orthogonal route for each edge, on the free tracks between the cards.

    An edge does not pass through a card or a frame title. A later edge avoids the segments of
    an earlier edge where a detour is cheap. The result for each edge is the SVG path, the
    place of its label and the number of other cards that the route crosses (0 by design).
    """
    rects = {ident: box(place) for ident, place in places.items()}
    blocks = [(r[0] - CLEARANCE, r[1] - CLEARANCE, r[2] + CLEARANCE, r[3] + CLEARANCE) for r in rects.values()] + titles
    grid = Grid(list(rects.values()), blocks)
    use, routes = {}, {}
    for edge in graph["edges"]:
        first, second = rects[edge["from"]], rects[edge["to"]]
        points = grid.route(first, second, use)
        if points is None:
            centre = [((r[0] + r[2]) / 2, (r[1] + r[3]) / 2) for r in (first, second)]
            points = centre
        else:
            grid_points = points[1:-1]
            for a, b in zip(grid_points, grid_points[1:]):
                pa = (grid.xs.index(a[0]), grid.ys.index(a[1]))
                pb = (grid.xs.index(b[0]), grid.ys.index(b[1]))
                key = (pa, pb) if pa < pb else (pb, pa)
                use[key] = use.get(key, 0) + 1
        points = straight(points)
        others = [r for ident, r in rects.items() if ident not in (edge["from"], edge["to"])]
        hits = sum(1 for r in others if any(crosses(s, r) for s in zip(points, points[1:])))
        routes[edge["id"]] = {"d": rounded(points), "points": points, "crossings": hits}
    placed = []
    for edge in graph["edges"]:
        route = routes[edge["id"]]
        x, y = label_place(route.pop("points"), len(edge["label"]), list(rects.values()), placed)
        route["lx"], route["ly"] = number(x), number(y + LABEL_H / 2 - 4)
    return routes


def overlap(first, second):
    """The area that two rectangles share."""
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    return width * height if width > 0 and height > 0 else 0


def label_place(points, characters, cards, placed):
    """The centre of the label of one edge: a point of its route where the label covers the
    least of the cards and of the labels before it. The label box is added to `placed`.
    """
    width = characters * LABEL_CHAR_W + 8
    best = None
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        for share, extra in ((0.5, 0), (0.3, 1), (0.7, 1), (0.15, 2), (0.85, 2)):
            x, y = a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share
            label = (x - width / 2, y - LABEL_H / 2, x + width / 2, y + LABEL_H / 2)
            cost = (sum(overlap(label, card) for card in cards) + 2 * sum(overlap(label, other) for other in placed)
                    + extra * 40 - min(length, width) * 2)
            if best is None or cost < best[0]:
                best = (cost, (x, y), label)
    placed.append(best[2])
    return best[1]


def title_block(frame):
    """The block of the title of a frame: no edge passes through it."""
    return (frame["x"] + 12, frame["y"] + 8, frame["x"] + 20 + len(frame["title"]) * TITLE_CHAR_W, frame["y"] + 32)


def geometry(graph):
    places, rectangles, bounds = layout_nodes(graph)
    rects = [box(places[node["id"]]) for node in graph["nodes"]]
    for i, first in enumerate(rects):
        for second in rects[i + 1:]:
            if first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]:
                raise AssertionError("two cards overlap")
    return {
        "node": {"w": NODE_W, "h": NODE_H},
        "nodes": {node["id"]: list(places[node["id"]]) for node in graph["nodes"]},
        "frames": rectangles,
        "edges": route_edges(graph, places, [title_block(frame) for frame in rectangles]),
        "bounds": bounds,
    }


# --- the page ----------------------------------------------------------------

CSS = r"""
:root { color-scheme: dark; }
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body {
  background: @@bg@@; color: @@text@@;
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  display: grid; height: 100vh; overflow: hidden;
  grid-template-columns: 300px minmax(0, 1fr) 360px;
  grid-template-rows: auto minmax(0, 1fr) auto;
  grid-template-areas: "top top top" "side stage detail" "side tour detail";
}
button, input, select { font: inherit; color: @@text@@; }
button {
  background: @@button@@; border: 1px solid @@line@@; border-radius: 6px; padding: 4px 10px; cursor: pointer;
}
button:hover:not(:disabled) { border-color: @@muted@@; }
button:disabled { opacity: .45; cursor: default; }
input[type=search], select {
  background: @@field@@; border: 1px solid @@line@@; border-radius: 6px; padding: 6px 8px; width: 100%;
}
:focus-visible { outline: 2px solid @@accent@@; outline-offset: 2px; }
.top {
  grid-area: top; display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 10px 16px; background: @@panel@@; border-bottom: 1px solid @@line@@;
}
.top h1 { font-size: 17px; margin: 0; }
.top p { margin: 2px 0 0; color: @@muted@@; font-size: 12.5px; }
.top .controls { display: flex; gap: 6px; align-items: center; color: @@muted@@; font-size: 12.5px; }
.top .controls .keys { margin-right: 10px; }
.side {
  grid-area: side; overflow-y: auto; background: @@panel@@; border-right: 1px solid @@line@@; padding: 12px 14px 28px;
}
.side section { margin-bottom: 18px; }
.side h2, .detail h3 {
  font-size: 11.5px; text-transform: uppercase; letter-spacing: .08em; color: @@muted@@; margin: 0 0 8px; font-weight: 650;
}
.side label.field { display: block; font-size: 12.5px; color: @@muted@@; margin: 8px 0 4px; }
.results { list-style: none; margin: 8px 0 0; padding: 0; max-height: 230px; overflow-y: auto; }
.results li { margin: 2px 0; }
.result {
  display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; background: transparent;
  border: 1px solid transparent; padding: 4px 6px;
}
.result:hover { background: @@panel2@@; border-color: @@line@@; }
.check { display: flex; align-items: center; gap: 8px; padding: 3px 0; cursor: pointer; font-size: 13.5px; }
.check input { margin: 0; accent-color: @@accent@@; }
.num { color: @@muted@@; font-variant-numeric: tabular-nums; }
.swatch { display: inline-block; width: 14px; height: 14px; border-radius: 4px; border: 1.5px solid; flex: none; }
.legend-row { display: flex; align-items: center; gap: 10px; font-size: 12.5px; margin: 6px 0; }
.border-sample { flex: none; width: 30px; height: 16px; border: 2px solid @@muted@@; border-radius: 4px; }
.border-sample.dashed { border-style: dashed; }
.line-sample { flex: none; overflow: visible; }
.line-sample line { stroke-width: 2.4; }
.line-sample line.option { stroke-dasharray: 5 4; stroke: @@muted@@; }
.row { display: flex; gap: 8px; margin-top: 10px; }
.muted { color: @@muted@@; }
.small { font-size: 12.5px; }
.note { font-size: 13px; color: @@muted@@; margin: 8px 0 4px; }
.path-steps { margin: 6px 0 0; padding-left: 20px; font-size: 13px; }
.path-steps li { margin: 6px 0; }
.stage { grid-area: stage; position: relative; min-width: 0; min-height: 0; overflow: hidden; }
#canvas { display: block; width: 100%; height: 100%; cursor: grab; touch-action: none; user-select: none; }
#canvas.panning { cursor: grabbing; }
#canvas:focus { outline: none; }
.backdrop { fill: @@bg@@; }
.dot { fill: @@dot@@; }
.frame rect { fill: #8cb4dc; fill-opacity: .035; stroke: @@line@@; stroke-width: 1.2; }
.frame-label { fill: @@frame@@; font-size: 12px; font-weight: 600; letter-spacing: .02em; }
.focus .frame rect { opacity: .55; }
.node { cursor: pointer; }
.node .card { stroke-width: 1.8; transition: fill-opacity .18s, stroke-opacity .18s; }
.node.option .card { stroke-dasharray: 7 4; }
.node:hover .card, .node:focus-visible .card { stroke-width: 3; }
.node:focus { outline: none; }
.nlabel { fill: @@cardtext@@; font-size: 13.5px; font-weight: 650; pointer-events: none; transition: fill .18s; }
.ncaption { fill: @@cardcaption@@; font-size: 11.5px; pointer-events: none; transition: fill .18s; }
.node.match .card { stroke: @@match@@; stroke-width: 3; }
.node.onpath .card { stroke: @@path@@; stroke-width: 3.2; }
.node.sel .card { stroke: @@select@@; stroke-width: 3.4; }
.focus .node:not(.on) .card { fill-opacity: @@dimfill@@; stroke-opacity: @@dimstroke@@; }
.focus .node:not(.on) .nlabel, .focus .node:not(.on) .ncaption { fill: @@dimtext@@; }
.hidden { display: none; }
.edge { fill: none; stroke-width: 1.5; opacity: .55; transition: opacity .18s; }
.edge.option { stroke-dasharray: 6 4; }
.edge.on { opacity: 1; stroke-width: 2.6; }
.focus .edge:not(.on) { opacity: .06; }
.edge.onpath { stroke: @@path@@; marker-end: url(#m-path); stroke-width: 3.2; }
.elabel { display: none; pointer-events: none; }
.elabel.on { display: inline; }
.elabel text { fill: @@text@@; font-size: 11.5px; text-anchor: middle; }
.elabel rect { fill: @@bg@@; fill-opacity: .9; stroke: @@line@@; stroke-width: 1; }
.tip {
  position: absolute; pointer-events: none; background: @@tip@@; border: 1px solid @@line@@; border-radius: 6px;
  padding: 6px 9px; font-size: 12.5px; max-width: 300px; box-shadow: 0 6px 18px rgba(0, 0, 0, .45); z-index: 5;
}
.tip strong { display: block; }
.tip span { color: @@muted@@; }
.tour {
  grid-area: tour; display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 10px 14px; background: @@panel2@@; border-top: 1px solid @@line@@; min-height: 66px;
}
.tour select { width: auto; min-width: 190px; }
.tour .count { color: @@muted@@; font-size: 12.5px; min-width: 82px; text-align: center; }
.tour p { margin: 0; flex: 1 1 320px; font-size: 14px; }
.detail {
  grid-area: detail; overflow-y: auto; background: @@panel@@; border-left: 1px solid @@line@@; padding: 14px 16px 28px;
}
.detail h2 { font-size: 19px; margin: 2px 0 8px; overflow-wrap: anywhere; }
.detail h3 { margin-top: 18px; }
.eyebrow { margin: 0; color: @@muted@@; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.chip {
  display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 2px 8px;
  border: 1px solid @@line@@; border-radius: 999px; background: @@panel2@@;
}
.chip.status-option { border-style: dashed; border-color: @@muted@@; }
.chip.status-core { border-color: @@muted@@; }
.summary { margin: 4px 0 10px; }
.cards { margin: 0; }
.cards dt { color: @@muted@@; font-size: 12.5px; margin-top: 8px; }
.cards dd { margin: 2px 0 0; }
.cards dd span { display: block; }
.plain, .notes, .edges { margin: 0; padding-left: 18px; }
.plain li, .notes li { margin: 3px 0; }
.edges { list-style: none; padding: 0; }
.edges li { margin: 6px 0; display: grid; grid-template-columns: 12px auto; column-gap: 8px; align-items: center; }
.edges .edge-meta { grid-column: 2; color: @@muted@@; font-size: 12.5px; }
.kind-dot { width: 10px; height: 10px; border-radius: 50%; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
.link {
  background: none; border: none; padding: 0; color: @@accent@@; text-align: left; text-decoration: underline;
  text-underline-offset: 2px; justify-self: start;
}
details.diagram { border-top: 1px solid @@line@@; padding: 8px 0; }
details.diagram summary { cursor: pointer; font-weight: 600; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
#side-toggle { display: none; }
@media (max-width: 1100px) {
  body { grid-template-columns: 260px minmax(0, 1fr); grid-template-areas: "top top" "side stage" "side tour" "detail detail";
    grid-template-rows: auto minmax(0, 1fr) auto 38vh; }
  .detail { border-left: none; border-top: 1px solid @@line@@; }
}
@media (max-width: 900px) {
  body { grid-template-columns: minmax(0, 1fr); grid-template-areas: "top" "stage" "tour" "detail"; }
  .top .controls .keys { display: none; }
  #side-toggle { display: inline-block; }
  .tour select { min-width: 0; flex: 1 1 100px; }
  .tour .count { min-width: 0; }
  .side {
    grid-area: 2 / 1 / -1 / 2; z-index: 3; display: none; width: min(320px, 88vw);
    box-shadow: 8px 0 22px rgba(0, 0, 0, .5);
  }
  .drawer-open .side { display: block; }
}
"""

JS = r"""
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var DATA = JSON.parse(document.getElementById("graph-data").textContent);
  var G = DATA.graph, GEO = DATA.geometry, NW = GEO.node.w, NH = GEO.node.h;

  function index(list) {
    var map = Object.create(null);
    list.forEach(function (item) { map[item.id] = item; });
    return map;
  }
  var nodes = index(G.nodes), groups = index(G.groups), diagrams = index(G.diagrams);
  var kinds = index(G.edgeKinds), statuses = index(G.statuses), tours = index(G.tours);
  var outEdges = Object.create(null), inEdges = Object.create(null);
  G.nodes.forEach(function (n) { outEdges[n.id] = []; inEdges[n.id] = []; });
  G.edges.forEach(function (e) { outEdges[e.from].push(e); inEdges[e.to].push(e); });

  var state = { selected: null, tour: null, step: 0, path: null, query: "", hideDiagram: {}, hideGroup: {} };
  var cam = { x: 0, y: 0, k: 1 }, anim = null, drag = null;
  function byId(id) { return document.getElementById(id); }
  var svg = byId("canvas"), view = byId("viewport"), tip = byId("tip");
  var nodeEl = Object.create(null), edgeEl = Object.create(null), labelEl = Object.create(null), frameEl = [];

  function make(name, attrs, parent, text) {
    var el = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (key) { el.setAttribute(key, attrs[key]); });
    if (text !== undefined) el.textContent = text;
    if (parent) parent.appendChild(el);
    return el;
  }
  function tag(name, cls, parent, text) {
    var el = document.createElement(name);
    if (cls) el.className = cls;
    if (text !== undefined) el.textContent = text;
    if (parent) parent.appendChild(el);
    return el;
  }
  function diagramName(id) { return diagrams[id].number + " " + diagrams[id].title; }
  function steps(count) { return count + (count === 1 ? " step" : " steps"); }

  // A text that is too long for its card gets a smaller font, then a shorter text.
  function fitText(el, max, size) {
    var length = el.getComputedTextLength();
    if (length <= max) return;
    el.style.fontSize = Math.max(9, Math.floor(size * max / length * 10) / 10) + "px";
    var text = el.textContent;
    while (el.getComputedTextLength() > max && text.length > 1) {
      text = text.slice(0, -1);
      el.textContent = text + "\u2026";
    }
  }

  function draw() {
    var layer = {};
    ["backdrop", "frames", "edges", "nodes", "labels"].forEach(function (name) {
      layer[name] = make("g", { "class": "layer-" + name }, view);
    });
    var b = GEO.bounds, margin = 2400;
    make("rect", { "class": "backdrop", x: b[0] - margin, y: b[1] - margin,
      width: b[2] - b[0] + 2 * margin, height: b[3] - b[1] + 2 * margin }, layer.backdrop);
    make("rect", { x: b[0] - margin, y: b[1] - margin, width: b[2] - b[0] + 2 * margin,
      height: b[3] - b[1] + 2 * margin, fill: "url(#dots)" }, layer.backdrop);
    GEO.frames.forEach(function (f) {
      var g = make("g", { "class": "frame" }, layer.frames);
      make("rect", { x: f.x, y: f.y, width: f.w, height: f.h, rx: 16 }, g);
      make("text", { "class": "frame-label", x: f.x + 16, y: f.y + 25 }, g, f.title);
      frameEl.push({ el: g, members: f.members });
    });
    G.edges.forEach(function (e) {
      var r = GEO.edges[e.id];
      edgeEl[e.id] = make("path", { d: r.d, "class": "edge k-" + e.kind + (e.status === "option" ? " option" : "") }, layer.edges);
      // The box behind a label is measured while the label is visible, then the label is hidden.
      var label = make("g", {}, layer.labels), back = make("rect", { rx: 4, height: 16, y: r.ly - 12 }, label);
      var width = make("text", { x: r.lx, y: r.ly }, label, e.label).getComputedTextLength();
      back.setAttribute("x", r.lx - width / 2 - 5);
      back.setAttribute("width", width + 10);
      label.setAttribute("class", "elabel");
      labelEl[e.id] = label;
    });
    G.nodes.forEach(function (n) {
      var p = GEO.nodes[n.id];
      var g = make("g", { "class": "node g-" + n.group + (n.status === "option" ? " option" : ""),
        transform: "translate(" + p[0] + " " + p[1] + ")", tabindex: "0", role: "button",
        "aria-label": n.label + ", " + groups[n.group].label + ", " + statuses[n.status].label, "data-id": n.id }, layer.nodes);
      make("rect", { "class": "card", width: NW, height: NH, rx: 10 }, g);
      fitText(make("text", { "class": "nlabel", x: 14, y: 25 }, g, n.label), NW - 28, 13.5);
      fitText(make("text", { "class": "ncaption", x: 14, y: 44 }, g, n.caption), NW - 28, 11.5);
      nodeEl[n.id] = g;
    });
  }

  // --- the camera ---
  function apply() {
    view.setAttribute("transform", "translate(" + cam.x.toFixed(2) + " " + cam.y.toFixed(2) + ") scale(" + cam.k.toFixed(4) + ")");
  }
  function size() {
    var r = svg.getBoundingClientRect();
    return { w: r.width || 800, h: r.height || 600, left: r.left, top: r.top };
  }
  function stop() { if (anim) { cancelAnimationFrame(anim); anim = null; } }
  function moveTo(target, animate) {
    stop();
    if (!animate) { cam = target; apply(); return; }
    var from = { x: cam.x, y: cam.y, k: cam.k }, start = null;
    function frame(now) {
      if (start === null) start = now;
      var t = Math.min(1, (now - start) / 480);
      var e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
      cam = { x: from.x + (target.x - from.x) * e, y: from.y + (target.y - from.y) * e, k: from.k + (target.k - from.k) * e };
      apply();
      anim = t < 1 ? requestAnimationFrame(frame) : null;
    }
    anim = requestAnimationFrame(frame);
  }
  function boxOf(ids) {
    var b = [Infinity, Infinity, -Infinity, -Infinity];
    ids.forEach(function (id) {
      var p = GEO.nodes[id];
      b = [Math.min(b[0], p[0]), Math.min(b[1], p[1]), Math.max(b[2], p[0] + NW), Math.max(b[3], p[1] + NH)];
    });
    return b;
  }
  function fitBox(b, maxK, animate) {
    var s = size(), pad = 56;
    var k = Math.min((s.w - 2 * pad) / Math.max(b[2] - b[0], 1), (s.h - 2 * pad) / Math.max(b[3] - b[1], 1), maxK);
    k = Math.max(k, 0.08);
    moveTo({ k: k, x: s.w / 2 - k * (b[0] + b[2]) / 2, y: s.h / 2 - k * (b[1] + b[3]) / 2 }, animate);
  }
  function fitAll(animate) {
    var ids = G.nodes.filter(function (n) { return visible(n.id); }).map(function (n) { return n.id; });
    fitBox(ids.length ? boxOf(ids) : GEO.bounds, 1.2, animate);
  }
  function centerOn(id) {
    var s = size(), p = GEO.nodes[id], k = Math.min(Math.max(cam.k, 0.9), 1.6);
    moveTo({ k: k, x: s.w / 2 - k * (p[0] + NW / 2), y: s.h / 2 - k * (p[1] + NH / 2) }, true);
  }
  function zoomBy(factor) {
    var s = size(), k = Math.min(3, Math.max(0.08, cam.k * factor));
    moveTo({ k: k, x: s.w / 2 - (s.w / 2 - cam.x) * k / cam.k, y: s.h / 2 - (s.h / 2 - cam.y) * k / cam.k }, true);
  }

  // --- the state ---
  function visible(id) {
    var n = nodes[id];
    if (state.hideGroup[n.group]) return false;
    return n.diagrams.some(function (d) { return !state.hideDiagram[d]; });
  }
  function matches(n) {
    if (!state.query) return false;
    return (n.label + "\n" + n.caption + "\n" + n.summary).toLowerCase().indexOf(state.query.toLowerCase()) !== -1;
  }
  function focus() {
    var f = { nodes: Object.create(null), edges: Object.create(null), mode: "" };
    if (state.path) {
      f.mode = "path";
      state.path.nodes.forEach(function (id) { f.nodes[id] = true; });
      state.path.steps.forEach(function (s) { f.edges[s.edge.id] = true; });
    } else if (state.selected) {
      f.mode = "select";
      f.nodes[state.selected] = true;
      outEdges[state.selected].concat(inEdges[state.selected]).forEach(function (e) {
        var other = e.from === state.selected ? e.to : e.from;
        if (visible(other)) { f.nodes[other] = true; f.edges[e.id] = true; }
      });
    } else if (state.tour) {
      f.mode = "tour";
      tours[state.tour].steps[state.step].nodes.forEach(function (id) { f.nodes[id] = true; });
      G.edges.forEach(function (e) { if (f.nodes[e.from] && f.nodes[e.to]) f.edges[e.id] = true; });
    } else if (state.query) {
      f.mode = "search";
      G.nodes.forEach(function (n) { if (matches(n)) f.nodes[n.id] = true; });
    } else {
      return null;
    }
    return f;
  }
  function update() {
    var f = focus();
    svg.classList.toggle("focus", !!f);
    G.nodes.forEach(function (n) {
      var el = nodeEl[n.id];
      el.classList.toggle("hidden", !visible(n.id));
      el.classList.toggle("on", !!(f && f.nodes[n.id]));
      el.classList.toggle("sel", state.selected === n.id);
      el.classList.toggle("onpath", !!(f && f.mode === "path" && f.nodes[n.id]));
      el.classList.toggle("match", !!(f && f.mode === "search" && f.nodes[n.id]));
    });
    G.edges.forEach(function (e) {
      var show = visible(e.from) && visible(e.to), on = show && !!(f && f.edges[e.id]);
      edgeEl[e.id].classList.toggle("hidden", !show);
      edgeEl[e.id].classList.toggle("on", on);
      edgeEl[e.id].classList.toggle("onpath", on && f.mode === "path");
      labelEl[e.id].classList.toggle("on", on);
    });
    frameEl.forEach(function (fr) { fr.el.classList.toggle("hidden", !fr.members.some(function (id) { return visible(id); })); });
    renderResults();
    renderPath();
    renderTour();
    renderDetail();
  }
  function resetFilters() {
    state.hideDiagram = {};
    state.hideGroup = {};
    Array.prototype.forEach.call(document.querySelectorAll(".side input[type=checkbox]"), function (box) { box.checked = true; });
  }
  function select(id, center) {
    if (id && !visible(id)) resetFilters();
    state.selected = id;
    update();
    if (id && center) centerOn(id);
  }

  // --- the tours ---
  function showStep() {
    var nodesOfStep = tours[state.tour].steps[state.step].nodes;
    if (!nodesOfStep.every(function (id) { return visible(id); })) resetFilters();
    update();
    fitBox(boxOf(nodesOfStep), 1.25, true);
  }
  function startTour(id) {
    state.tour = id || null;
    state.step = 0;
    state.selected = null;
    state.path = null;
    if (state.tour) showStep(); else update();
  }
  function stepTour(delta) {
    if (!state.tour) return;
    var next = state.step + delta;
    if (next < 0 || next >= tours[state.tour].steps.length) return;
    state.step = next;
    state.selected = null;
    state.path = null;
    showStep();
  }
  function renderTour() {
    var t = state.tour ? tours[state.tour] : null;
    byId("tour-select").value = state.tour || "";
    byId("tour-prev").disabled = !t || state.step === 0;
    byId("tour-next").disabled = !t || state.step === t.steps.length - 1;
    byId("tour-count").textContent = t ? "Step " + (state.step + 1) + " of " + t.steps.length : "";
    byId("tour-text").textContent = t ? t.steps[state.step].text :
      "Select a tour. It steps through one diagram, with a text for each step.";
  }

  // --- the path finder ---
  function search(from, to, directed) {
    var prev = Object.create(null), queue = [from], head = 0;
    prev[from] = null;
    while (head < queue.length) {
      var current = queue[head++];
      if (current === to) break;
      var next = outEdges[current].map(function (e) { return { edge: e, node: e.to, forward: true }; });
      if (!directed) next = next.concat(inEdges[current].map(function (e) { return { edge: e, node: e.from, forward: false }; }));
      next.forEach(function (s) {
        if (!(s.node in prev) && visible(s.node)) {
          prev[s.node] = { from: current, edge: s.edge, forward: s.forward };
          queue.push(s.node);
        }
      });
    }
    if (!(to in prev)) return null;
    var list = [], at = to;
    while (prev[at]) {
      list.unshift({ from: prev[at].from, to: at, edge: prev[at].edge, forward: prev[at].forward });
      at = prev[at].from;
    }
    return { nodes: [from].concat(list.map(function (s) { return s.to; })), steps: list, directed: directed };
  }
  function findPath() {
    var from = byId("path-from").value, to = byId("path-to").value, note = byId("path-note");
    state.path = null;
    if (!from || !to || from === to) {
      note.textContent = "Select two different parts.";
      update();
      return;
    }
    if (!visible(from) || !visible(to)) resetFilters();
    var found = search(from, to, true) || search(from, to, false);
    if (!found) {
      note.textContent = "No path connects these parts.";
      update();
      return;
    }
    state.path = found;
    state.selected = null;
    note.textContent = found.directed ?
      "The shortest path along the arrows has " + steps(found.steps.length) + "." :
      "No path follows the arrows. The shortest path against some arrows has " + steps(found.steps.length) + ".";
    update();
    fitBox(boxOf(found.nodes), 1.2, true);
  }
  function clearPath() {
    state.path = null;
    byId("path-note").textContent = "";
    byId("path-from").value = "";
    byId("path-to").value = "";
    update();
  }
  function nodeLink(parent, id) {
    var b = tag("button", "link", parent, nodes[id].label);
    b.type = "button";
    b.addEventListener("click", function () { select(id, true); });
    return b;
  }
  function renderPath() {
    var list = byId("path-steps");
    list.textContent = "";
    if (!state.path) return;
    state.path.steps.forEach(function (s) {
      var li = tag("li", "", list);
      nodeLink(li, s.from);
      tag("span", "", li, s.forward ? " \u2192 " : " \u2190 ");
      nodeLink(li, s.to);
      tag("div", "muted small", li, s.edge.label + " / " + kinds[s.edge.kind].label);
    });
  }

  // --- the panels ---
  function renderResults() {
    var list = byId("results");
    list.textContent = "";
    if (!state.query) return;
    var found = G.nodes.filter(function (n) { return visible(n.id) && matches(n); });
    if (!found.length) { tag("li", "muted small", list, "No part matches."); return; }
    found.forEach(function (n) {
      var b = tag("button", "result", tag("li", "", list));
      b.type = "button";
      tag("span", "swatch g-" + n.group, b);
      tag("span", "", b, n.label);
      b.addEventListener("click", function () { select(n.id, true); });
    });
  }
  function heading(panel, text) { tag("h3", "", panel, text); }
  function renderDetail() {
    var panel = byId("detail");
    panel.textContent = "";
    if (state.selected) nodeDetail(panel, nodes[state.selected]);
    else if (state.tour) diagramDetail(panel, diagrams[tours[state.tour].diagram]);
    else overview(panel);
  }
  function nodeDetail(panel, n) {
    tag("p", "eyebrow", panel, "Selected part");
    tag("h2", "", panel, n.label);
    var chips = tag("div", "chips", panel);
    var chip = tag("span", "chip", chips);
    tag("span", "swatch g-" + n.group, chip);
    tag("span", "", chip, groups[n.group].label);
    tag("span", "chip status-" + n.status, chips, statuses[n.status].label);
    n.diagrams.forEach(function (d) { tag("span", "chip", chips, diagramName(d)); });
    tag("p", "summary", panel, n.summary);
    heading(panel, "On the diagram cards");
    var cards = tag("dl", "cards", panel);
    n.diagrams.forEach(function (d) {
      tag("dt", "", cards, diagramName(d));
      var dd = tag("dd", "", cards);
      n.cards[d].forEach(function (line) { tag("span", "", dd, line); });
    });
    heading(panel, "Frames");
    var frames = tag("ul", "plain", panel);
    G.frames.forEach(function (f) { if (f.nodes.indexOf(n.id) !== -1) tag("li", "", frames, diagramName(f.diagram) + " / " + f.label); });
    edgeList(panel, "Edges out", outEdges[n.id], true);
    edgeList(panel, "Edges in", inEdges[n.id], false);
    heading(panel, "Sources");
    var sources = tag("ul", "plain mono", panel);
    n.sources.forEach(function (p) { tag("li", "", sources, p); });
  }
  function edgeList(panel, title, list, out) {
    heading(panel, title + " (" + list.length + ")");
    if (!list.length) { tag("p", "muted small", panel, "None."); return; }
    var ul = tag("ul", "edges", panel);
    list.forEach(function (e) {
      var li = tag("li", "", ul);
      tag("span", "kind-dot k-" + e.kind, li);
      nodeLink(li, out ? e.to : e.from);
      tag("span", "edge-meta", li, e.label + " / " + kinds[e.kind].label + (e.status === "option" ? " / option" : ""));
    });
  }
  function diagramDetail(panel, d) {
    tag("p", "eyebrow", panel, "Tour");
    tag("h2", "", panel, diagramName(d.id));
    tag("p", "summary", panel, d.subtitle);
    heading(panel, "Notes of the diagram");
    var notes = tag("ul", "notes", panel);
    d.notes.forEach(function (text) { tag("li", "", notes, text); });
    tag("p", "muted small", panel, "Source: " + d.source);
  }
  function overview(panel) {
    tag("p", "eyebrow", panel, "Overview");
    tag("h2", "", panel, G.meta.title);
    tag("p", "summary", panel, G.meta.subtitle + " " + G.meta.snapshot);
    tag("p", "muted small", panel, G.meta.note);
    tag("p", "muted small", panel, G.nodes.length + " parts, " + G.edges.length + " edges, " + G.tours.length + " tours.");
    heading(panel, "How to use the page");
    var help = tag("ul", "notes", panel);
    ["Drag the canvas to pan it. Use the mouse wheel to zoom.",
     "Select a part to read its summary and its edges.",
     "Select a tour. Then use the arrow keys or Next and Previous.",
     "The path finder shows the shortest path between two parts.",
     "Escape clears the selection."].forEach(function (text) { tag("li", "", help, text); });
    heading(panel, "The diagrams");
    G.diagrams.forEach(function (d) {
      var box = tag("details", "diagram", panel);
      tag("summary", "", box, diagramName(d.id));
      tag("p", "small", box, d.subtitle);
      var notes = tag("ul", "notes small", box);
      d.notes.forEach(function (text) { tag("li", "", notes, text); });
      var start = tag("button", "link small", box, "Start the tour");
      start.type = "button";
      start.addEventListener("click", function () { startTour(d.id); });
    });
    heading(panel, "Sources");
    var sources = tag("ul", "plain mono", panel);
    G.meta.sources.forEach(function (p) { tag("li", "", sources, p); });
  }

  function buildControls() {
    var diagramBox = byId("filter-diagrams");
    G.diagrams.forEach(function (d) {
      var label = tag("label", "check", diagramBox);
      var box = tag("input", "", label);
      box.type = "checkbox";
      box.checked = true;
      box.addEventListener("change", function () { state.hideDiagram[d.id] = !box.checked; update(); });
      tag("span", "num", label, d.number);
      tag("span", "", label, d.title);
    });
    var groupBox = byId("filter-groups");
    G.groups.forEach(function (g) {
      var label = tag("label", "check", groupBox);
      var box = tag("input", "", label);
      box.type = "checkbox";
      box.checked = true;
      box.addEventListener("change", function () { state.hideGroup[g.id] = !box.checked; update(); });
      tag("span", "swatch g-" + g.id, label);
      tag("span", "", label, g.colour + ": " + g.label);
    });
    byId("filter-reset").addEventListener("click", function () { resetFilters(); update(); fitAll(true); });
    var legend = byId("legend");
    G.statuses.forEach(function (s) {
      var row = tag("div", "legend-row", legend);
      tag("span", "border-sample " + s.border, row);
      tag("span", "", row, s.label + ". " + s.meaning);
    });
    G.edgeKinds.forEach(function (k) {
      var row = tag("div", "legend-row", legend);
      var sample = make("svg", { "class": "line-sample", width: 30, height: 12, viewBox: "0 0 30 12", "aria-hidden": "true" }, row);
      make("line", { "class": "k-" + k.id, x1: 1, y1: 6, x2: 25, y2: 6 }, sample);
      tag("span", "", row, k.label + ". " + k.meaning);
    });
    var row = tag("div", "legend-row", legend);
    var sample = make("svg", { "class": "line-sample", width: 30, height: 12, viewBox: "0 0 30 12", "aria-hidden": "true" }, row);
    make("line", { "class": "option", x1: 1, y1: 6, x2: 29, y2: 6 }, sample);
    tag("span", "", row, "A dashed line is an edge of an option.");

    var sorted = G.nodes.slice().sort(function (a, b) { return a.label.localeCompare(b.label) || (a.id < b.id ? -1 : 1); });
    ["path-from", "path-to"].forEach(function (id) {
      var choice = byId(id);
      tag("option", "", choice, "Select a part").value = "";
      sorted.forEach(function (n) { tag("option", "", choice, n.label).value = n.id; });
      choice.addEventListener("change", function () { if (byId("path-from").value && byId("path-to").value) findPath(); });
    });
    byId("path-find").addEventListener("click", findPath);
    byId("path-clear").addEventListener("click", clearPath);

    var tourChoice = byId("tour-select");
    tag("option", "", tourChoice, "Select a tour").value = "";
    G.tours.forEach(function (t) { tag("option", "", tourChoice, t.label).value = t.id; });
    tourChoice.addEventListener("change", function () { startTour(tourChoice.value); svg.focus(); });
    byId("tour-prev").addEventListener("click", function () { stepTour(-1); });
    byId("tour-next").addEventListener("click", function () { stepTour(1); });

    var field = byId("search");
    field.addEventListener("input", function () { state.query = field.value.trim(); update(); });
    field.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter") return;
      var first = G.nodes.filter(function (n) { return visible(n.id) && matches(n); })[0];
      if (first) select(first.id, true);
    });
    byId("zoom-in").addEventListener("click", function () { zoomBy(1.25); });
    byId("zoom-out").addEventListener("click", function () { zoomBy(0.8); });
    byId("zoom-fit").addEventListener("click", function () { fitAll(true); });
    byId("side-toggle").addEventListener("click", function () { setDrawer(!document.body.classList.contains("drawer-open"), true); });
    narrow.addEventListener("change", function () { setDrawer(document.body.classList.contains("drawer-open"), false); });
  }

  // --- the drawer ---
  // On a narrow screen, the sidebar is a drawer over the canvas, so the canvas keeps its width.
  // While the drawer covers them, the canvas, the tour bar and the right panel take no focus.
  var narrow = window.matchMedia("(max-width: 900px)");
  function drawerShown() { return narrow.matches && document.body.classList.contains("drawer-open"); }
  function setDrawer(open, moveFocus) {
    var toggle = byId("side-toggle");
    document.body.classList.toggle("drawer-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    var covered = drawerShown();
    [byId("stage"), document.querySelector(".tour"), byId("detail")].forEach(function (el) {
      el.toggleAttribute("inert", covered);
      if (covered) el.setAttribute("aria-hidden", "true"); else el.removeAttribute("aria-hidden");
    });
    if (moveFocus) (open ? byId("side").querySelector("input, select, button") : toggle).focus();
  }

  // --- the input ---
  function nodeOf(target) {
    var el = target && target.closest ? target.closest(".node") : null;
    return el ? el.getAttribute("data-id") : null;
  }
  function moveTip(ev) {
    if (tip.hidden) return;
    var r = byId("stage").getBoundingClientRect();
    tip.style.left = Math.min(ev.clientX - r.left + 14, r.width - 220) + "px";
    tip.style.top = (ev.clientY - r.top + 16) + "px";
  }
  function bindInput() {
    svg.addEventListener("pointerdown", function (ev) {
      if (ev.button !== 0) return;
      stop();
      drag = { x: ev.clientX, y: ev.clientY, cx: cam.x, cy: cam.y, node: nodeOf(ev.target), moved: false, id: ev.pointerId };
    });
    window.addEventListener("pointermove", function (ev) {
      if (drag) {
        var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
        if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 4) {
          drag.moved = true;
          tip.hidden = true;
          svg.classList.add("panning");
        }
        if (drag.moved) { cam = { x: drag.cx + dx, y: drag.cy + dy, k: cam.k }; apply(); }
      }
      moveTip(ev);
    });
    window.addEventListener("pointerup", function (ev) {
      if (!drag) return;
      var d = drag;
      drag = null;
      svg.classList.remove("panning");
      if (d.moved) return;
      if (d.node) select(d.node, false);
      else if (svg.contains(ev.target) && state.selected) select(null, false);
    });
    svg.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      stop();
      var s = size(), px = ev.clientX - s.left, py = ev.clientY - s.top;
      var dy = ev.deltaMode === 1 ? ev.deltaY * 16 : ev.deltaY;
      var k = Math.min(3, Math.max(0.08, cam.k * Math.exp(-dy * 0.0016)));
      cam = { x: px - (px - cam.x) * k / cam.k, y: py - (py - cam.y) * k / cam.k, k: k };
      apply();
    }, { passive: false });
    svg.addEventListener("pointerover", function (ev) {
      var id = nodeOf(ev.target);
      if (!id || drag) return;
      var n = nodes[id];
      tip.textContent = "";
      tag("strong", "", tip, n.label);
      tag("span", "", tip, groups[n.group].label + " / " + statuses[n.status].label);
      tip.hidden = false;
      moveTip(ev);
    });
    svg.addEventListener("pointerout", function (ev) {
      var id = nodeOf(ev.target);
      if (id && nodeOf(ev.relatedTarget) !== id) tip.hidden = true;
    });
    svg.addEventListener("keydown", function (ev) {
      var id = nodeOf(ev.target);
      if (id && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); select(id, true); }
    });
    document.addEventListener("keydown", function (ev) {
      var t = ev.target, field = t && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName);
      if (ev.key === "Escape") {
        if (drawerShown()) { ev.preventDefault(); setDrawer(false, true); return; }
        if (state.selected) state.selected = null;
        else if (state.path) { clearPath(); return; }
        else if (state.tour) state.tour = null;
        else if (state.query) { state.query = ""; byId("search").value = ""; }
        update();
        return;
      }
      if (field || !state.tour) return;
      if (ev.key === "ArrowRight" || ev.key === "ArrowDown") { ev.preventDefault(); stepTour(1); }
      else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") { ev.preventDefault(); stepTour(-1); }
    });
  }

  draw();
  buildControls();
  bindInput();
  update();
  fitAll(false);
  document.documentElement.setAttribute("data-ready", "true");
})();
"""

PAGE_HTML = """<!DOCTYPE html>
<!-- Built by scripts/build-architecture-html.py from docs/diagrams/architecture-graph.json. Do not edit by hand. -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="@@CSP@@">
<title>@@TITLE@@</title>
<style>@@CSS@@</style>
</head>
<body>
<header class="top">
  <div>
    <h1>@@TITLE@@</h1>
    <p>@@SUBTITLE@@</p>
  </div>
  <div class="controls">
    <button type="button" id="side-toggle" aria-controls="side" aria-expanded="false">Menu</button>
    <span class="keys">Arrow keys: tour. Escape: clear.</span>
    <button type="button" id="zoom-out" aria-label="Zoom out">-</button>
    <button type="button" id="zoom-fit">Fit</button>
    <button type="button" id="zoom-in" aria-label="Zoom in">+</button>
  </div>
</header>
<aside class="side" id="side" aria-label="Search, filters and path finder">
  <section>
    <h2><label for="search">Search</label></h2>
    <input id="search" type="search" placeholder="A label or a summary" autocomplete="off">
    <ul id="results" class="results" aria-label="Search results"></ul>
  </section>
  <section>
    <h2>Diagrams</h2>
    <div id="filter-diagrams"></div>
  </section>
  <section>
    <h2>Colour key</h2>
    <div id="filter-groups"></div>
    <div class="row"><button type="button" id="filter-reset">Show all</button></div>
  </section>
  <section>
    <h2>Legend</h2>
    <div id="legend"></div>
  </section>
  <section>
    <h2>Path finder</h2>
    <label class="field" for="path-from">Start</label>
    <select id="path-from"></select>
    <label class="field" for="path-to">End</label>
    <select id="path-to"></select>
    <div class="row">
      <button type="button" id="path-find">Find the path</button>
      <button type="button" id="path-clear">Clear</button>
    </div>
    <p id="path-note" class="note" aria-live="polite"></p>
    <ol id="path-steps" class="path-steps"></ol>
  </section>
</aside>
<main class="stage" id="stage">
  <svg id="canvas" tabindex="0" role="application" aria-label="Architecture graph">
    <defs>
@@DEFS@@
    </defs>
    <g id="viewport"></g>
  </svg>
  <div id="tip" class="tip" hidden></div>
</main>
<div class="tour" role="region" aria-label="Guided tour">
  <label class="sr" for="tour-select">Tour</label>
  <select id="tour-select"></select>
  <button type="button" id="tour-prev">Previous</button>
  <span id="tour-count" class="count"></span>
  <button type="button" id="tour-next">Next</button>
  <p id="tour-text" aria-live="polite"></p>
</div>
<aside class="detail" id="detail" aria-label="Details" aria-live="polite"></aside>
<script type="application/json" id="graph-data">
@@DATA@@
</script>
<script>@@JS@@</script>
</body>
</html>
"""


def marker(ident, colour):
    return (f'      <marker id="{ident}" viewBox="0 0 10 10" refX="9.5" refY="5" markerWidth="11" markerHeight="11" '
            f'markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0L10 5L0 10z" fill="{colour}"/></marker>')


def style_sheet(graph):
    """The CSS of the page: the fixed rules, then one rule set for each group and edge kind."""
    sheet = CSS
    for name, value in (*PAGE.items(), ("cardtext", CARD_TEXT), ("cardcaption", CARD_CAPTION),
                        ("dimfill", str(DIM_FILL)), ("dimstroke", str(DIM_STROKE))):
        sheet = sheet.replace(f"@@{name}@@", value)
    rules = []
    for group in graph["groups"]:
        line = tint(group["color"], 0.5)
        rules.append(f".g-{group['id']} .card {{ fill: {group['color']}; stroke: {line}; }}")
        rules.append(f".swatch.g-{group['id']} {{ background: {group['color']}; border-color: {line}; }}")
    for kind in graph["edgeKinds"]:
        rules.append(f".k-{kind['id']} {{ stroke: {kind['color']}; marker-end: url(#m-{kind['id']}); }}")
        rules.append(f".kind-dot.k-{kind['id']} {{ background: {kind['color']}; }}")
    return sheet + "\n".join(rules) + "\n"


def csp_hash(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii") + "'"


def network_findings(page):
    return [pattern.pattern for pattern in NETWORK if pattern.search(page)]


def build(graph):
    """The page of a graph, as text, and its geometry. Raise Invalid for a source that is not valid."""
    validate(graph)
    problems = palette_problems()
    if problems:
        raise Invalid("\n".join(problems))
    shapes = geometry(graph)
    data = json.dumps({"graph": graph, "geometry": shapes}, ensure_ascii=True, indent=1)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    css = style_sheet(graph)
    defs = "\n".join(
        [marker(f"m-{kind['id']}", kind["color"]) for kind in graph["edgeKinds"]]
        + [marker("m-path", PAGE["path"]),
           '      <pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse">'
           '<circle class="dot" cx="12" cy="12" r="1.1"/></pattern>'])
    csp = (f"default-src 'none'; script-src {csp_hash(JS)}; style-src {csp_hash(css)}; "
           "img-src 'none'; font-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'")
    page = PAGE_HTML
    for token, value in (("@@CSP@@", csp), ("@@TITLE@@", html.escape(graph["meta"]["title"])),
                         ("@@SUBTITLE@@", html.escape(graph["meta"]["subtitle"])), ("@@DEFS@@", defs),
                         ("@@CSS@@", css), ("@@JS@@", JS), ("@@DATA@@", data)):
        page = page.replace(token, value)
    if not page.isascii():
        raise AssertionError("the page is not ASCII")
    if "</script" in JS.lower() or "</style" in css.lower():
        raise AssertionError("an inline block closes its own element")
    found = network_findings(page)
    if found:
        raise AssertionError(f"the page holds a network construct: {found[0]}")
    return page, shapes


def render(graph):
    """The page of a graph, as text."""
    return build(graph)[0]


def counts(graph):
    return f"{len(graph['nodes'])} nodes, {len(graph['edges'])} edges, {len(graph['tours'])} tours"


def compare(page, path, name):
    """0 and `architecture: ok` when the file is the page; else 1 and the first differences."""
    try:
        current = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError):
        print(f"architecture: {name} is missing or not text; run python3 scripts/build-architecture-html.py")
        return 1
    if current == page:
        return 0
    print(f"architecture: {name} differs from the build of its source; run python3 scripts/build-architecture-html.py")
    lines = list(difflib.unified_diff(current.splitlines(), page.splitlines(), name, "build", n=0, lineterm=""))
    for line in lines[:40]:
        print(line if len(line) <= 200 else line[:200] + " [...]")
    if len(lines) > 40:
        print(f"[{len(lines) - 40} more diff lines]")
    return 1


# --- the self-test -----------------------------------------------------------

def fixture():
    """A small valid graph: a chain of three cards in one frame needs a bowed edge."""
    def node(ident, frame, diagrams, status="core", group="service"):
        return {"id": ident, "label": f"Node {ident}", "caption": "A caption", "group": group, "status": status,
                "frame": frame, "diagrams": diagrams, "cards": {d: [f"Node {ident}", "A line"] for d in diagrams},
                "summary": "The node is a fixture. It has two sentences.", "sources": ["README.md"]}

    def edge(ident, first, second, kind="request", status="core"):
        return {"id": ident, "from": first, "to": second, "kind": kind, "label": f"{first} to {second}",
                "status": status, "source": "README.md"}
    return {
        "meta": {"title": "Fixture graph", "subtitle": "A small graph.", "snapshot": "No deployment.",
                 "note": "Arrows show requests.", "sources": ["README.md"]},
        "groups": [{"id": "aws", "label": "AWS infrastructure", "colour": "Blue", "color": "#285b88"},
                   {"id": "service", "label": "Service", "colour": "Green", "color": "#176b5b"}],
        "statuses": [{"id": "core", "label": "Core", "meaning": "The core.", "border": "solid"},
                     {"id": "option", "label": "Option", "meaning": "An option.", "border": "dashed"}],
        "edgeKinds": [{"id": "request", "label": "Request", "color": "#79b8ff", "meaning": "A call."},
                      {"id": "dependency", "label": "Dependency", "color": "#a8b3bd", "meaning": "A need."}],
        "diagrams": [{"id": "one", "number": "01", "title": "One", "subtitle": "The first.", "source": "a.json", "notes": ["A note."]},
                     {"id": "two", "number": "02", "title": "Two", "subtitle": "The second.", "source": "b.json", "notes": ["A note."]}],
        "frames": [{"id": "one/left", "diagram": "one", "label": "Left", "nodes": ["a", "b", "c"]},
                   {"id": "two/right", "diagram": "two", "label": "Right", "nodes": ["d", "a"]}],
        "layout": {"rows": [["one/left"], ["two/right"]]},
        "nodes": [node("a", "one/left", ["one", "two"]), node("b", "one/left", ["one"], group="aws"),
                  node("c", "one/left", ["one"]), node("d", "two/right", ["two"], status="option")],
        "edges": [edge("ab", "a", "b"), edge("bc", "b", "c"), edge("ac", "a", "c", "dependency"),
                  edge("cd", "c", "d", status="option"), edge("da", "d", "a"), edge("ad", "a", "d", "dependency")],
        "tours": [{"id": "one", "label": "01 One", "diagram": "one",
                   "steps": [{"nodes": ["a", "b"], "text": "The first step."}, {"nodes": ["c"], "text": "The last step."}]},
                  {"id": "two", "label": "02 Two", "diagram": "two",
                   "steps": [{"nodes": ["d", "a"], "text": "One step."}]}],
    }


def broken(change):
    graph = fixture()
    change(graph)
    return graph


INVALID = (
    ("a repeated node id", lambda g: g["nodes"].append(copy.deepcopy(g["nodes"][0]))),
    ("an edge to an unknown node", lambda g: g["edges"][0].update(to="zz")),
    ("an edge to its own node", lambda g: g["edges"][0].update(to="a")),
    ("an unknown edge kind", lambda g: g["edges"][0].update(kind="other")),
    ("an unknown status", lambda g: g["nodes"][0].update(status="maybe")),
    ("a tour step with an unknown node", lambda g: g["tours"][0]["steps"][0]["nodes"].append("zz")),
    ("a diagram with no tour", lambda g: g["tours"].pop()),
    ("a tour of another diagram", lambda g: g["tours"][1].update(diagram="one")),
    ("a home frame that does not list its node", lambda g: g["frames"][0]["nodes"].remove("c")),
    ("node diagrams that differ from its frames", lambda g: g["nodes"][1].update(diagrams=["one", "two"])),
    ("a card list for a missing diagram", lambda g: g["nodes"][2]["cards"].update(two=["x"])),
    ("a group colour with low contrast", lambda g: g["groups"][0].update(color="#c0c0c0")),
    ("an edge colour with low contrast", lambda g: g["edgeKinds"][0].update(color="#202020")),
    ("a label longer than the card", lambda g: g["nodes"][0].update(label="x" * (LABEL_CHARS + 1))),
    ("a summary with five sentences", lambda g: g["nodes"][0].update(summary="One. Two. Three. Four. Five.")),
    ("a frame in no layout row", lambda g: g["layout"].update(rows=[["one/left"]])),
    ("an unknown frame in a layout row", lambda g: g["layout"]["rows"].append(["one/none"])),
    ("a source that is not a path", lambda g: g["nodes"][0].update(sources=["a b"])),
    ("a status with the wrong border", lambda g: g["statuses"][1].update(border="solid")),
    ("a missing key", lambda g: g.pop("tours")),
)


def selftest():
    failures, passed = [], 0

    def expect(condition, text):
        nonlocal passed
        if condition:
            passed += 1
        else:
            failures.append(text)

    graph = fixture()
    page = render(graph)
    expect(page == render(fixture()), "two builds of the fixture differ")
    expect(page.encode("ascii") == render(json.loads(json.dumps(graph))).encode("ascii"), "a JSON round trip changes the page")
    expect("Content-Security-Policy" in page and "default-src 'none'" in page, "the page has no strict CSP")
    expect(not network_findings(page), "the page holds a network construct")
    # A dimmed part fades its card with the checked opacities, and its texts keep the AA ratio.
    for group in graph["groups"]:
        expect(not dimmed_problems(group["color"]), f"group {group['id']}: a dimmed card text is below {TEXT_RATIO}")
    expect(dimmed_problems(graph["groups"][0]["color"], page=dict(PAGE, dimtext="#4a5663")),
           "the dim check accepts a dark dim text")
    expect(dimmed_problems(graph["groups"][0]["color"], fill=0.9), "the dim check accepts a card that does not fade")
    expect(f".focus .node:not(.on) .card {{ fill-opacity: {DIM_FILL}; stroke-opacity: {DIM_STROKE}; }}" in page
           and f".focus .node:not(.on) .nlabel, .focus .node:not(.on) .ncaption {{ fill: {PAGE['dimtext']}; }}" in page,
           "the page does not dim the cards with the checked values")
    expect(not re.search(r"\.focus \.node:not\(\.on\)\s*\{", page), "the page fades a whole dimmed part with its texts")
    body = page.split('<script type="application/json" id="graph-data">\n', 1)[1].split("\n</script>", 1)[0]
    data = json.loads(body)
    expect(data["graph"] == graph, "the data of the page is not the source")
    shapes = data["geometry"]
    rects = {ident: box(place) for ident, place in shapes["nodes"].items()}
    expect(len(rects) == 4, "a node has no place")
    frames = {frame["id"]: frame for frame in shapes["frames"]}
    for node in graph["nodes"]:
        frame, rect = frames[node["frame"]], rects[node["id"]]
        expect(frame["x"] <= rect[0] and rect[2] <= frame["x"] + frame["w"] and frame["y"] <= rect[1]
               and rect[3] <= frame["y"] + frame["h"], f"node {node['id']} is outside its frame")
    expect(shapes["nodes"]["a"][1] == shapes["nodes"]["b"][1] == shapes["nodes"]["c"][1]
           and shapes["nodes"]["a"][0] < shapes["nodes"]["b"][0] < shapes["nodes"]["c"][0], "the chain is not one row")
    for ident, route in shapes["edges"].items():
        expect(route["crossings"] == 0, f"edge {ident} crosses a card")
    expect(shapes["edges"]["da"]["d"] != shapes["edges"]["ad"]["d"], "two edges of one pair share one curve")
    for name, change in INVALID:
        try:
            render(broken(change))
        except Invalid:
            passed += 1
        else:
            failures.append(f"the builder accepts {name}")
    try:
        parse('{"a": 1, "a": 2}')
    except Invalid:
        passed += 1
    else:
        failures.append("the parser accepts a repeated key")
    with tempfile.TemporaryDirectory(prefix="architecture-selftest-") as temp:
        target = Path(temp) / "page.html"
        target.write_bytes(page.encode("ascii"))
        expect(compare(page, target, "page.html") == 0, "the check refuses an equal file")
        target.write_bytes(page.replace("Fixture graph", "Fixture graph!").encode("ascii"))
        report = io.StringIO()
        with contextlib.redirect_stdout(report):
            changed = compare(page, target, "page.html")
            missing = compare(page, Path(temp) / "none.html", "none.html")
        expect(changed == 1 and "differs" in report.getvalue() and "Fixture graph!" in report.getvalue(),
               "the check accepts a changed file or shows no difference")
        expect(missing == 1 and "missing" in report.getvalue(), "the check accepts a missing file")
    for line in failures:
        print(f"architecture selftest: FAIL: {line}")
    if failures:
        print(f"architecture selftest: FAIL ({len(failures)} of {passed + len(failures)} checks)")
        return 1
    print(f"architecture selftest: pass ({passed} checks, {len(INVALID) + 1} invalid fixtures refused)")
    return 0


# --- the command ---------------------------------------------------------------

def resolve(name):
    path = Path(name)
    return path if path.is_absolute() else ROOT / path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", default=SOURCE_REL, help=f"the graph source (default {SOURCE_REL})")
    parser.add_argument("--out", default=OUT_REL, help=f"the page (default {OUT_REL})")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="build in memory and compare with the page; exit 1 on a difference")
    mode.add_argument("--selftest", action="store_true", help="test the builder with its own fixture")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    source, out = resolve(args.source), resolve(args.out)
    try:
        graph = parse(source.read_bytes().decode("utf-8"))
        page, shapes = build(graph)
    except Invalid as exc:
        print(f"architecture: {args.source} is not valid:", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as exc:
        print(f"architecture: cannot read {args.source}: {exc}", file=sys.stderr)
        return 2
    if args.check:
        status = compare(page, out, args.out)
        if status == 0:
            print(f"architecture: ok ({counts(graph)})")
        return status
    out.write_bytes(page.encode("ascii"))
    crossing = sum(1 for route in shapes["edges"].values() if route["crossings"])
    print(f"architecture: wrote {args.out} ({counts(graph)}, {crossing} edges cross a card)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

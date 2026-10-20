#!/usr/bin/env python3
"""Give an archify view the look of an AWS architecture diagram.

archify (MIT license) renders a self-contained HTML viewer from a typed JSON source. This
script adds one style block and one script block to such a file, and it makes the light
theme the default. The viewer keeps each function: focus, trace, search, guided views,
themes and exports. A reader can still select the dark theme.

The look follows the usual AWS diagram: a white page, nested frames for the AWS Cloud, the
Region, the VPC and the subnets, a small badge at the corner of each frame, and each
resource as a coloured icon tile with its name below it. The tile colour is the AWS
category colour: orange for compute, green for storage, red for identity and secrets.
The glyphs are simple line drawings of this project, not the AWS icon set.

The archify component type selects the colour of a tile and of its legend entry: `cloud`
is compute (orange), `backend` storage (green), `database` data (magenta), `security`
identity and secrets (red), `frontend` AI and memory (teal), `messagebus` network and
operations (purple), `external` outside AWS (gray). The prefix of a component id selects
the glyph: `ec2_host` gets a chip and `s3_files` a bucket. The table PREFIXES lists each
prefix; a component with no known prefix gets the glyph of its type. The first words of a frame label select the
frame style: "AWS Cloud", "AWS account", "Region", "Availability Zone", "VPC", "Public
subnet", "Private subnet", "EC2 instance", "Dedicated Host", "Memory stack", "Outside AWS",
"Your computer", "Home lab", "Podman machine". A security-group frame is red. archify draws each frame 20
units below its lowest member; the script gives nested frames the same spacing at the
bottom as at the top, and moves the legend and the canvas down by the same amount. Each frame
title moves to the top-left corner of its frame.

The icon tiles, the frame styles and the frame spacing apply to architecture views only.
Another archify view, for example a workflow, gets the light default, the plain canvas,
the font and the AWS category colours.

Commands:
  apply <file.html>...   add or replace the look in each file
  --check <file.html>... exit 1 when a file does not hold the current look
  --selftest             test the script on a small built-in page

The script needs Python 3.10 or later and no other tool. It makes no network call. Two
runs on the same file give the same bytes.
"""
import re
import sys
from pathlib import Path

MARK = "tenant-ec2-aws-look"
VERSION = "1"
THEME_FROM = "window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'"
THEME_TO = "'light' /* tenant-ec2-aws-look: light by default */"

CSS = r"""
/* tenant-ec2-aws-look: the look of an AWS architecture diagram for an archify view. */
html[data-preset="classic"][data-theme="light"] {
  --cloud-stroke: #ed7100; --cloud-fill: rgba(237, 113, 0, 0.08);
  --backend-stroke: #7aa116; --backend-fill: rgba(122, 161, 22, 0.10);
  --database-stroke: #c925d1; --database-fill: rgba(201, 37, 209, 0.08);
  --security-stroke: #dd344c; --security-fill: rgba(221, 52, 76, 0.08);
  --frontend-stroke: #01a88d; --frontend-fill: rgba(1, 168, 141, 0.10);
  --messagebus-stroke: #8c4fff; --messagebus-fill: rgba(140, 79, 255, 0.08);
  --external-stroke: #545b64; --external-fill: rgba(84, 91, 100, 0.08);
}
html[data-preset="classic"][data-theme="dark"] {
  --cloud-stroke: #ff9900; --backend-stroke: #9bc53d; --database-stroke: #e05ce8; --security-stroke: #f0566b;
  --frontend-stroke: #2ed3b7; --messagebus-stroke: #a77bff; --external-stroke: #aab7b8;
}
html[data-preset="classic"][data-theme="light"] body { background: #ffffff; }
html[data-preset="classic"] .diagram-container { background: var(--panel) !important; }
html[data-preset="classic"][data-theme="light"] .diagram-container { background: #ffffff !important; }
svg[data-node-ui] rect[fill="url(#grid)"] { display: none; }
svg[data-node-ui] text { font-family: "Amazon Ember", "Helvetica Neue", Helvetica, Arial, sans-serif; letter-spacing: 0; }
svg[data-node-ui] g[data-node-id][data-aws-tile] > rect:not(.c-mask) { fill: transparent !important; stroke: transparent !important; }
svg[data-node-ui] g[data-node-id][data-aws-tile]:hover > rect:not(.c-mask),
svg[data-node-ui] g[data-node-id][data-aws-tile]:focus-visible > rect:not(.c-mask),
svg[data-node-ui] g[data-node-id][data-aws-tile][aria-pressed="true"] > rect:not(.c-mask) { stroke: #879196 !important; stroke-dasharray: 4 3; }
svg[data-node-ui] g[data-node-id][data-aws-tile] .semantic-sigil { display: none; }
html[data-theme="light"] svg[data-node-ui] g[data-node-id][data-aws-tile] > rect.c-mask { fill: #ffffff !important; stroke: #d5dbdb; stroke-width: 1px; }
html[data-theme="dark"] svg[data-node-ui] g[data-node-id][data-aws-tile] > rect.c-mask { stroke: #3b4a5c; stroke-width: 1px; }
html[data-theme="light"] svg[data-node-ui] g[data-node-id][data-aws-tile] text[data-node-label] { fill: #16191f; font-weight: 600; }
html[data-theme="light"] svg[data-node-ui] g[data-node-id][data-aws-tile] text.t-muted { fill: #545b64; }
svg[data-node-ui] rect[data-aws-frame] { stroke-width: 1.4px; stroke-dasharray: none; fill: none; }
svg[data-node-ui] rect[data-aws-frame="cloud"] { stroke: #232f3e !important; fill: none !important; }
svg[data-node-ui] rect[data-aws-frame="account"] { stroke: #e7157b !important; fill: none !important; }
svg[data-node-ui] rect[data-aws-frame="region"] { stroke: #00a4a6 !important; stroke-dasharray: 7 4 !important; fill: none !important; }
svg[data-node-ui] rect[data-aws-frame="az"] { stroke: #00a4a6 !important; stroke-dasharray: 3 3 !important; fill: none !important; }
svg[data-node-ui] rect[data-aws-frame="vpc"] { stroke: #8c4fff !important; fill: none !important; }
svg[data-node-ui] rect[data-aws-frame="public"] { stroke: #7aa116 !important; fill: #f2f6e8 !important; }
svg[data-node-ui] rect[data-aws-frame="private"] { stroke: #00a4a6 !important; fill: #e6f6f7 !important; }
svg[data-node-ui] rect[data-aws-frame="ec2"] { stroke: #ed7100 !important; fill: none !important; }
svg[data-node-ui] rect[data-aws-frame="sg"] { stroke: #dd344c !important; stroke-dasharray: 5 3 !important; fill: none !important; }
svg[data-node-ui] rect[data-aws-frame="outside"] { stroke: #7d8998 !important; fill: none !important; }
svg[data-node-ui] rect[data-aws-frame="generic"] { stroke: #7d8998 !important; stroke-dasharray: 6 4 !important; fill: none !important; }
html[data-theme="dark"] svg[data-node-ui] rect[data-aws-frame="public"] { fill: rgba(122, 161, 22, 0.12) !important; }
html[data-theme="dark"] svg[data-node-ui] rect[data-aws-frame="private"] { fill: rgba(0, 164, 166, 0.12) !important; }
svg[data-node-ui] g[data-aws-frame-label] > rect { fill: transparent !important; stroke: none !important; }
svg[data-node-ui] g[data-aws-frame-label] > text { font-size: 10.5px; font-weight: 600; }
svg[data-node-ui] g[data-aws-frame-label="cloud"] > text { fill: #232f3e !important; }
svg[data-node-ui] g[data-aws-frame-label="account"] > text { fill: #e7157b !important; }
svg[data-node-ui] g[data-aws-frame-label="region"] > text, svg[data-node-ui] g[data-aws-frame-label="az"] > text,
svg[data-node-ui] g[data-aws-frame-label="private"] > text { fill: #00828a !important; }
svg[data-node-ui] g[data-aws-frame-label="vpc"] > text { fill: #7d3cf0 !important; }
svg[data-node-ui] g[data-aws-frame-label="public"] > text { fill: #5c7d0a !important; }
svg[data-node-ui] g[data-aws-frame-label="ec2"] > text { fill: #c45a00 !important; }
svg[data-node-ui] g[data-aws-frame-label="sg"] > text { fill: #c7243a !important; }
svg[data-node-ui] g[data-aws-frame-label="outside"] > text, svg[data-node-ui] g[data-aws-frame-label="generic"] > text { fill: #545b64 !important; }
html[data-theme="dark"] svg[data-node-ui] g[data-aws-frame-label] > text { fill: #d5dbdb !important; }
html[data-theme="dark"] svg[data-node-ui] rect[data-aws-frame="cloud"] { stroke: #d5dbdb !important; }
""".strip()

JS = r"""
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  // The archify type gives the AWS category colour of the tile; the legend uses the same colours.
  var COLOR = { cloud: '#ed7100', backend: '#7aa116', database: '#c925d1', security: '#dd344c', frontend: '#01a88d',
    messagebus: '#8c4fff', external: '#545b64' };
  var GLYPH = {
    chip: 'M8.5 7.5h7a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1zM10 4v3.5M14 4v3.5M10 16.5V20M14 16.5V20M4 10h3.5M4 14h3.5M16.5 10H20M16.5 14H20',
    box: 'M12 3.5l7.5 4.2v8.6L12 20.5l-7.5-4.2V7.7zM4.5 7.7l7.5 4.2 7.5-4.2M12 11.9v8.6',
    bucket: 'M4.5 6.5a7.5 2.5 0 1 0 15 0a7.5 2.5 0 1 0-15 0zM4.5 6.5l1.8 11.8c.2 1.2 2.7 2.2 5.7 2.2s5.5-1 5.7-2.2l1.8-11.8',
    disk: 'M5 6a7 2.5 0 1 0 14 0a7 2.5 0 1 0-14 0zM5 6v12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5',
    folder: 'M3.5 7v11a1.5 1.5 0 0 0 1.5 1.5h14a1.5 1.5 0 0 0 1.5-1.5V9.5A1.5 1.5 0 0 0 19 8h-7.2L9.8 5.5H5A1.5 1.5 0 0 0 3.5 7z',
    memory: 'M5 6a7 2.5 0 1 0 14 0a7 2.5 0 1 0-14 0zM5 6v12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6M12 10l1 2.3 2.3 1-2.3 1L12 16.6l-1-2.3-2.3-1 2.3-1z',
    spark: 'M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9zM18 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z',
    key: 'M4.5 12a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0zM11.5 12h9M17 12v3M20 12v2.5',
    id: 'M4.5 5.5h15a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1zM7 11a2 2 0 1 0 4 0a2 2 0 1 0-4 0zM6 16c.6-1.4 1.6-2.1 3-2.1s2.4.7 3 2.1M14 10h4M14 13h4',
    shield: 'M12 3.5l7 3v5c0 4.3-2.9 7.6-7 9.3-4.1-1.7-7-5-7-9.3v-5zM9 12l2.2 2.2L15.5 10',
    layers: 'M12 3.5l8.5 4.5-8.5 4.5L3.5 8zM3.5 12l8.5 4.5 8.5-4.5M3.5 16l8.5 4.5 8.5-4.5',
    terminal: 'M4.5 4.5h15a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18V6a1.5 1.5 0 0 1 1.5-1.5zM7 9.5l3 2.5-3 2.5M12.5 15h4.5',
    globe: 'M3.5 12a8.5 8.5 0 1 0 17 0a8.5 8.5 0 1 0-17 0zM3.5 12h17M12 3.5c2.3 2.4 3.4 5.2 3.4 8.5s-1.1 6.1-3.4 8.5c-2.3-2.4-3.4-5.2-3.4-8.5s1.1-6.1 3.4-8.5z',
    lock: 'M6.5 10.5h11a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19v-7a1.5 1.5 0 0 1 1.5-1.5zM8 10.5V8a4 4 0 0 1 8 0v2.5',
    person: 'M8.5 8a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0zM4.5 20.5c.9-4 3.8-6.3 7.5-6.3s6.6 2.3 7.5 6.3',
    people: 'M5.5 8.5a3 3 0 1 0 6 0a3 3 0 1 0-6 0zM2.5 19.5c.7-3.4 3-5.3 6-5.3s5.3 1.9 6 5.3M14.5 6.2a3 3 0 1 1 1.6 5.6M17 14.4c2.2.5 3.7 2.2 4.3 5.1',
    window: 'M4.5 4.5h15a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18V6a1.5 1.5 0 0 1 1.5-1.5zM3 9h18',
    branch: 'M5 6a2 2 0 1 0 4 0a2 2 0 1 0-4 0zM5 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0zM15 8a2 2 0 1 0 4 0a2 2 0 1 0-4 0zM7 8v8M17 10c0 4-4 4-8 6.5',
    idp: 'M12 3.5l7 3v5c0 4.3-2.9 7.6-7 9.3-4.1-1.7-7-5-7-9.3v-5zM9.8 10.3a2.2 2.2 0 1 0 4.4 0a2.2 2.2 0 1 0-4.4 0zM8.6 16c.7-1.6 1.9-2.4 3.4-2.4s2.7.8 3.4 2.4',
    bell: 'M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15zM10 20a2 2 0 0 0 4 0',
    chart: 'M4 4v16h16M4 18l4.5-5 3.5 3 6-7.5',
    vault: 'M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM7.8 12a2.2 2.2 0 1 0 4.4 0a2.2 2.2 0 1 0-4.4 0zM12.2 12H17M15.5 12v2',
    money: 'M3.5 12a8.5 8.5 0 1 0 17 0a8.5 8.5 0 1 0-17 0zM14.8 9.2c-.5-1-1.5-1.5-2.8-1.5-1.6 0-2.8.8-2.8 2.1 0 3.1 5.8 1.6 5.8 4.4 0 1.3-1.2 2.2-3 2.2-1.4 0-2.5-.6-3-1.7M12 6v1.7M12 16.3V18',
    home: 'M4 11l8-6.5 8 6.5M6 9.5v10h12v-10M10 19.5v-5h4v5',
    flag: 'M5 21V4M5 4h11l-2 3.5 2 3.5H5',
    cloud: 'M7.5 18.5h9.5a4 4 0 0 0 .6-8 5.5 5.5 0 0 0-10.6-1.2A4.6 4.6 0 0 0 7.5 18.5z',
    unlock: 'M6.5 10.5h11a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19v-7a1.5 1.5 0 0 1 1.5-1.5zM8 10.5V8a4 4 0 0 1 7.6-1.7',
    doc: 'M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10.5a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5zM14 3.5V8h4M9 12h6M9 15h6M9 18h4',
    host: 'M4.5 5h15a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM9 19.5h6M12 16v3.5'
  };
  // The prefix of a component id, before the first underscore, selects the glyph of its tile.
  var PREFIXES = {
    ec2: 'chip', mac: 'chip', host: 'chip', svc: 'box', ctr: 'box',
    s3: 'bucket', ebs: 'disk', efs: 'folder', snap: 'disk', db: 'disk', mem: 'memory', model: 'spark', bedrock: 'spark',
    sm: 'key', key: 'key', iam: 'id', role: 'id', policy: 'shield', gate: 'lock',
    cfn: 'layers', ssm: 'terminal', logs: 'chart', alarm: 'bell', bill: 'money',
    eip: 'globe', dns: 'globe', edge: 'lock',
    you: 'person', admin: 'person', team: 'people', term: 'terminal', web: 'window',
    idp: 'idp', git: 'branch', vault: 'vault', send: 'vault', home: 'home', ext: 'cloud',
    cfg: 'doc', doc: 'doc', stack: 'layers', dh: 'host'
  };
  // The glyph of a component with no known prefix, by its archify type.
  var KIND = { cloud: 'chip', backend: 'disk', database: 'disk', security: 'shield', frontend: 'spark',
    messagebus: 'globe', external: 'cloud' };
  // The first words of a frame label: [style, badge colour, badge glyph].
  var FRAMES = [
    ['aws cloud', 'cloud', '#232f3e', 'cloud'], ['aws account', 'account', '#e7157b', 'people'],
    ['region', 'region', '#00a4a6', 'flag'], ['availability zone', 'az', null, null],
    ['vpc', 'vpc', '#8c4fff', 'cloud'], ['public subnet', 'public', '#7aa116', 'unlock'],
    ['private subnet', 'private', '#00a4a6', 'lock'], ['ec2 instance', 'ec2', '#ed7100', 'chip'],
    ['outside aws', 'outside', '#7d8998', 'home'], ['your computer', 'outside', '#7d8998', 'window'],
    ['memory stack', 'generic', '#7d8998', 'layers'], ['dedicated host', 'ec2', '#ed7100', 'host'],
    ['podman machine', 'generic', '#7d8998', 'box'],
    ['home lab', 'outside', '#7d8998', 'home']
  ];
  function make(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (parent) parent.appendChild(node);
    return node;
  }
  function badge(parent, x, y, size, colour, glyph) {
    var group = make('g', { 'data-aws-badge': '', 'aria-hidden': 'true', transform: 'translate(' + x + ' ' + y + ')' }, parent);
    make('rect', { width: size, height: size, rx: Math.round(size / 6), fill: colour }, group);
    make('path', { d: GLYPH[glyph], fill: 'none', stroke: '#ffffff', 'stroke-width': 1.7, 'stroke-linecap': 'round',
      'stroke-linejoin': 'round', transform: 'translate(' + (size * 0.12) + ' ' + (size * 0.12) + ') scale(' + (size * 0.76 / 24) + ')' }, group);
    return group;
  }
  function tiles(svg) {
    svg.querySelectorAll('g[data-node-id]').forEach(function (node) {
      if (node.hasAttribute('data-aws-tile')) return;
      var box = node.querySelector('rect:not(.c-mask)');
      var label = node.querySelector('text[data-node-label]');
      if (!box || !label) return;
      var id = node.getAttribute('data-node-id');
      var kind = node.getAttribute('data-node-kind');
      var glyph = PREFIXES[id.split('_')[0]] || KIND[kind] || 'cloud';
      var colour = COLOR[kind] || COLOR.external;
      var x = +box.getAttribute('x'), y = +box.getAttribute('y');
      var w = +box.getAttribute('width'), h = +box.getAttribute('height');
      var texts = node.querySelectorAll('text');
      var size = Math.max(26, Math.min(40, h - 40));
      if (h >= 72) {
        badge(node, x + (w - size) / 2, y + 5, size, colour, glyph);
        var line = y + 5 + size + 14;
        texts.forEach(function (text) { text.setAttribute('y', line); line += 13; });
      } else {
        size = Math.min(32, h - 12);
        badge(node, x + 8, y + (h - size) / 2, size, colour, glyph);
        var centre = x + 8 + size + (w - size - 8) / 2;
        texts.forEach(function (text) { text.setAttribute('x', centre); });
      }
      node.setAttribute('data-aws-tile', glyph);
    });
  }
  function frames(svg) {
    var styles = {};
    svg.querySelectorAll('rect[data-graph-role="structural-frame"]').forEach(function (rect) {
      var label = (rect.getAttribute('data-composition-frame-label') || '').toLowerCase();
      var style = rect.getAttribute('data-composition-frame-kind') === 'security-group' ? ['', 'sg', null, null] : null;
      FRAMES.forEach(function (entry) { if (!style && label.indexOf(entry[0]) === 0) style = entry; });
      if (!style) return;
      rect.setAttribute('data-aws-frame', style[1]);
      styles[rect.getAttribute('data-composition-frame-id')] = style;
    });
    svg.querySelectorAll('g[data-graph-role="structural-frame-label"]').forEach(function (group) {
      var style = styles[group.getAttribute('data-composition-frame-id')];
      if (!style || group.hasAttribute('data-aws-frame-label')) return;
      group.setAttribute('data-aws-frame-label', style[1]);
      var text = group.querySelector('text');
      if (!text || !style[2]) return;
      var x = +text.getAttribute('x'), y = +text.getAttribute('y');
      badge(group, x - 2, y - 12, 16, style[2], style[3]);
      text.setAttribute('x', x + 18);
    });
  }
  function apply() {
    var about = document.getElementById('archify-diagram-description');
    if (!about || about.textContent.indexOf('An architecture diagram') !== 0) return;
    document.querySelectorAll('svg[data-node-ui]').forEach(function (svg) { tiles(svg); frames(svg); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply); else apply();
  new MutationObserver(function () { apply(); }).observe(document.documentElement, { childList: true, subtree: true });
})();
""".strip()

BLOCK = (f'<style id="{MARK}-style" data-version="{VERSION}">\n{CSS}\n</style>\n'
         f'<script id="{MARK}-script" data-version="{VERSION}">\n{JS}\n</script>\n')
OLD_BLOCK = re.compile(rf'<style id="{MARK}-style"[^>]*>.*?</style>\n<script id="{MARK}-script"[^>]*>.*?</script>\n', re.S)


FRAME = re.compile(r'(<rect data-graph-role="structural-frame"[^>]*? y=")([0-9.]+)(" width="[0-9.]+" height=")([0-9.]+)(")')
FRAME_X = re.compile(r' x="([0-9.]+)" y="[0-9.]+" width="([0-9.]+)"')
LEGEND = re.compile(r'<g data-legend="" data-legend-bridge=""(?: transform="translate\(0 [0-9.]+\)")?>')
VIEWBOX = re.compile(r'(<svg viewBox="0 0 [0-9.]+ )([0-9.]+)(")')
GAP_MIN, GAP_MAX = 10, 24
# The description line of an architecture view; a workflow view gets the colours and the page only.
ARCHITECTURE = 'id="archify-diagram-description">An architecture diagram generated by Archify.'


def number(value):
    return int(value) if value == int(value) else round(value, 2)


def frame_bottoms(text):
    """Nested frames get bottom spacing like their top spacing; the legend and the canvas move down.

    archify draws each frame 20 units below its lowest member, so nested frames that share their
    lowest members end on one line. An outer frame then ends at least as far below an inner frame
    as it starts above it, within GAP_MIN and GAP_MAX. A second run changes nothing.
    """
    frames = []
    for match in FRAME.finditer(text):
        x, width = (float(v) for v in FRAME_X.search(match.group(0)).groups())
        y, height = float(match.group(2)), float(match.group(4))
        frames.append({"match": match, "x": x, "y": y, "w": width, "h": height, "bottom": y + height})
    if not frames:
        return text
    old_low = max(f["bottom"] for f in frames)
    frames.sort(key=lambda f: f["w"] * f["h"])
    for i, inner in enumerate(frames):
        for outer in frames[i + 1:]:
            if (outer["x"] <= inner["x"] + 1e-6 and outer["y"] <= inner["y"] + 1e-6
                    and outer["x"] + outer["w"] + 1e-6 >= inner["x"] + inner["w"]):
                gap = min(GAP_MAX, max(GAP_MIN, inner["y"] - outer["y"]))
                outer["bottom"] = max(outer["bottom"], inner["bottom"] + gap)
    delta = max(f["bottom"] for f in frames) - old_low
    if delta <= 0:
        return text
    pieces, last = [], 0
    for f in sorted(frames, key=lambda f: f["match"].start()):
        m = f["match"]
        pieces += [text[last:m.start()], m.group(1), m.group(2), m.group(3), str(number(f["bottom"] - f["y"])), m.group(5)]
        last = m.end()
    text = "".join(pieces) + text[last:]
    text = LEGEND.sub(lambda m: f'<g data-legend="" data-legend-bridge="" transform="translate(0 {number(delta)})">', text, count=1)
    return VIEWBOX.sub(lambda m: f"{m.group(1)}{number(float(m.group(2)) + delta)}{m.group(3)}", text, count=1)


TITLE = re.compile(r'(<g data-graph-role="structural-frame-label" data-composition-frame-id="([0-9]+)"[^>]*>\s*'
                   r'<rect data-graph-role="structural-frame-label-mask" x=")[0-9.]+(" y=")[0-9.]+("[^>]*>\s*'
                   r'<text data-boundary-label="" x=")[0-9.]+(" y=")[0-9.]+(")')
FRAME_ID = re.compile(r'data-composition-frame-id="([0-9]+)"')


def frame_titles(text):
    """Each frame title moves to the top-left corner of its frame, as in an AWS diagram.

    archify puts the title of a frame just above the members, so nested frames with the same top
    members show their titles on one line. The title mask starts 4 units below the top border and
    6 units right of the left border; the text baseline is 12 units lower.
    """
    frames = {}
    for match in FRAME.finditer(text):
        x = float(FRAME_X.search(match.group(0)).group(1))
        frames[FRAME_ID.search(match.group(0)).group(1)] = (x, float(match.group(2)))

    def place(m):
        if m.group(2) not in frames:
            return m.group(0)
        x, y = frames[m.group(2)]
        return (f"{m.group(1)}{number(x + 6)}{m.group(3)}{number(y + 4)}{m.group(4)}"
                f"{number(x + 10)}{m.group(5)}{number(y + 16)}{m.group(6)}")
    return TITLE.sub(place, text)


def skin(text):
    """The text of an archify view with the current look: the block before </body>, light theme first."""
    if 'name="generator" content="archify' not in text:
        raise ValueError("not an archify view: no archify generator line")
    text = OLD_BLOCK.sub("", text)
    if ARCHITECTURE in text:
        text = frame_titles(frame_bottoms(text))
    text = text.replace(THEME_FROM, THEME_TO)
    if text.count("</body>") != 1:
        raise ValueError("an archify view needs exactly one </body>")
    return text.replace("</body>", BLOCK + "</body>")


def main(argv):
    if argv[:1] == ["--selftest"]:
        page = ('<html data-theme="dark"><head><meta name="generator" content="archify 3.2.0"><script>var t = '
                + THEME_FROM + ';</script></head><body><svg data-node-ui=""></svg></body></html>')
        once = skin(page)
        assert skin(once) == once, "a second run changes the page"
        assert THEME_FROM not in once and THEME_TO in once, "the light default is missing"
        assert once.count(f'id="{MARK}-style"') == 1 and once.count(f'id="{MARK}-script"') == 1
        assert 'data-version' in once and "frame" not in once.split("<body>")[1].split("<style")[0]
        frame = ('<rect data-graph-role="structural-frame" data-composition-frame-kind="region" '
                 'data-composition-frame-id="{0}" data-composition-frame-label="{1}" x="{2}" y="{3}" width="{4}" '
                 'height="{5}" rx="12" class="c-region" stroke-width="1"/>')
        arch = ('<html><head><meta name="generator" content="archify 3.2.0"></head><body>'
                '<p id="archify-diagram-description">An architecture diagram generated by Archify.</p>'
                '<svg viewBox="0 0 800 600" data-node-ui="">'
                + frame.format(0, "AWS Cloud", 100, 100, 600, 380)
                + frame.format(1, "VPC", 120, 122, 300, 358)
                + frame.format(2, "EC2 instance", 140, 140, 260, 340)
                + '<g data-legend="" data-legend-bridge=""><text>Legend</text></g></svg></body></html>')
        done = skin(arch)
        assert skin(done) == done, "a second run changes an architecture page"
        assert 'height="340"' in done, "the inner frame changed"
        assert 'height="376"' in done and 'height="420"' in done, "a nested frame bottom is wrong"
        assert 'viewBox="0 0 800 640"' in done, "the canvas did not grow by the spacing"
        assert 'data-legend-bridge="" transform="translate(0 40)"' in done, "the legend did not move"
        assert skin(page.replace("<svg", '<p id="archify-diagram-description">A workflow diagram generated by '
                                 'Archify.</p><svg')).count("translate(0") == 0, "a workflow page got the frame pass"
        try:
            skin("<html><body></body></html>")
        except ValueError:
            pass
        else:
            raise AssertionError("a page without the archify generator line passes")
        print("aws look: selftest ok")
        return 0
    check = argv[:1] == ["--check"]
    files = argv[1:] if check or argv[:1] == ["apply"] else None
    if not files:
        print(__doc__.strip().split("\n\n")[-2], file=sys.stderr)
        return 2
    bad = 0
    for name in files:
        path = Path(name)
        text = path.read_text(encoding="utf-8")
        new = skin(text)
        if check:
            if new != text:
                print(f"aws look: {name} does not hold the current look; run: python3 -I {sys.argv[0]} apply {name}")
                bad += 1
        elif new != text:
            path.write_text(new, encoding="utf-8")
            print(f"aws look: applied to {name}")
        else:
            print(f"aws look: {name} already holds the current look")
    if check and not bad:
        print(f"aws look: ok ({len(files)} file(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

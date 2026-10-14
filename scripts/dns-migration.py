#!/usr/bin/env python3
"""Read the DNS records of two providers and compare them. No network call.

The helper of docs/reference/dns-migration.md: the move of a DNS zone from Cloudflare to
Porkbun. The commands of that page save each provider answer in a private directory. This
script reads those files. A command prints counts and states. It writes each record into a
file of the private directory, with the mode 0600.

Usage: dns-migration.py <command> <private directory> <work directory> [<argument>...]
       dns-migration.py --self-test

The rules of the plan file planned-records.json:
  - Each record of the complete Cloudflare list has one row, with the id of the record in
    `source`. A row has the decision copy, drop or decide. A row that the owner adds has
    the source `new`.
  - A record with no Cloudflare-only feature keeps its name, type, content and priority.
  - A record keeps its TTL. An automatic TTL and a TTL that Porkbun cannot hold are
    decisions of the owner: the script changes no TTL by itself.
  - Each host record is exactly one A record with the stack address and the TTL 600.
  - A row of a record with structured fields holds them in `data`. The gate before the
    switch needs each value of `data` in the direct answer of Porkbun.

Not verified: no real answer of a provider ran through this script. The self-test uses
fixtures of its own.
"""
import collections
import inspect
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile

SIMPLE = ("A", "AAAA", "CNAME", "MX", "TXT", "NS")
HOSTS = ("CNAME", "MX", "NS", "ALIAS", "SRV")
HINTS = (
    (".cfargotunnel.com", "Tunnel"),
    (".pages.dev", "Pages"),
    (".workers.dev", "Workers"),
    ("mx.cloudflare.net", "Email Routing"),
)
# The TTL that the kit gives to a host record (docs/options/dns.md).
HOST_TTL = 600
# Not verified: the smallest TTL that Porkbun holds.
PORKBUN_MIN_TTL = 600
# The record types that scripts/dns/porkbun.sh makes through the API. Each other type
# needs a passed probe before the API makes a record of it.
PROVEN = ("A",)
PROBE_LABEL = "dns-migration-probe"
# The place of each field of `data` in the content of an answer, for the record types whose
# form the script knows. Not verified: the names of these fields in a real list of Cloudflare.
ORDER = {"SRV": ("priority", "weight", "port", "target"), "CAA": ("flags", "tag", "value")}
PAGES = "cloudflare-records"
COUNTED = ("to-create-api.json", "cloudflare-restore.json")
PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "docs", "reference", "dns-migration.md")


def stop(text):
    sys.stderr.write("dns-migration: " + text + "\n")
    sys.exit(1)


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        stop("no JSON in " + os.path.basename(path))


def text_of(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        stop("cannot read " + os.path.basename(path))


def save(path, text):
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8") as handle:
        handle.write(text)


def number(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return -1


def form(kind, text):
    """One text form of a record content, so that both providers compare."""
    text = str(text).strip()
    if kind == "TXT":
        if len(text) > 1 and text[0] == text[-1] == '"':
            text = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', text)).replace('\\"', '"')
        return text
    text = " ".join(text.split())
    return text.rstrip(".").lower() if kind in HOSTS else text


def key(name, kind, text, prio):
    prio = number(prio) if kind in ("MX", "SRV") else 0
    return (str(name).rstrip(".").lower(), str(kind), form(kind, text), prio)


def files(out, work, env_file, *providers):
    """The credential file of each named provider, and the names of the domain."""
    found = {}
    for raw in text_of(env_file).splitlines():
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", raw.strip())
        if match:
            value = match.group(2).strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            found[match.group(1)] = value

    def need(name, pattern):
        if not re.fullmatch(pattern, found.get(name, "")):
            stop("the environment file gives no usable value for " + name)
        return found[name]

    domain = need("EC2_DOMAIN", r"[a-z0-9-]+(\.[a-z0-9-]+){2,}")
    hosts = ["pangolin"] if "pangolin" in found.get("EC2_OPTIONS", "").split(",") else []
    edge = (found.get("EC2_EDGE_NAMES") or "ok").split(",")
    hosts += [name for name in ("ok", "llm", "ov", "agent") if name in edge]
    save(os.path.join(work, "apex"), domain.split(".", 1)[1] + "\n")
    save(os.path.join(work, "host-names"), "".join(name + "." + domain + "\n" for name in hosts))
    if "cloudflare" in providers:
        token = need("EC2_CLOUDFLARE_API_TOKEN", r"[A-Za-z0-9._~+/=-]+")
        save(os.path.join(work, "cloudflare.header"), "Authorization: Bearer " + token + "\n")
        save(os.path.join(work, "zone-id"), need("EC2_CLOUDFLARE_ZONE_ID", r"[0-9a-f]{32}") + "\n")
    if "porkbun" in providers:
        pair = {"apikey": need("EC2_PORKBUN_API_KEY", r"\S+")}
        pair["secretapikey"] = need("EC2_PORKBUN_SECRET_KEY", r"\S+")
        save(os.path.join(work, "porkbun.json"), json.dumps(pair))


def apex_of(work):
    return text_of(os.path.join(work, "apex")).strip()


def stack_address(out):
    """The stack address, from the file that the lead writes into the private directory."""
    text = text_of(os.path.join(out, "stack-address.txt")).strip()
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        stop("stack-address.txt holds no IPv4 address")


# --- the Cloudflare zone -------------------------------------------------------------------


def zone_records(text):
    """The records of a zone file as (type, text). A line that is only a comment is no record.

    The text of a record keeps each token, also a comment after the record. So two exports
    compare token by token: a content that holds the text of a type name is not lost.
    """
    found, tokens, depth, owner = [], [], 0, ""
    for raw in text.splitlines():
        if not tokens and raw.lstrip()[:1] in ("", ";"):
            continue
        part = re.findall(r'"(?:[^"\\]|\\.)*"|[^\s"]+|"', raw)
        if not tokens and raw[:1] in (" ", "\t"):
            part.insert(0, owner)
        for token in part:
            if token.startswith(";"):
                break
            if not token.startswith('"'):
                depth += token.count("(") - token.count(")")
        tokens += part
        if depth > 0:
            continue
        owner, rest = tokens[0], tokens[1:]
        if owner.startswith("$"):
            kind = owner.upper()
        else:
            while rest and (rest[0].isdigit() or rest[0].upper() in ("IN", "CH", "HS")):
                rest = rest[1:]
            kind = rest[0].upper() if rest else ""
        found.append((kind, " ".join(tokens)))
        tokens, depth = [], 0
    if tokens:
        found.append(("", " ".join(tokens)))
    return found


def bind(out, work, name):
    found = [kind for kind, _ in zone_records(text_of(os.path.join(out, name))) if not kind.startswith("$")]
    if found.count("SOA") != 1:
        stop("the export does not hold exactly one SOA record: it is not a zone file")
    print("export: %d records and 1 SOA record" % (len(found) - 1))


def cloudflare(path):
    data = load(path)
    info = data.get("result_info") if isinstance(data, dict) else None
    if not isinstance(info, dict) or data.get("success") is not True \
            or not isinstance(data.get("result"), list) \
            or not isinstance(info.get("total_pages"), int) \
            or not isinstance(info.get("total_count"), int):
        stop("the Cloudflare API refuses the request, or its answer holds no page count")
    return data


def pages(out, work, prefix, page):
    data = cloudflare(os.path.join(out, "%s-%d.json" % (prefix, int(page))))
    print(max(data["result_info"]["total_pages"], 1))


def inventory(out, prefix=PAGES):
    """Each record of one complete read of the Cloudflare list, from its page files."""
    info = cloudflare(os.path.join(out, prefix + "-1.json"))["result_info"]
    found = []
    for page in range(1, max(info["total_pages"], 1) + 1):
        found += cloudflare(os.path.join(out, "%s-%d.json" % (prefix, page)))["result"]
    if not all(isinstance(record, dict) for record in found) or len(found) != info["total_count"] \
            or len({str(record.get("id")) for record in found}) != len(found):
        stop("the page files %s do not hold each record one time; run the list again" % prefix)
    return found


def fields(record):
    """The name, the type, the content form, the TTL and the priority of one record of the list."""
    name, kind = str(record.get("name", "")).rstrip(".").lower(), str(record.get("type", ""))
    return name, kind, form(kind, record.get("content", "")), number(record.get("ttl")) or 1, record.get("priority")


def structured(record):
    """The field `data` of one record of the list as one text, with each key in one order.

    It is no text for a record with no such field. So two records with the same fields of
    `data` give the same text, in each order of the keys.
    """
    data = record.get("data")
    return json.dumps(data, sort_keys=True, separators=(",", ":")) if isinstance(data, dict) and data else ""


def mark(record):
    """The identity of one record of the list: each field that its public answer depends on.

    It holds the proxy state and each field of `data`. So a change of one field of `data` is
    another record for the comparison of step D1 and for the way back of step D7b.
    """
    return (str(record.get("name", "")).rstrip(".").lower(), str(record.get("type", "")),
            str(record.get("content", "")), number(record.get("ttl")), bool(record.get("proxied")),
            str(record.get("priority", "")), structured(record))


def mark_text(item):
    return "%s %s %s, TTL %s, proxied %s, priority %s, data %s" % (item[:6] + (item[6] or "none",))


def features(record, apex):
    """Two lists for one record of the list: the Cloudflare-only features, and the TTL decisions."""
    name, kind, text, ttl, _ = fields(record)
    found = ["proxy"] if record.get("proxied") else []
    if kind == "CNAME" and name == apex:
        found.append("CNAME flattening")
    found += [label for part, label in HINTS if part in text.lower()]
    if kind == "NS" and name == apex:
        found.append("NS of the apex")
    if kind not in SIMPLE:
        found.append("write the Porkbun form by hand")
    slow = []
    if ttl == 1:
        slow.append("automatic TTL")
    elif ttl < PORKBUN_MIN_TTL:
        slow.append("TTL below %d" % PORKBUN_MIN_TTL)
    return found, slow


def host_gate(out, work, found, where):
    """Stop when a host record of `found` is not correct. host-records.txt holds each record.

    Correct is: exactly one A record, the stack address, the TTL 600, no proxy. A message
    names the first label of the record. It holds no address.
    """
    address, hosts = stack_address(out), text_of(os.path.join(work, "host-names")).split()
    if not hosts:
        stop("the environment file gives no host name")
    lines = ["Checked: " + where,
             "Expected for each host name: one A record, %s, the TTL %d, no proxy." % (address, HOST_TTL)]
    wrong = []
    for host in hosts:
        own = [record for record in found if record["name"] == host and record["type"] == "A"]
        if not own:
            state = "no A record"
        elif len(own) > 1:
            state = "%d A records" % len(own)
        elif own[0]["content"] != address:
            state = "another address"
        elif own[0]["ttl"] != HOST_TTL:
            state = "the TTL is not %d" % HOST_TTL
        elif own[0].get("proxied"):
            state = "the proxy is on"
        else:
            state = "correct"
        lines.append("%s: %s" % (host, state))
        lines += ["    A %s, TTL %s, proxy %s" % (record["content"], record["ttl"],
                                                "on" if record.get("proxied") else "off") for record in own]
        if state != "correct":
            wrong.append("host record %s: %s" % (host.split(".", 1)[0], state))
    save(os.path.join(out, "host-records.txt"), "\n".join(lines) + "\n")
    for line in wrong:
        sys.stderr.write("dns-migration: " + line + "\n")
    if wrong:
        stop("%d of %d host records are not correct in %s; host-records.txt holds each record"
             % (len(wrong), len(hosts), where))
    return len(hosts)


def plan(out, work):
    """inventory.txt, ttl-decisions.txt and planned-records.json from the page files of the list."""
    target = os.path.join(out, "planned-records.json")
    if os.path.exists(target):
        stop("planned-records.json exists and can hold decisions; move it away first")
    apex, found = apex_of(work), inventory(out)
    if not found:
        stop("the list of the zone holds no record")
    rows, slow_rows = [], []
    order = sorted(found, key=lambda r: (str(r.get("name")), str(r.get("type")), str(r.get("content"))))
    for row_number, record in enumerate(order, 1):
        name, kind, text, ttl, prio = fields(record)
        if name != apex and not name.endswith("." + apex):
            stop("the list holds a record outside the apex domain")
        feature, slow = features(record, apex)
        rows.append({"row": row_number, "source": str(record.get("id")), "name": name, "type": kind,
                     "content": text, "ttl": ttl, "prio": prio, "proxied": bool(record.get("proxied")),
                     "feature": ", ".join(feature + slow), "decision": "decide" if feature or slow else "copy"})
        if structured(record):
            rows[-1]["data"] = record["data"]
        if slow:
            slow_rows.append("row %d: %s %s, %s%s" % (row_number, kind, name, slow[0],
                                                     ", with another decision" if feature else ""))
    table = ["| Row | Name | Type | Content | TTL | Proxied | Cloudflare-only feature | Decision |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        table.append("| %d | %s | %s | %s | %s | %s | %s | %s |" % (
            row["row"], row["name"], row["type"], row["content"].replace("|", "\\|"),
            "automatic" if row["ttl"] == 1 else row["ttl"],
            "yes" if row["proxied"] else "no", row["feature"] or "none", row["decision"]))
    save(os.path.join(out, "inventory.txt"), "\n".join(table) + "\n")
    save(os.path.join(out, "ttl-decisions.txt"), "".join(line + "\n" for line in slow_rows))
    hosts = host_gate(out, work, rows, "the Cloudflare zone")
    save(target, json.dumps(rows, indent=2) + "\n")
    print("plan: %d records, %d copy, %d decide; TTL decisions: %d; host records: %d of %d" % (
        len(rows), sum(row["decision"] == "copy" for row in rows),
        sum(row["decision"] == "decide" for row in rows), len(slow_rows), hosts, hosts))


def plan_rows(out):
    rows = load(os.path.join(out, "planned-records.json"))
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        stop("planned-records.json is not a list of rows")
    return rows


def ttl(out, work, seconds):
    """Write the one TTL that the owner decides into each row that waits for its TTL only."""
    if not re.fullmatch(r"[1-9][0-9]{1,8}", seconds):
        stop("the TTL is not a number of seconds above 9")
    apex, rows = apex_of(work), plan_rows(out)
    source = {str(record.get("id")): record for record in inventory(out)}
    changed = 0
    for row in rows:
        record = source.get(str(row.get("source")))
        if row.get("decision") != "decide" or record is None:
            continue
        feature, slow = features(record, apex)
        if slow and not feature:
            row["ttl"], row["decision"] = int(seconds), "copy"
            changed += 1
    save(os.path.join(out, "planned-records.json"), json.dumps(rows, indent=2) + "\n")
    print("ttl: %d rows have the TTL %s and the decision copy" % (changed, seconds))


def planned(out, work):
    """The rows of the plan file with the decision copy, after each check of the plan.

    The plan must hold one row for each record of the complete Cloudflare list. A record
    with no row and no decision drop stops the command, with a count. An empty plan stops.
    """
    apex, rows = apex_of(work), plan_rows(out)
    source = {str(record.get("id")): record for record in inventory(out)}
    if not rows:
        stop("the plan is empty: %d records of the inventory have no row" % len(source))
    still = sum(row.get("decision") == "decide" for row in rows)
    if still:
        stop("%d rows still have the decision decide" % still)
    linked = set()
    for row in rows:
        label, origin = "row %s" % row.get("row"), str(row.get("source", ""))
        if row.get("decision") not in ("copy", "drop"):
            stop(label + ": the decision is not copy or drop")
        if origin == "new":
            if row["decision"] == "drop":
                stop(label + ": a new row has the decision copy; remove a row that is not necessary")
            continue
        if origin not in source:
            stop(label + ": the source is not the id of a record of the inventory, and not the text new")
        if origin in linked:
            stop(label + ": a second row for one record of the inventory; a new record has the source new")
        linked.add(origin)
    if len(linked) != len(source):
        stop("%d records of the inventory have no row in the plan and no decision drop"
             % (len(source) - len(linked)))
    keep, seen = [], set()
    for row in rows:
        label, origin = "row %s" % row.get("row"), str(row.get("source", ""))
        if row["decision"] == "drop":
            continue
        name = str(row.get("name", ""))
        if name != name.lower().rstrip(".") or (name != apex and not name.endswith("." + apex)):
            stop(label + ": the name is not a name of the apex domain in lower case")
        if not re.fullmatch(r"[A-Z]+", str(row.get("type", ""))) or not str(row.get("content", "")):
            stop(label + ": the type or the content is not usable")
        if not isinstance(row.get("ttl"), int) or isinstance(row["ttl"], bool) or row["ttl"] < 2:
            stop(label + ": the TTL is not a number of seconds; 1 is the automatic TTL of Cloudflare")
        if row["type"] in ("MX", "SRV") and not isinstance(row.get("prio"), int):
            stop(label + ": an MX or SRV row needs a number in prio")
        if not isinstance(row.get("data", {}), dict):
            stop(label + ": the field data is not a set of named values")
        mine = key(name, row["type"], row["content"], row.get("prio"))
        if origin != "new":
            was_name, was_kind, was_text, was_ttl, was_prio = fields(source[origin])
            feature, slow = features(source[origin], apex)
            if not feature and mine != key(was_name, was_kind, was_text, was_prio):
                stop(label + ": the record differs from the inventory and has no Cloudflare-only feature; "
                     "a change that the owner decides is the decision drop and a new row")
            if not slow and row["ttl"] != was_ttl:
                stop(label + ": the TTL differs from the inventory; each TTL that Porkbun can hold stays")
        if mine in seen:
            stop(label + ": a second copy of another row")
        seen.add(mine)
        keep.append(row)
    if not keep:
        stop("the plan copies no record")
    host_gate(out, work, [dict(row, proxied=False) for row in keep], "the plan")
    return keep


def unchanged(out, work):
    """Stop when the Cloudflare zone changed since Phase A.

    It compares the two exports record by record, without the SOA records only. It compares
    the two reads of the JSON list with the proxy state and each field of `data`.
    zone-changes.txt holds each difference.
    """
    lines = []

    def differences(label, before, after):
        gone, new = before - after, after - before
        lines.extend("%s, only in Phase A: %s" % (label, item) for item in sorted(gone.elements()))
        lines.extend("%s, only now: %s" % (label, item) for item in sorted(new.elements()))
        return sum(gone.values()) + sum(new.values())

    exports = [collections.Counter(text for kind, text in zone_records(text_of(os.path.join(out, name)))
                                   if kind != "SOA")
               for name in ("cloudflare-zone.bind", "cloudflare-zone-final.bind")]
    lists = [collections.Counter(mark_text(mark(record)) for record in inventory(out, prefix))
             for prefix in (PAGES, "cloudflare-final")]
    in_export, in_list = differences("export", *exports), differences("list", *lists)
    save(os.path.join(out, "zone-changes.txt"), "".join(line + "\n" for line in lines) or "no difference\n")
    if in_export or in_list:
        stop("the zone CHANGED since the export: %d differences in the export, %d in the list with the "
             "proxy state; zone-changes.txt holds each one" % (in_export, in_list))
    print("zone: no record changed since the export (%d records, with the proxy state)"
          % sum(lists[0].values()))


def restore(out, work):
    """cloudflare-restore.json: one request body for each record of the inventory that a new zone lacks.

    A body holds the proxy state and the TTL of the list, also the automatic TTL. The read of
    the new zone is in the page files cloudflare-restored-<page>.json. A record of the new zone
    with another value in one field of `data` is not the record of the inventory.
    """
    there = collections.Counter(mark(record) for record in inventory(out, "cloudflare-restored"))
    bodies, lines = [], []
    for record in sorted(inventory(out), key=mark):
        if there[mark(record)] > 0:
            there[mark(record)] -= 1
            continue
        item = {"type": str(record.get("type", "")), "name": str(record.get("name", "")),
                "ttl": number(record.get("ttl")) or 1, "proxied": bool(record.get("proxied"))}
        if isinstance(record.get("data"), dict) and record["data"]:
            item["data"] = record["data"]
        else:
            item["content"] = str(record.get("content", ""))
            if record.get("priority") is not None:
                item["priority"] = record["priority"]
        bodies.append(item)
        lines.append("to make: %s %s, proxied %s" % (item["type"], item["name"], item["proxied"]))
    extra = sorted((+there).elements())
    lines += ["only in the new zone: " + mark_text(item) for item in extra]
    save(os.path.join(out, "cloudflare-restore.json"), json.dumps(bodies, indent=2) + "\n")
    save(os.path.join(out, "restore-diff.txt"), "".join(line + "\n" for line in lines) or "no difference\n")
    print("restore: %d records to make, %d of them with the proxy on; %d records only in the new zone"
          % (len(bodies), sum(item["proxied"] for item in bodies), len(extra)))


def restore_body(out, work, index):
    """The request body of one record of cloudflare-restore.json. It holds no credential."""
    save(os.path.join(work, "body.json"), json.dumps(load(os.path.join(out, "cloudflare-restore.json"))[int(index)]))


def cloudflare_answer(out, work):
    """Stop when Cloudflare refuses the last request. A message never shows the token."""
    data = load(os.path.join(work, "answer.json"))
    if isinstance(data, dict) and data.get("success") is True:
        return
    errors = data.get("errors") if isinstance(data, dict) else None
    text = "; ".join(str(e.get("message", "no message")) for e in errors
                     if isinstance(e, dict)) if isinstance(errors, list) else ""
    token = text_of(os.path.join(work, "cloudflare.header")).strip().rpartition(" ")[2]
    if token:
        text = text.replace(token, "<token>")
    stop("the Cloudflare API refuses the request: " + (text or "no message")[:300])


# --- the Porkbun zone ----------------------------------------------------------------------


def porkbun(path, work):
    """The answer of the Porkbun API as a dict. A message never shows a key."""
    data = load(path)
    if not isinstance(data, dict) or data.get("status") != "SUCCESS":
        text = str(data.get("message", "no message")) if isinstance(data, dict) else "no message"
        for value in load(os.path.join(work, "porkbun.json")).values():
            text = text.replace(str(value), "<key>")
        stop("the Porkbun API refuses the request: " + text[:300])
    return data


def zone(out, work, name):
    found = porkbun(os.path.join(out, name), work).get("records")
    if not isinstance(found, list) or not all(isinstance(r, dict) for r in found):
        stop("the answer of the Porkbun API holds no list of records")
    return found


def state(out, work, name):
    found, kinds = zone(out, work, name), {}
    for record in found:
        kinds[str(record.get("type"))] = kinds.get(str(record.get("type")), 0) + 1
    wild = sum(str(record.get("name", "")).startswith("*.") for record in found)
    print("porkbun: %d records (%s); wildcard names: %d" % (
        len(found), ", ".join("%s %d" % item for item in sorted(kinds.items())) or "none", wild))


def diff(out, work, zone_name, result_name):
    """Compare the plan file with one read of the Porkbun zone, record by record.

    It writes to-create.json and the two lists of `batch` from the same read. So no list
    of an earlier read stays for the create loop.
    """
    keep, there = planned(out, work), {}
    for record in zone(out, work, zone_name):
        mine = key(record.get("name"), record.get("type"), record.get("content"), record.get("prio"))
        there.setdefault(mine, []).append(record)
    counts = {"same": 0, "ttl differs": 0, "duplicate": 0, "create": 0, "only at Porkbun": 0}
    lines, create, marks, probes = [], [], set(), 0
    for row in keep:
        mine = key(row["name"], row["type"], row["content"], row.get("prio"))
        marks.add(mine)
        if mine not in there:
            status = "create"
            create.append(row)
        elif len(there[mine]) > 1:
            status = "duplicate"
        elif number(there[mine][0].get("ttl")) != row["ttl"]:
            status = "ttl differs"
        else:
            status = "same"
        counts[status] += 1
        lines.append("%s: row %s %s %s" % (status, row.get("row"), row["type"], row["name"]))
    names_of_plan = {row["name"] for row in keep}
    for mine in sorted(there):
        if mine not in marks:
            counts["only at Porkbun"] += len(there[mine])
            note = " (a planned record has this name)" if mine[0] in names_of_plan else ""
            if any(label.startswith(PROBE_LABEL) for label in mine[0].split(".")):
                probes += len(there[mine])
                note = " (a probe record: remove it)"
            lines.append("only at Porkbun: %s %s %s%s" % (mine[1], mine[0], mine[2], note))
    save(os.path.join(out, result_name), "\n".join(lines) + "\n")
    save(os.path.join(out, "to-create.json"), json.dumps(create, indent=2) + "\n")
    print("diff: " + ", ".join("%d %s" % (counts[name], name) for name in counts))
    batch(out, create)
    if probes:
        stop("%d probe records are still in the Porkbun zone; remove each one before the next step" % probes)


def to_create(out):
    rows = load(os.path.join(out, "to-create.json"))
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        stop("to-create.json is not a list of rows")
    return rows


def passed(out):
    """The record types with a passed probe, from probes.json of the private directory."""
    path = os.path.join(out, "probes.json")
    data = load(path) if os.path.exists(path) else {}
    if not isinstance(data, dict):
        stop("probes.json is not a list of results")
    return {kind for kind, item in data.items() if isinstance(item, dict) and item.get("result") == "passed"}


def probe_result(out, kind, reason):
    path = os.path.join(out, "probes.json")
    data = load(path) if os.path.exists(path) else {}
    data[kind] = {"result": "failed" if reason else "passed", "reason": reason}
    save(path, json.dumps(data, indent=2) + "\n")
    print("probe %s: %s" % (kind, "FAILED (%s)" % reason if reason else "passed"))


def probe_types(out, work):
    """The record types of to-create.json that need a probe: each type but A with no passed probe."""
    kinds = sorted({str(row.get("type")) for row in to_create(out)} - set(PROVEN) - passed(out))
    save(os.path.join(work, "probe-types"), "".join(kind + "\n" for kind in kinds))
    print("probe: %d types wait for a probe%s" % (len(kinds), ": " + ", ".join(kinds) if kinds else ""))


def request(work, apex, row):
    """The request body of one new record, with the key pair, in the work directory."""
    data = load(os.path.join(work, "porkbun.json"))
    name = "" if row["name"] == apex else row["name"][:-len(apex) - 1]
    data.update({"name": name, "type": row["type"], "content": row["content"], "ttl": str(row["ttl"])})
    if row["type"] in ("MX", "SRV"):
        data["prio"] = str(row["prio"])
    save(os.path.join(work, "body.json"), json.dumps(data))


def probe_body(out, work, kind):
    """The request body of the probe record of one type: a harmless name, the TTL 600.

    The content and the priority come from the first row of that type in to-create.json. The
    name is the label dns-migration-probe-<type> below the apex domain. Service labels that
    start with "_" stay in front of it.
    """
    apex = apex_of(work)
    rows = [row for row in to_create(out) if row.get("type") == kind]
    if not re.fullmatch(r"[A-Z]+", kind) or kind in PROVEN or not rows:
        stop("no record of the type %s waits for a probe" % kind)
    sub = rows[0]["name"][:-len(apex) - 1] if rows[0]["name"] != apex else ""
    service = []
    for label in sub.split("."):
        if not label.startswith("_"):
            break
        service.append(label)
    name = ".".join(service + ["%s-%s" % (PROBE_LABEL, kind.lower()), apex])
    row = {"name": name, "type": kind, "content": rows[0]["content"], "prio": rows[0].get("prio"),
           "ttl": HOST_TTL}
    save(os.path.join(work, "probe.json"), json.dumps(row))
    request(work, apex, row)


def probe_answer(out, work, kind):
    """The exit status 0 when Porkbun made the probe record. A refusal is a result of the probe."""
    data = load(os.path.join(work, "answer.json"))
    if not isinstance(data, dict) or data.get("status") != "SUCCESS":
        probe_result(out, kind, "the API refuses the create request")
        sys.exit(1)


def probe_compare(out, work, kind, name):
    """Compare the probe record in one read of the zone with the request. Write its id for the removal."""
    sent = load(os.path.join(work, "probe.json"))
    if not isinstance(sent, dict) or sent.get("type") != kind:
        stop("the probe file holds another type")
    own = [record for record in zone(out, work, name)
           if str(record.get("name", "")).rstrip(".").lower() == sent["name"] and record.get("type") == kind]
    ids = [str(record.get("id", "")) for record in own]
    if not all(re.fullmatch(r"[0-9]+", one) for one in ids):
        stop("the read of the zone holds no usable id for the probe record")
    save(os.path.join(work, "probe-ids"), "".join(one + "\n" for one in ids))
    if len(own) != 1:
        reason = "the zone holds %d records with the probe name" % len(own)
    elif key(own[0].get("name"), kind, own[0].get("content"), own[0].get("prio")) \
            != key(sent["name"], kind, sent["content"], sent.get("prio")):
        reason = "Porkbun holds another content or priority"
    elif number(own[0].get("ttl")) != sent["ttl"]:
        reason = "Porkbun holds another TTL"
    else:
        reason = ""
    probe_result(out, kind, reason)


def batch(out, rows):
    """Split the rows to create. The API makes a type that the kit proves or that has a passed probe."""
    done = set(PROVEN) | passed(out)
    api = [row for row in rows if row.get("type") in done]
    hand = [row for row in rows if row.get("type") not in done]
    table = ["| Row | Name | Type | Content | Priority | TTL |", "| --- | --- | --- | --- | --- | --- |"]
    for row in hand:
        table.append("| %s | %s | %s | %s | %s | %s |" % (
            row.get("row"), row.get("name"), row.get("type"), str(row.get("content")).replace("|", "\\|"),
            row.get("prio") if row.get("type") in ("MX", "SRV") else "none", row.get("ttl")))
    save(os.path.join(out, "to-create-api.json"), json.dumps(api, indent=2) + "\n")
    save(os.path.join(out, "to-create-by-hand.txt"), "\n".join(table) + "\n")

    def kinds(part):
        return ", ".join(sorted({str(row.get("type")) for row in part})) or "none"

    print("batch: %d records through the API (%s); %d records by hand (%s)"
          % (len(api), kinds(api), len(hand), kinds(hand)))


def count(out, work, name="to-create-api.json"):
    if name not in COUNTED:
        stop("count reads one of: " + ", ".join(COUNTED))
    print(len(load(os.path.join(out, name))))


def body(out, work, index):
    """The request body of one record of to-create-api.json. A type with no proof stops."""
    row = load(os.path.join(out, "to-create-api.json"))[int(index)]
    if row.get("type") not in set(PROVEN) | passed(out):
        stop("the type %s has no passed probe: the API makes no record of it" % row.get("type"))
    request(work, apex_of(work), row)


def answer(out, work):
    porkbun(os.path.join(work, "answer.json"), work)


def questions(out, work):
    """The planned records of each question (name and type). An ALIAS record answers a question for A."""
    found = {}
    for row in planned(out, work):
        found.setdefault((row["name"], "A" if row["type"] == "ALIAS" else row["type"]), []).append(row)
    return found


def names(out, work):
    pairs = sorted(questions(out, work))
    save(os.path.join(out, "names.txt"), "".join("%s %s\n" % pair for pair in pairs))
    print("names: %d questions" % len(pairs))


def parts(text):
    """The parts of one record content in one form: no quotes, lower case, no dot at the end."""
    return [part.rstrip(".").lower() for part in str(text).replace('"', " ").split() if part.rstrip(".")]


def values(data):
    """Each value of the field `data` of one planned record, in the form of `parts`."""
    if isinstance(data, dict):
        return [part for name in sorted(data) for part in values(data[name])]
    if isinstance(data, list):
        return [part for item in data for part in values(item)]
    if data is None:
        return []
    return parts(data if isinstance(data, str) else json.dumps(data))


def lacks(kind, data, held):
    """True when the content parts `held` of one answer record are not the field `data` of its planned row.

    Each value of `data` must be a part of the content. For a type of ORDER with each field of
    its form, the parts of the content must also be those fields in their places.
    """
    if not isinstance(data, dict):
        return False
    order = ORDER.get(kind, ())
    if order and all(name in data for name in order) \
            and [part for name in order for part in values(data[name])] != held:
        return True
    return bool(collections.Counter(values(data)) - collections.Counter(held))


def result_file(text):
    """One result file of the function compare_servers of the page.

    It gives the second server, the state of each question, and each record of the direct
    answer of the second server to that question: its identity in the form of `key`, its TTL
    and the parts of its content. A line of the direct answer has the form of `dig +noall
    +answer +authority`. A record with another name or another type is not a part of the answer.
    """
    server, states, held, question, block = "", {}, {}, None, ""
    for raw in text.splitlines():
        match = re.fullmatch(r"(same|DIFFERENT|NO ANSWER): (\S+) (\S+)", raw)
        if match:
            question, block = (match.group(3).rstrip(".").lower(), match.group(2)), ""
            states[question], held[question] = match.group(1), []
        elif raw.startswith("second server: "):
            server = raw.split(": ", 1)[1].strip().rstrip(".").lower()
        elif raw in ("first:", "second:", "direct:"):
            block = raw
        elif block == "direct:" and question:
            cut = raw.split(None, 4)
            if len(cut) != 5 or not cut[1].isdigit() or cut[3].upper() != question[1] \
                    or cut[0].rstrip(".").lower() != question[0]:
                continue
            prio, content = (cut[4].split(None, 1) + [""])[:2] if question[1] in ("MX", "SRV") else (None, cut[4])
            held[question].append((key(question[0], question[1], content, prio), int(cut[1]), parts(cut[4])))
    return server, states, held


def against_plan(rows, found):
    """Why the direct answer of one name server is not the planned records of one question, or no text.

    The answer must hold each planned record of the question one time and no other record:
    the content, the priority, the TTL, and each value of the field `data` of a row that has
    one, in its place for a type of ORDER. A reason names no content. An ALIAS record has no
    planned address: its answer needs one address record or more, each with the planned TTL,
    and the script compares no content for it.
    """
    if any(row["type"] == "ALIAS" for row in rows):
        if not found:
            return "no address record"
        if any(seconds != row["ttl"] for row in rows for _, seconds, _ in found):
            return "another TTL"
        return ""
    want = {key(row["name"], row["type"], row["content"], row.get("prio")): row for row in rows}
    if sorted(mine for mine, _, _ in found) != sorted(want):
        return "another record set"
    for mine, seconds, held in found:
        if seconds != want[mine]["ttl"]:
            return "another TTL"
        if lacks(mine[1], want[mine].get("data"), held):
            return "another value of the field data"
    return ""


def preswitch(out, work, *results):
    """The gate before the name server change. No read of an API replaces it.

    Each result file is one run of the comparison against one name server of Porkbun. Each
    planned question needs a direct answer in each file, and that answer must be the planned
    records. A difference to the old answer passes only when expected-differences.txt names
    it, in the form of names.txt. That file permits a difference to the answer of Cloudflare
    only. It permits no answer of Porkbun that is not the plan.
    """
    if len(results) < 2:
        stop("the gate needs the result files of two name servers of Porkbun")
    asked, expected = questions(out, work), set()
    path = os.path.join(out, "expected-differences.txt")
    if os.path.exists(path):
        expected = {(part[0].rstrip(".").lower(), part[1].upper())
                    for part in (raw.split() for raw in text_of(path).splitlines()) if len(part) == 2}
    lines, silent, wrong, other, servers = [], 0, 0, 0, set()
    for name in results:
        server, states, held = result_file(text_of(os.path.join(out, name)))
        if server:
            servers.add(server)
        for question in sorted(asked):
            status = states.get(question)
            if status in (None, "NO ANSWER"):
                silent += 1
                lines.append("%s, no direct answer: %s %s" % (name, question[1], question[0]))
                continue
            reason = against_plan(asked[question], held[question])
            if reason:
                wrong += 1
                lines.append("%s, the answer is not the planned record (%s): %s %s"
                             % (name, reason, question[1], question[0]))
            if status == "DIFFERENT" and question not in expected:
                other += 1
                lines.append("%s, a difference that is not expected: %s %s" % (name, question[1], question[0]))
    save(os.path.join(out, "preswitch-gate.txt"), "".join(line + "\n" for line in lines) or "no finding\n")
    if len(servers) != len(results):
        stop("the switch is BLOCKED: the result files do not come from %d different name servers" % len(results))
    if silent:
        stop("the switch is BLOCKED: %d direct answers of the Porkbun name servers are missing" % silent)
    if wrong:
        stop("the switch is BLOCKED: %d direct answers of the Porkbun name servers are not the planned records; "
             "no expected difference permits that; preswitch-gate.txt names each one" % wrong)
    if other:
        stop("the switch is BLOCKED: %d differences are not in expected-differences.txt" % other)
    alias = sum(any(row["type"] == "ALIAS" for row in rows) for rows in asked.values())
    print("preswitch: %d questions have a direct answer at %d name servers of Porkbun; each answer is the "
          "planned record; expected differences: %d; ALIAS questions with no check of the content: %d"
          % (len(asked), len(results), len(expected & set(asked)), alias))


COMMANDS = {"files": files, "bind": bind, "pages": pages, "plan": plan, "ttl": ttl, "unchanged": unchanged,
            "restore": restore, "restore-body": restore_body, "cloudflare-answer": cloudflare_answer,
            "state": state, "diff": diff, "probe-types": probe_types, "probe-body": probe_body,
            "probe-answer": probe_answer, "probe-compare": probe_compare, "count": count,
            "body": body, "answer": answer, "names": names, "preswitch": preswitch}


# --- the self-test -------------------------------------------------------------------------

APEX = "example.com"
ADDRESS = "198.51.100.7"
ENVIRONMENT = (
    ("EC2_DOMAIN", "ec2." + APEX),
    ("EC2_OPTIONS", "pangolin"),
    ("EC2_EDGE_NAMES", "ok,llm,ov,agent"),
    ("EC2_CLOUDFLARE_API_TOKEN", "fixture-token"),
    ("EC2_CLOUDFLARE_ZONE_ID", "0123456789abcdef" * 2),
    ("EC2_PORKBUN_API_KEY", "fixture-key"),
    ("EC2_PORKBUN_SECRET_KEY", "fixture-pair"),
)


def fixture_zone():
    """A zone with each case of the plan: a proxy, mail, TTL decisions and the five host records."""
    rows = [
        (APEX, "A", "203.0.113.5", 1, True, None),
        ("www." + APEX, "CNAME", APEX, 1, True, None),
        (APEX, "MX", "mx1.mail.example.net", 3600, False, 10),
        (APEX, "MX", "mx2.mail.example.net", 3600, False, 20),
        (APEX, "TXT", '"v=spf1 include:_spf.mail.example.net ~all"', 3600, False, None),
        ("_dmarc." + APEX, "TXT", '"v=DMARC1; p=none"', 300, False, None),
        ("note." + APEX, "TXT", '"SOA review 1"', 3600, False, None),
        (APEX, "CAA", '0 issue "letsencrypt.org"', 3600, False, None),
        ("sub." + APEX, "NS", "ns1.other.example.net", 86400, False, None),
        ("auto." + APEX, "A", "203.0.113.9", 1, False, None),
    ]
    rows += [("%s.ec2.%s" % (name, APEX), "A", ADDRESS, HOST_TTL, False, None)
             for name in ("pangolin", "ok", "llm", "ov", "agent")]
    found = []
    for index, (name, kind, text, seconds, proxied, prio) in enumerate(rows, 1):
        record = {"id": "%032x" % index, "name": name, "type": kind, "content": text, "ttl": seconds,
                  "proxied": proxied}
        if prio is not None:
            record["priority"] = prio
        found.append(record)
    return found


def fixture_service(port=5060):
    """An SRV record with structured fields. The content does not change with the port."""
    return {"id": "a" * 32, "name": "_sip._tcp." + APEX, "type": "SRV", "content": "5 5060 sip.example.net",
            "ttl": 3600, "proxied": False, "priority": 10,
            "data": {"priority": 10, "weight": 5, "port": port, "target": "sip.example.net"}}


def fixture_pages(out, prefix, records, size=8):
    parts = [records[start:start + size] for start in range(0, len(records), size)] or [[]]
    for page, part in enumerate(parts, 1):
        info = {"page": page, "per_page": size, "count": len(part), "total_count": len(records),
                "total_pages": len(parts)}
        save(os.path.join(out, "%s-%d.json" % (prefix, page)),
             json.dumps({"success": True, "errors": [], "result": part, "result_info": info}))


def fixture_export(records, serial, split=False):
    soa = "%s.\t3600\tIN\tSOA\tns.provider.example. dns.provider.example. %s 10000 2400 604800 3600" % (APEX, serial)
    if split:
        soa = "%s. 3600 IN SOA ns.provider.example. dns.provider.example. (\n\t%s ; serial\n" % (APEX, serial)
        soa += "\t10000 2400 604800 3600 )"
    lines = [";; Domain: %s." % APEX, ";; Exported: fixture %s" % serial, soa, ""]
    lines += ["%s.\t%s\tIN\t%s\t%s" % (r["name"], r["ttl"], r["type"], r["content"]) for r in records]
    return "\n".join(lines) + "\n"


def self_test():
    """Fixtures for each rule of the script. Each command runs as its own process."""
    fixtures = [0]

    with tempfile.TemporaryDirectory() as top:
        env_file = os.path.join(top, "fixture.env")
        save(env_file, "".join("%s=%s\n" % pair for pair in ENVIRONMENT))

        def run(out, work, command, *arguments, status=0):
            """Run one command. `status` 0 needs a pass; another value needs a stop with a nonzero status."""
            done = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), command, out, work,
                                   *arguments], capture_output=True, text=True, check=False)
            text = done.stdout + done.stderr
            assert (done.returncode == 0) == (status == 0), "%s: status %d: %s" % (command, done.returncode, text)
            fixtures[0] += 1
            return text

        def case(name, records=None, address=ADDRESS):
            """A new private directory and a new work directory with one read of the fixture zone."""
            out, work = os.path.join(top, name), os.path.join(top, name + ".work")
            os.mkdir(out, 0o700)
            os.mkdir(work, 0o700)
            records = fixture_zone() if records is None else records
            fixture_pages(out, PAGES, records)
            save(os.path.join(out, "cloudflare-zone.bind"), fixture_export(records, 1))
            save(os.path.join(out, "stack-address.txt"), address + "\n")
            run(out, work, "files", env_file, "cloudflare", "porkbun")
            return out, work

        def rows_of(out):
            return load(os.path.join(out, "planned-records.json"))

        def write_rows(out, rows):
            save(os.path.join(out, "planned-records.json"), json.dumps(rows))

        def decided(name, records=None):
            """A plan with each decision: the TTL 600 for a TTL decision, copy for each other row."""
            out, work = case(name, records)
            run(out, work, "plan")
            run(out, work, "ttl", "600")
            rows = rows_of(out)
            for row in rows:
                if row["decision"] == "decide":
                    row["decision"], row["ttl"] = "copy", 600 if row["ttl"] == 1 else row["ttl"]
            write_rows(out, rows)
            save(os.path.join(out, "porkbun-zone.json"), json.dumps({"status": "SUCCESS", "records": []}))
            return out, work, rows

        # The plan preserves each TTL and lists each TTL decision. It changes no TTL by itself.
        out, work = case("plan")
        run(out, work, "bind", "cloudflare-zone.bind")
        assert run(out, work, "pages", PAGES, "1").strip() == "2"
        text = run(out, work, "plan")
        assert "15 records, 10 copy, 5 decide; TTL decisions: 4; host records: 5 of 5" in text, text
        by_name = {(row["name"], row["type"], row["content"]): row for row in rows_of(out)}
        assert by_name[(APEX, "MX", "mx1.mail.example.net")]["ttl"] == 3600
        assert by_name[("_dmarc." + APEX, "TXT", "v=DMARC1; p=none")]["ttl"] == 300
        assert by_name[("_dmarc." + APEX, "TXT", "v=DMARC1; p=none")]["decision"] == "decide"
        assert by_name[("auto." + APEX, "A", "203.0.113.9")]["ttl"] == 1
        assert len(text_of(os.path.join(out, "ttl-decisions.txt")).splitlines()) == 4
        run(out, work, "plan", status=1)
        assert "2 rows have the TTL 900" in run(out, work, "ttl", "900")
        assert sum(row["decision"] == "decide" for row in rows_of(out)) == 3
        run(out, work, "ttl", "5", status=1)

        # A host record that is not exactly one A record with the stack address, the TTL 600
        # and no proxy stops the plan. The message names the record and holds no address.
        for name, change, state_text in (
                ("host-address", {"content": "203.0.113.77"}, "host record ok: another address"),
                ("host-ttl", {"ttl": 300}, "host record ok: the TTL is not 600"),
                ("host-proxy", {"proxied": True}, "host record ok: the proxy is on"),
                ("host-second", None, "host record ok: 2 A records"),
                ("host-missing", "remove", "host record ok: no A record")):
            records = fixture_zone()
            index = next(i for i, r in enumerate(records) if r["name"] == "ok.ec2." + APEX)
            if change == "remove":
                del records[index]
            elif change is None:
                records.append(dict(records[index], id="f" * 32, content="203.0.113.78"))
            else:
                records[index].update(change)
            out, work = case(name, records)
            text = run(out, work, "plan", status=1)
            assert state_text in text and "1 of 5 host records are not correct" in text, text
            assert "198.51.100" not in text and "203.0.113" not in text, text
            assert not os.path.exists(os.path.join(out, "planned-records.json"))
            assert ADDRESS in text_of(os.path.join(out, "host-records.txt"))
        out, work = case("host-file", address="not an address")
        run(out, work, "plan", status=1)

        # The plan holds one row for each record of the complete inventory. A missing row,
        # an empty plan and an open decision stop, with a count.
        out, work, rows = decided("rows")
        assert "0 same, 0 ttl differs, 0 duplicate, 15 create" in run(out, work, "diff", "porkbun-zone.json",
                                                                      "diff.txt")
        write_rows(out, [])
        assert "the plan is empty: 15 records" in run(out, work, "diff", "porkbun-zone.json", "diff.txt", status=1)
        assert "the plan is empty" in run(out, work, "names", status=1)
        write_rows(out, rows[:3] + rows[4:])
        text = run(out, work, "diff", "porkbun-zone.json", "diff.txt", status=1)
        assert "1 records of the inventory have no row in the plan and no decision drop" in text, text
        write_rows(out, [dict(row, decision="drop") if row["type"] == "CAA" else row for row in rows])
        assert "14 create" in run(out, work, "diff", "porkbun-zone.json", "diff.txt")
        write_rows(out, [dict(row, decision="decide") if row["type"] == "CAA" else row for row in rows])
        assert "1 rows still have the decision decide" in run(out, work, "names", status=1)
        write_rows(out, [dict(row, source="0" * 32) if row["type"] == "CAA" else row for row in rows])
        run(out, work, "names", status=1)
        write_rows(out, rows + [dict(rows[0], row=99)])
        assert "a second row for one record" in run(out, work, "names", status=1)
        write_rows(out, [dict(row, content="mx9.mail.example.net") if row["content"].startswith("mx1.") else row
                         for row in rows])
        assert "differs from the inventory" in run(out, work, "names", status=1)
        write_rows(out, [dict(row, ttl=600) if row["type"] == "MX" else row for row in rows])
        assert "the TTL differs from the inventory" in run(out, work, "names", status=1)
        write_rows(out, [dict(row, ttl=1) if row["name"].startswith("auto.") else row for row in rows])
        assert "automatic TTL" in run(out, work, "names", status=1)
        write_rows(out, [dict(row, decision="drop") if row["name"].startswith("llm.") else row for row in rows])
        text = run(out, work, "names", status=1)
        assert "host record llm: no A record" in text and "198.51.100" not in text, text
        new = {"row": 16, "source": "new", "name": "new." + APEX, "type": "A", "content": "203.0.113.20",
               "ttl": 600, "prio": None, "decision": "copy"}
        write_rows(out, rows + [new])
        assert "16 create" in run(out, work, "diff", "porkbun-zone.json", "diff.txt")
        assert "names: 15 questions" in run(out, work, "names")

        # The comparison before the switch drops the SOA records only. A TXT content with the
        # text SOA and the proxy state of the fresh list are compared.
        out, work = case("zone")
        records = fixture_zone()
        save(os.path.join(out, "cloudflare-zone-final.bind"), fixture_export(records, 2, split=True))
        fixture_pages(out, "cloudflare-final", records)
        assert "no record changed since the export (15 records" in run(out, work, "unchanged")
        other = [dict(r, content='"SOA review 2"') if r["name"].startswith("note.") else r for r in records]
        save(os.path.join(out, "cloudflare-zone-final.bind"), fixture_export(other, 2))
        text = run(out, work, "unchanged", status=1)
        assert "2 differences in the export, 0 in the list" in text, text
        save(os.path.join(out, "cloudflare-zone-final.bind"), fixture_export(records, 2))
        fixture_pages(out, "cloudflare-final", [dict(r, proxied=False) if r["name"] == APEX else r for r in records])
        text = run(out, work, "unchanged", status=1)
        assert "0 differences in the export, 2 in the list" in text, text
        save(os.path.join(out, "cloudflare-zone-final.bind"), "no zone file\n")
        run(out, work, "bind", "cloudflare-zone-final.bind", status=1)

        # A rebuilt Cloudflare zone comes from the JSON inventory, with the proxy state.
        fixture_pages(out, "cloudflare-restored", [])
        assert "15 records to make, 2 of them with the proxy on; 0 records only" in run(out, work, "restore")
        bodies = load(os.path.join(out, "cloudflare-restore.json"))
        assert [item["proxied"] for item in bodies if item["name"] == APEX and item["type"] == "A"] == [True]
        assert {item["ttl"] for item in bodies if item["proxied"]} == {1}
        assert run(out, work, "count", "cloudflare-restore.json").strip() == "15"
        run(out, work, "restore-body", "0")
        assert "apikey" not in text_of(os.path.join(work, "body.json"))
        refused = {"success": False, "errors": [{"message": "fixture-token"}]}
        save(os.path.join(work, "answer.json"), json.dumps(refused))
        text = run(out, work, "cloudflare-answer", status=1)
        assert "fixture-token" not in text and "<token>" in text, text
        save(os.path.join(work, "answer.json"), json.dumps({"success": True}))
        run(out, work, "cloudflare-answer")
        fixture_pages(out, "cloudflare-restored", records + [dict(records[0], id="e" * 32, name="more." + APEX)])
        assert "0 records to make, 0 of them with the proxy on; 1 records only" in run(out, work, "restore")

        # The field `data` is a part of the identity of a record. A change of one field of it
        # is a difference before the switch and a record to make in a rebuilt zone. Another
        # order of its keys is no difference.
        records = fixture_zone() + [fixture_service()]
        moved = fixture_zone() + [fixture_service(5061)]
        turned = [dict(r, data=dict(reversed(list(r["data"].items())))) if "data" in r else r for r in records]
        out, work = case("data", records)
        save(os.path.join(out, "cloudflare-zone-final.bind"), fixture_export(records, 2))
        fixture_pages(out, "cloudflare-final", turned)
        assert "no record changed since the export (16 records" in run(out, work, "unchanged")
        fixture_pages(out, "cloudflare-final", moved)
        text = run(out, work, "unchanged", status=1)
        assert "0 differences in the export, 2 in the list" in text, text
        changes = text_of(os.path.join(out, "zone-changes.txt"))
        assert '"port":5060' in changes and '"port":5061' in changes and "data none" not in changes, changes
        fixture_pages(out, "cloudflare-restored", moved)
        text = run(out, work, "restore")
        assert "1 records to make, 0 of them with the proxy on; 1 records only in the new zone" in text, text
        bodies = load(os.path.join(out, "cloudflare-restore.json"))
        assert bodies[0]["type"] == "SRV" and bodies[0]["data"]["port"] == 5060 and "content" not in bodies[0]
        assert '"port":5061' in text_of(os.path.join(out, "restore-diff.txt"))
        fixture_pages(out, "cloudflare-restored", turned)
        assert "0 records to make, 0 of them with the proxy on; 0 records only" in run(out, work, "restore")

        # The API makes a record type other than A only after its probe passes.
        out, work, rows = decided("probe")
        text = run(out, work, "diff", "porkbun-zone.json", "diff.txt")
        assert "batch: 7 records through the API (A); 8 records by hand" in text, text
        assert "5 types wait for a probe: CAA, CNAME, MX, NS, TXT" in run(out, work, "probe-types")
        run(out, work, "probe-body", "A", status=1)
        run(out, work, "probe-body", "MX")
        sent = load(os.path.join(work, "body.json"))
        assert sent["name"] == PROBE_LABEL + "-mx" and sent["ttl"] == "600" and sent["prio"] == "10", sent
        save(os.path.join(work, "answer.json"), json.dumps({"status": "SUCCESS", "id": "77"}))
        run(out, work, "probe-answer", "MX")
        held = {"id": "77", "name": "%s-mx.%s" % (PROBE_LABEL, APEX), "type": "MX", "ttl": "600", "prio": "10",
                "content": "mx1.mail.example.net"}
        save(os.path.join(out, "probe-zone.json"), json.dumps({"status": "SUCCESS", "records": [held]}))
        assert "probe MX: passed" in run(out, work, "probe-compare", "MX", "probe-zone.json")
        assert text_of(os.path.join(work, "probe-ids")) == "77\n"
        run(out, work, "probe-body", "TXT")
        assert load(os.path.join(work, "body.json"))["name"] == "_dmarc.%s-txt" % PROBE_LABEL
        save(os.path.join(work, "answer.json"), json.dumps({"status": "ERROR", "message": "fixture-key"}))
        assert "probe TXT: FAILED" in run(out, work, "probe-answer", "TXT", status=1)
        run(out, work, "probe-body", "CAA")
        held = {"id": "78", "name": "%s-caa.%s" % (PROBE_LABEL, APEX), "type": "CAA", "ttl": "600",
                "content": '0 issue "other.example"'}
        save(os.path.join(out, "probe-zone.json"), json.dumps({"status": "SUCCESS", "records": [held]}))
        assert "probe CAA: FAILED (Porkbun holds another content" in run(out, work, "probe-compare", "CAA",
                                                                        "probe-zone.json")
        assert "4 types wait for a probe: CAA, CNAME, NS, TXT" in run(out, work, "probe-types")
        text = run(out, work, "diff", "porkbun-zone.json", "diff.txt")
        assert "batch: 9 records through the API (A, MX); 6 records by hand (CAA, CNAME, NS, TXT)" in text, text
        assert run(out, work, "count").strip() == "9"
        run(out, work, "count", "planned-records.json", status=1)
        api = load(os.path.join(out, "to-create-api.json"))
        run(out, work, "body", str(next(i for i, row in enumerate(api) if row["type"] == "MX")))
        assert load(os.path.join(work, "body.json"))["apikey"] == "fixture-key"
        save(os.path.join(out, "to-create-api.json"), json.dumps([row for row in rows if row["type"] == "NS"]))
        assert "no passed probe" in run(out, work, "body", "0", status=1)
        assert "1 probe records are still in the Porkbun zone" in run(out, work, "diff", "probe-zone.json",
                                                                     "diff.txt", status=1)
        twice = [{"id": str(index), "name": "ok.ec2." + APEX, "type": "A", "content": ADDRESS, "ttl": "600"}
                 for index in (1, 2)]
        save(os.path.join(out, "twice-zone.json"), json.dumps({"status": "SUCCESS", "records": twice}))
        text = run(out, work, "diff", "twice-zone.json", "diff.txt")
        assert "0 same, 0 ttl differs, 1 duplicate, 14 create" in text, text
        save(os.path.join(work, "answer.json"), json.dumps({"status": "ERROR", "message": "no: fixture-key"}))
        text = run(out, work, "answer", status=1)
        assert "fixture-key" not in text and "<key>" in text, text

        # The gate before the switch: a direct answer of two Porkbun name servers for each
        # planned question, and no difference that is not expected.
        out, work, rows = decided("gate", fixture_zone() + [fixture_service()])
        assert [row["data"]["port"] for row in rows if "data" in row] == [5060]
        run(out, work, "names")
        asked = [tuple(line.split()) for line in text_of(os.path.join(out, "names.txt")).splitlines()]

        def held(pair):
            """The direct answer of a name server that holds the plan: each record of one question."""
            lines = []
            for row in rows:
                if (row["name"], row["type"]) == pair:
                    text = row["content"]
                    if pair[1] in ("MX", "SRV"):
                        text = "%d %s." % (row["prio"], text.upper())
                    elif pair[1] == "TXT":
                        text = '"%s" "%s"' % (text[:5], text[5:])
                    elif pair[1] in HOSTS:
                        text += "."
                    lines.append("%s.\t%d\tIN\t%s\t%s" % (pair[0].upper(), row["ttl"], pair[1], text))
            return lines

        def result(name, server, states=None, answers=None):
            states, answers = states or {}, answers or {}
            lines = ["first server: ns.provider.example", "second server: " + server]
            for pair in asked:
                if states.get(pair) != "missing":
                    lines += ["%s: %s %s" % (states.get(pair, "same"), pair[1], pair[0]), "direct:"]
                    lines += answers.get(pair, held(pair))
            save(os.path.join(out, name), "\n".join(lines) + "\n")

        def gate(status=0, both=False, **second):
            """Run the gate. `second` changes the second name server, and with `both` also the first one."""
            result("preswitch.txt", "ns1.new.example", **(second if both else {}))
            result("preswitch-2.txt", "ns2.new.example", **second)
            return run(out, work, "preswitch", "preswitch.txt", "preswitch-2.txt", status=status)

        text = gate()
        assert "15 questions have a direct answer at 2 name servers" in text and "ALIAS questions" in text, text
        run(out, work, "preswitch", "preswitch.txt", status=1)
        result("preswitch-2.txt", "ns1.new.example")
        assert "different name servers" in run(out, work, "preswitch", "preswitch.txt", "preswitch-2.txt", status=1)
        text = gate(1, states={asked[0]: "NO ANSWER"})
        assert "BLOCKED: 1 direct answers of the Porkbun name servers are missing" in text, text
        assert "BLOCKED: 1 direct answers" in gate(1, states={asked[0]: "missing"})
        text = gate(1, states={asked[0]: "DIFFERENT"})
        assert "BLOCKED: 1 differences are not in expected-differences.txt" in text, text
        save(os.path.join(out, "expected-differences.txt"), "%s %s\n" % asked[0])
        assert "expected differences: 1" in gate(states={asked[0]: "DIFFERENT"})

        # Each direct answer is the planned record. An expected difference is a difference to
        # the answer of Cloudflare only: it permits no answer of Porkbun that is not the plan.
        plain, service = ("auto." + APEX, "A"), ("_sip._tcp." + APEX, "SRV")
        line = plain[0] + ".\t%d\tIN\tA\t%s"
        save(os.path.join(out, "expected-differences.txt"), "%s %s\n" % plain)
        assert "expected differences: 1" in gate(states={plain: "DIFFERENT"})
        text = gate(1, both=True, states={plain: "DIFFERENT"}, answers={plain: [line % (600, "203.0.113.99")]})
        assert "BLOCKED: 2 direct answers of the Porkbun name servers are not the planned records" in text, text
        assert "203.0.113" not in text and plain[0] not in text, text
        found = text_of(os.path.join(out, "preswitch-gate.txt"))
        assert "preswitch-2.txt, the answer is not the planned record (another record set): A " + plain[0] in found
        assert "203.0.113" not in found and len(found.splitlines()) == 2, found
        text = gate(1, answers={plain: [line % (600, "203.0.113.99")]})
        assert "BLOCKED: 1 direct answers of the Porkbun name servers are not the planned records" in text, text
        for lines in ([line % (600, "203.0.113.9"), line % (600, "203.0.113.99")],
                      [line.replace("\tA\t", "\tAAAA\t") % (600, "203.0.113.9")], []):
            gate(1, answers={plain: lines})
            assert "(another record set): A " + plain[0] in text_of(os.path.join(out, "preswitch-gate.txt"))
        gate(1, answers={plain: [line % (300, "203.0.113.9")]})
        assert "(another TTL): A " + plain[0] in text_of(os.path.join(out, "preswitch-gate.txt"))
        for change in ({"port": 5061}, {"weight": 5060, "port": 5}, {"more": "other.example.net"}):
            write_rows(out, [dict(row, data=dict(row["data"], **change)) if "data" in row else row for row in rows])
            gate(1)
            found = text_of(os.path.join(out, "preswitch-gate.txt"))
            assert "(another value of the field data): SRV " + service[0] in found, found
            assert len(found.splitlines()) == 2, found
        write_rows(out, [dict(row, data={"port": 5060, "target": "SIP.example.net."}) if "data" in row else row
                         for row in rows])
        gate()
        write_rows(out, [dict(row, data="5060") if "data" in row else row for row in rows])
        assert "the field data is not a set of named values" in gate(1)
        alias = {"row": 99, "source": "new", "name": "alias." + APEX, "type": "ALIAS",
                 "content": "target.example.net", "ttl": 600, "prio": None, "decision": "copy"}
        write_rows(out, rows + [alias])
        run(out, work, "names")
        asked.append((alias["name"], "A"))
        address = [alias["name"] + ".\t600\tIN\tA\t203.0.113.30"]
        result("preswitch.txt", "ns1.new.example", answers={asked[-1]: address})
        result("preswitch-2.txt", "ns2.new.example", answers={asked[-1]: address})
        text = run(out, work, "preswitch", "preswitch.txt", "preswitch-2.txt")
        assert "16 questions" in text and "ALIAS questions with no check of the content: 1" in text, text
        result("preswitch-2.txt", "ns2.new.example", answers={asked[-1]: [address[0].replace("\t600\t", "\t300\t")]})
        run(out, work, "preswitch", "preswitch.txt", "preswitch-2.txt", status=1)
        found = text_of(os.path.join(out, "preswitch-gate.txt"))
        assert found == "preswitch-2.txt, the answer is not the planned record (another TTL): A " + alias["name"] + "\n"
        result("preswitch-2.txt", "ns2.new.example")
        run(out, work, "preswitch", "preswitch.txt", "preswitch-2.txt", status=1)
        assert "(no address record): A " + alias["name"] in text_of(os.path.join(out, "preswitch-gate.txt"))

    # The page and the script agree: each command of a block exists, and a failed bootstrap
    # ends its block with a nonzero status.
    note = "no page in this copy"
    if os.path.exists(PAGE):
        blocks = re.findall(r"^```sh\n(.*?)^```$", text_of(PAGE), flags=re.S | re.M)
        used = set()
        for block in blocks:
            code = "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("#"))
            used |= set(re.findall(r"(?m)(?:^\s*|\bif |\$\()records ([a-z][a-z-]*)", code))
        assert used and used <= set(COMMANDS), sorted(used - set(COMMANDS))
        assert set(COMMANDS) <= used, sorted(set(COMMANDS) - used)
        boot = [block for block in blocks if "host-bootstrap.sh" in block]
        assert len(boot) == 1 and re.search(r"\nelse\n(?:.*\n)*?\s+exit 1\nfi\n", boot[0]), "the bootstrap block"
        fixtures[0] += 2
        note = "with the page"
    print("dns-migration self-test: pass (%d fixtures, %s)" % (fixtures[0], note))


def main():
    arguments = sys.argv[1:]
    if arguments == ["--self-test"]:
        self_test()
        return
    if len(arguments) < 3 or arguments[0] not in COMMANDS:
        stop("usage: dns-migration.py <command> <private directory> <work directory> [<argument>...], "
             "or --self-test; the commands: " + ", ".join(COMMANDS))
    command = COMMANDS[arguments[0]]
    try:
        inspect.signature(command).bind(*arguments[1:])
    except TypeError:
        stop("the command " + arguments[0] + " has other arguments")
    command(*arguments[1:])


if __name__ == "__main__":
    main()

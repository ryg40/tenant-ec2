# Move the DNS zone of the domain to Porkbun

[DNS records](../options/dns.md) | [Pangolin edge](../options/pangolin.md) | [Host boot](host-boot.md) | [Deploy script](deploy-script.md)

Use this page for a first deploy with the option `pangolin` when another provider answers for the domain.
The example of this page is a zone at Cloudflare.
The authoritative DNS of the domain moves to Porkbun, with each record of the zone.
Then the Pangolin edge can get its wildcard certificate with the DNS-01 challenge at Porkbun.

Two rules bind each step:

- Preserve all existing domain records before requesting changes.
- Do not change the name servers based only on the host records. Preserve every existing domain record and service.

Warning: a name server change moves each service of the domain, not only the host. A record that Porkbun lacks stops its service: mail, a site or a login.

Warning: this page gives no approval. [Section 7](#7-the-approval-table) lists each live step.

Not verified: no command of this page ran against a provider, a registrar or a host.
An offline run with stubs of `curl` and `dig` and a fake zone proved the blocks of the phases A to D, the failure branch of step E3 and the store check of step E4 only.
The self-test of the helper script proves the rules of the helper with fixtures only.

## 1. Purpose and preconditions

The procedure has five phases.
Each phase ends with a check.
Do not start a phase before the check of the phase before it passes.

| Phase | Result | What it changes |
| --- | --- | --- |
| A | A complete export of the Cloudflare zone, an inventory and a decision list. | Nothing. |
| B | The state of the Porkbun zone. | Nothing. |
| C | The Porkbun zone holds the complete record set. A test proves it before the switch. | Records at Porkbun. No public answer changes. |
| D | The name servers of Porkbun answer for the domain. | The name servers of the domain at the registrar. |
| E | The kit confirms the host records. The edge has its certificate. | The certificate store of the host. A service restart is possible. |

### Words and placeholders

| Text | Meaning |
| --- | --- |
| apex domain, `<apex domain>` | The registered domain. Its zone holds each record of the domain. `EC2_DOMAIN` is one label below it. |
| host records | The A records of the [record set](../options/dns.md#the-record-set) of the kit. With the option `pangolin` and four edge names, they are `pangolin`, `ok`, `llm`, `ov` and `agent` below `EC2_DOMAIN`. |
| stack address, `<stack address>` | The output `ElasticIp` of the stack. |
| `<zone id>` | The value of `EC2_CLOUDFLARE_ZONE_ID`: 32 hexadecimal digits. |
| `<new zone id>` | The zone id of a new Cloudflare zone, only for the way back of step D7b. |
| `<cloudflare ns>` | One name server of the Cloudflare zone. |
| `<porkbun ns>` | One default name server of Porkbun. |
| `<record id>` | The id that a provider gives to one record. |
| private directory | A directory with the mode 0700 outside every clone of the kit, for example a folder of the private tasks directory of the lead. It holds no tracked file. It holds each export and each result. |
| owner | The person who owns the domain and the two provider accounts. |
| lead | The operator who runs the commands. |
| helper | The script `scripts/dns-migration.py` of the kit. Step A2 gives its commands. |
| inventory | The complete JSON record list of the Cloudflare zone from step A4, with the proxy state of each record. |

### The state before the first step

Each line must be true.
A line that is not true stops the procedure.

1. The registrar of the apex domain is Porkbun. A transfer to Porkbun is complete: the public registry record names Porkbun and shows no pending transfer.
2. The name servers of Cloudflare still answer for the domain.
3. The host records exist in the Cloudflare zone. Each one is an A record with the stack address, the TTL 600 and no proxy.
4. The Cloudflare zone holds records beyond the host records. No complete list of them exists. Phase A makes it.
5. The Pangolin edge has no certificate. Its DNS-01 challenge runs at Porkbun, and Porkbun does not answer for the domain yet.
6. `EC2_OPTIONS` holds `pangolin`, and `EC2_DNS_PROVIDER` is `porkbun`. The kit `dns` command stops with another provider while the option is active.
7. The Porkbun pair of the host has API access for the one domain only.
8. A Cloudflare token with the permission Zone, DNS, Edit on the one zone is on the development machine. It is not on the host.

### Who runs which step

| Actor | Steps |
| --- | --- |
| The owner | Gives one separate approval for each live step. Decides each item of the decision list. Opens the Porkbun account and changes the name servers. |
| The lead | Runs each read, each approved record write, each kit command and each host command. Fills the private sheet. |
| An agent | Can act as the lead. It does not open the registrar account. It does not answer in the place of the owner. |

### Rules for each command

- A domain, a record name, a record content, an address and a zone id are host values. They stay in the private directory. They go into no tracked file, no commit message, no tracker text and no report.
- A command of this page prints counts. It writes each record into a file of the private directory. The owner reads those files.
- A stop of the host record check names a host record by its first label, for example `ok`. That label is a name of the kit. No message holds an address.
- A credential stays in a file with the mode 0600 that the tool reads. No argument of a command, no address and no line of the output holds a credential. This is the rule of [the DNS scripts](../options/dns.md#where-a-dns-credential-is).
- Each phase reads only the credential that it needs.
- Stop at the first failed command. Stop also at each result that the step does not name.
- No step removes a record without a decision of the owner.

## 2. Phase A: export and inventory

Phase A reads the complete Cloudflare zone and the public state of the domain.
It changes nothing at a provider.

### A1. Start the phase shell

Each phase starts with this block, on the development machine.
Go to a clone of the kit first. The clone must hold the helper script of step A2.
Then start one new shell with the command `sh`.
A failed command then ends that shell, not your login shell.
The end of the shell removes the credential files.
The blocks need a POSIX shell, `python3`, `curl` 7.55 or later, `dig`, `awk`, `git` and `openssl`.
The block stops when the directory is not a clone with the helper script of step A2.
The block stops when the private directory holds a tracked file.
It prints a note when the directory is in a Git work tree that does not ignore it. Then keep each file of the directory out of that repository.

```sh
set +x
set -eu
umask 077
dns_kit=$(pwd)
test -f "$dns_kit/scripts/dns-migration.py"
printf 'Private environment file: '
IFS= read -r dns_env
printf 'Private directory: '
IFS= read -r dns_out
test -f "$dns_env"
test -d "$dns_out"
if [ -n "$(git -C "$dns_out" ls-files . 2>/dev/null | head -n 1)" ]; then
    echo "the private directory holds tracked files; use another directory"
    exit 1
fi
if git -C "$dns_out" rev-parse --is-inside-work-tree >/dev/null 2>&1 &&
    ! git -C "$dns_out" check-ignore -q . 2>/dev/null; then
    echo "note: the private directory is in a Git work tree that does not ignore it; never stage it"
fi
chmod 700 "$dns_out"
dns_work=$(mktemp -d)
trap 'rm -rf "$dns_work"' EXIT
cloudflare_api=https://api.cloudflare.com/client/v4
porkbun_api=https://api.porkbun.com/api/json/v3

# records <command> [<argument>...]: one command of the helper script of step A2.
records() {
    records_command=$1
    shift
    python3 -I "$dns_kit/scripts/dns-migration.py" "$records_command" "$dns_out" "$dns_work" "$@"
}

# list_zone <file prefix>: read each page of the JSON record list of the Cloudflare zone
# into the files <file prefix>-<page>.json.
list_zone() {
    page=1
    while :; do
        curl -sS --max-time 60 -H "@$dns_work/cloudflare.header" \
            -o "$dns_out/$1-$page.json" \
            "$cloudflare_api/zones/$zone_id/dns_records?per_page=100&page=$page"
        pages=$(records pages "$1" "$page")
        [ "$page" -lt "$pages" ] || break
        page=$((page + 1))
    done
    echo "list: $pages page files"
}

# ask <server> <name> <type>: the sorted answer of one server to one question. A name
# server gives the NS records of a delegated name in the authority part of its answer.
ask() {
    if [ "$3" = NS ]; then
        dig +noall +answer +authority +time=5 +tries=2 "@$1" "$2" NS </dev/null |
            awk -v name="$2." '$4 == "NS" && tolower($1) == tolower(name) {print tolower($5)}' | sort
    else
        dig +short +time=5 +tries=2 "@$1" "$2" "$3" </dev/null | sort
    fi
}

# direct <server> <name> <type>: each record of the answer of one server to one question,
# with its TTL.
direct() {
    dig +noall +answer +authority +time=5 +tries=2 "@$1" "$2" "$3" </dev/null
}

# compare_servers <server 1> <server 2> <result file>: ask both servers each question of
# names.txt. Write each answer into the result file, and each record of the second server
# with its TTL. Print the three counts only.
compare_servers() {
    printf 'first server: %s\nsecond server: %s\n' "$1" "$2" >"$dns_out/$3"
    same=0
    different=0
    silent=0
    while read -r name type; do
        first=$(ask "$1" "$name" "$type")
        second=$(ask "$2" "$name" "$type")
        if [ -z "$second" ]; then
            silent=$((silent + 1))
            printf 'NO ANSWER: %s %s\nfirst:\n%s\n' "$type" "$name" "$first" >>"$dns_out/$3"
        elif [ "$first" = "$second" ]; then
            same=$((same + 1))
            echo "same: $type $name" >>"$dns_out/$3"
        else
            different=$((different + 1))
            printf 'DIFFERENT: %s %s\nfirst:\n%s\nsecond:\n%s\n' "$type" "$name" "$first" "$second" >>"$dns_out/$3"
        fi
        printf 'direct:\n%s\n' "$(direct "$2" "$name" "$type")" >>"$dns_out/$3"
    done <"$dns_out/names.txt"
    echo "compare: $same same, $different different, $silent with no answer of the second server"
}
```

### A2. The helper script

The helper is the script [`scripts/dns-migration.py`](../../scripts/dns-migration.py) of the kit.
It reads the answers of the two providers that the blocks save, and it compares them.
It makes no network call.
Run it from the clone of the kit. Do not copy it into the private directory.
The function `records` of step A1 runs it with the private directory and the work directory.

The script has a self-test with fixtures of its own. The [check script](checks.md) runs it as `selftest scripts/dns-migration.py`.
Run the self-test one time before Phase A. It needs no credential and no private file.

```sh
python3 -I scripts/dns-migration.py --self-test
```

The last line must start with `dns-migration self-test: pass`.

A command of the script prints counts and states.
It stops with a nonzero status at each result that its step does not permit. Then the phase shell ends.

| Command | Step | Result |
| --- | --- | --- |
| `records files <environment file> [cloudflare] [porkbun]` | A3, B1, D1, D6, D7b | The credential file of each named provider and the names of the domain, in the work directory. |
| `records bind <file>` | A3 | A stop when the export does not hold exactly one SOA record. |
| `records pages <file prefix> <page>` | A4, D1, D7b | The number of pages of the JSON list. The function `list_zone` uses it. |
| `records plan` | A6 | `inventory.txt`, `ttl-decisions.txt`, `host-records.txt` and `planned-records.json`. A stop for a host record that is not correct. |
| `records ttl <seconds>` | C2 | The one TTL that the owner decides, in each row that waits for its TTL only. |
| `records state <file>` | B1 | The counts of one read of the Porkbun zone. |
| `records diff <zone file> <result file>` | C3, C4a, C4b, C5 | The comparison of the plan with one read of the Porkbun zone. The lists `to-create.json`, `to-create-api.json` and `to-create-by-hand.txt`. A stop for a probe record that is still in the zone. |
| `records probe-types`, `probe-body <type>`, `probe-answer <type>`, `probe-compare <type> <file>` | C4a | The probe of one record type, and its result in `probes.json`. |
| `records count [<file>]`, `body <index>`, `answer` | B3, C4a, C4b | The request body of one new record, and a stop when Porkbun refuses a request. |
| `records names` | C6 | `names.txt`: one question for each planned record. |
| `records preswitch <result file> <result file>` | C6, D1 | The gate before the name server change. A stop when a direct answer of Porkbun is not the planned record. |
| `records unchanged` | D1 | A stop when the Cloudflare zone changed since Phase A, also in one field of `data`. |
| `records restore`, `restore-body <index>`, `cloudflare-answer` | D7b | The records that a new Cloudflare zone lacks, with the proxy state and each field of `data`. |

Each command that reads the plan file checks it first. It stops when one line is not true:

- The plan holds one row for each record of the complete JSON list of step A4. A record with no row and no decision `drop` stops the command, with a count. An empty plan stops.
- No row has the decision `decide`.
- A record with no Cloudflare-only feature keeps its name, type, content and priority.
- A record keeps its TTL. Only a row of `ttl-decisions.txt` gets another TTL, and only the owner decides it.
- Each host record is exactly one A record with the stack address and the TTL 600.

### A3. Export the zone as a BIND file

This is one read-only request with the Cloudflare token: `GET zones/<zone id>/dns_records/export`.
The answer is a zone file in the BIND form.
It is a durable copy of the zone that a person can read.
The export stays in the private directory. It never goes into a tracked file.

Not verified: the form of this request and the form of its answer. No file of the kit sends the export request.

```sh
if [ -e "$dns_out/cloudflare-zone.bind" ]; then
    echo "an export exists; a new Phase A needs a new private directory"
    exit 1
fi
records files "$dns_env" cloudflare
zone_id=$(cat "$dns_work/zone-id")
curl -sS --fail --max-time 60 -H "@$dns_work/cloudflare.header" \
    -o "$dns_out/cloudflare-zone.bind" "$cloudflare_api/zones/$zone_id/dns_records/export"
records bind cloudflare-zone.bind
```

The result is one line with the number of records of the export.
A failed request means that the token lacks the right, or that the zone id is wrong.
The block does not replace an existing export. A second run of Phase A uses a new private directory.

The export holds the SOA record and the NS records of Cloudflare.
It holds no setting of the zone that is not a record.
Not verified: the export says which record has the proxy on. So the JSON list of step A4 is the source of the proxy state.

### A4. List the records as JSON

This is the record read of [the provider file](../../scripts/dns/cloudflare.sh), without its filter: `GET zones/<zone id>/dns_records?per_page=100&page=<page>`.
The function `list_zone` of step A1 reads each page.
The JSON list holds the fields that the export lacks: `proxied`, the automatic TTL and the origin content of a proxied record.
A record with structured fields, for example an SRV record with its port, has them in the field `data`.
The helper reads `data` as a part of the record: a record with another value in one field of `data` is another record.
It is the complete inventory of the zone. The plan file, the comparison of step D1 and the way back of step D7b read it.

Not verified: the request with the fields `per_page` and `page`, and the field `result_info` of its answer. The kit reads one name with this request, and it reads no page count.

```sh
list_zone cloudflare-records
```

### A5. Read the public state

These are public DNS reads with `dig`.
They need no credential.
The file `public-before.txt` keeps the name servers and the delegation for the way back.

```sh
apex=$(cat "$dns_work/apex")
parent=${apex#*.}
parent_ns=$(dig +short NS "$parent." | head -n 1)
test -n "$parent_ns"
{
    echo "== the delegation at the parent zone"
    dig +norecurse +noall +authority +answer "@$parent_ns" NS "$apex"
    echo "== the DS record at the parent zone"
    dig +norecurse +noall +answer "@$parent_ns" DS "$apex"
    for resolver in 1.1.1.1 8.8.8.8; do
        echo "== the resolver $resolver"
        dig +short "@$resolver" NS "$apex"
        dig +short "@$resolver" SOA "$apex"
    done
} >"$dns_out/public-before.txt" 2>&1
echo "DS records at the parent zone: $(dig +norecurse +short "@$parent_ns" DS "$apex" | grep -c . || true)"
```

Write the two Cloudflare name servers and the TTL of the delegation into the private sheet.
The TTL is the number in each NS line of the parent zone.

A DS record means that the domain has DNSSEC.
A resolver that validates then refuses each answer of Porkbun after the switch, while the old DS record exists.
Step D3 handles a DS record. A count of 0 needs no step.

### A6. Make the inventory and the plan file

The helper needs the stack address for the check of the host records.
The stack address is the output `ElasticIp` of the stack. The command of step E1 prints it. That command can run before Phase A, with its own approval.
The block writes the address into the file `stack-address.txt` of the private directory. No command prints it.

```sh
printf 'Stack address: '
IFS= read -r address
printf '%s\n' "$address" >"$dns_out/stack-address.txt"
records plan
```

The command writes four files into the private directory and prints the counts.

| File | Content |
| --- | --- |
| `inventory.txt` | One table row for each record of the zone. The owner reads it. |
| `planned-records.json` | The same rows as data, with one decision for each row. Phase C uses it. |
| `ttl-decisions.txt` | Each record with a TTL that the owner must decide. |
| `host-records.txt` | The state of each host record, with each address. |

The command does not replace an existing `planned-records.json`: the file can hold decisions.

The command checks each host record.
A host record is correct when its name has exactly one A record, with the stack address, the TTL 600 and no proxy.
The count `host records` then shows each host record, for example `5 of 5`.
Each other state stops the command with a nonzero status. Then it writes no plan file.
The other states are: no A record, a second A record, another address, another TTL, and the proxy on.
The message names the record by its first label, for example `host record ok: another address`.
It holds no address. The file `host-records.txt` holds each address.
Such a stop means that line 3 of the state before the first step is not true. The owner decides the correction.

The inventory table has this form:

```text
| Row | Name | Type | Content | TTL | Proxied | Cloudflare-only feature | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | <name> | <type> | <content> | <seconds or automatic> | yes or no | <feature or none> | copy, decide or drop |
```

A row gets the decision `copy` when Porkbun can hold it unchanged.
A row gets the decision `decide` when it has the proxy on, when it uses a Cloudflare-only feature, or when its type needs a form by hand.
The helper finds a feature by the name of its target. Read each row: a rule of the helper can miss a feature.

A row gets the decision `decide` also for its TTL.
The helper preserves the TTL of each record. It changes no TTL by itself.
Two kinds of TTL are a decision of the owner:

- The automatic TTL of Cloudflare. It is not a number of seconds that Porkbun can hold.
- A TTL below 600. Not verified: 600 is the smallest TTL that Porkbun holds.

The count `TTL decisions` is the number of these records. The file `ttl-decisions.txt` lists them. Step C2 writes each decision.

### A7. Record classes that need attention

Compare each class with the inventory. Write each open item into the decision list of the private sheet.

| Class | What to check | Decision of the owner |
| --- | --- | --- |
| Apex A and AAAA | The site of the domain. With the proxy on, the content is the origin. | See "A proxied record" below. |
| CNAME at the apex | Cloudflare answers it as an address (CNAME flattening). The DNS rules permit no CNAME at an apex. | An ALIAS record at Porkbun, or A and AAAA records. |
| `www` | Often a CNAME to the apex, or a proxied record. | Copy, or follow the decision for the apex. |
| MX | Mail for the domain. Each record has a priority. A wrong MX record loses mail. | Copy each record with its priority. |
| TXT with SPF | One `v=spf1` record for each name that sends mail. | Copy the exact text. |
| TXT with DKIM | The names below `_domainkey`. A long key has more than one string. | Copy. The joined text must be equal. |
| TXT with DMARC | The name `_dmarc`. | Copy. |
| TXT with a site verification | The proof of the domain for a mail service, a search console or an identity provider. | Copy. A lost record can end the verification later. |
| CAA | The certificate authorities that can make a certificate. The wildcard certificate of the host needs no CAA record, or `issue` or `issuewild` for `letsencrypt.org`. | Copy. Add Let's Encrypt when the set does not permit it. |
| SRV | Priority, weight, port and target. | Write the Porkbun form by hand. |
| NS of a subzone | The delegation of a name below the apex to other name servers. | Copy the exact names. The subzone itself does not move. |
| CNAME to a Cloudflare Tunnel or to Pages | The target works only through the proxy of Cloudflare, or only for a zone at Cloudflare. | A replacement, or the end of the service. |
| Email routing | MX and TXT records that Cloudflare manages for its mail forward. | Another mail forward before the switch, or the end of the forward. |
| Wildcard `*` | A record that answers for each name with no record of its own. | Copy. Check each name that it must not answer. |
| Each other type | For example HTTPS, SVCB, TLSA or SSHFP. | Write the Porkbun form by hand. |
| SOA and NS of the apex | They belong to the provider. | Do not copy them. Porkbun makes its own. |

### A8. Features that are not records

A zone at Cloudflare can use functions that no DNS record holds.
No command of this page exports them, and they do not move to Porkbun.
The owner reads each one in the Cloudflare dashboard.
Each feature that the zone uses is one decision row of the private sheet. It is not a record.
The decision is: keep the function in another way, or end it.

| Feature | What the owner reads | Decision of the owner |
| --- | --- | --- |
| The proxy of a record | The column `Proxied` of the inventory. | See "A proxied record" below. |
| Page rules | Each rule and its address pattern. | The same function at the origin, or the end of the rule. |
| Redirect rules and transform rules | Each single redirect, each bulk redirect, and each rule that changes an address or a header. | A redirect or a change at the origin or at another service, or the end. |
| Origin rules | Each rule that changes the origin, its port or its host header. | The same setting at the origin, or the end. |
| Email routing | Each forward address. Cloudflare manages the MX and TXT records of the forward. | Another mail forward that runs before the switch, or the end of the forward. |
| Cloudflare Tunnel | Each public host name of a tunnel. Its record is a CNAME to a tunnel target. | Another public path to the service, or the end. The record alone does not work at Porkbun. |
| Pages or Workers routes | Each custom domain of a Pages project, each Worker route and each custom domain of a Worker. | Another host for the site or the function, or the end. |
| Access applications | Each application of Cloudflare Access with a name of the domain. | Another login in front of the service, or the end of the service. |
| DNSSEC at Cloudflare | The DNSSEC state of the zone, and the DS record at the registrar. | Step D3. DNSSEC at Porkbun is a separate step after the move. |
| CNAME flattening | The setting of the zone, and each CNAME at the apex. | An ALIAS record at Porkbun, or A and AAAA records. |
| Zone-level settings | The TLS mode, the edge certificates, HSTS, the redirect to HTTPS, the cache rules, the firewall rules, the rate limits and the access rules of the zone. | The same function at the origin, or the end of it. |

Warning: a service behind the proxy or behind an Access application can lose its only protection with the move. Decide each such row before step C4.

For each row, the owner also saves the values of the feature in the private directory: a screen copy or an export from the dashboard.
The way back of step D7b needs these values when Cloudflare removed the zone.

Not verified: this list is complete. Cloudflare can have a function that this page does not name. The owner reads each part of the dashboard of the zone.

### A proxied record

A proxied record gives the address of Cloudflare as its public answer. The JSON list holds the origin as its content.
Not verified: Porkbun has no proxy for a record. This page plans with none.
Each proxied record needs one decision:

1. The origin address as a plain record at Porkbun.
2. A replacement service in front of the origin.
3. The end of the record.

With the first decision, the origin becomes public: its address is in the public DNS, and each client connects to it directly.
The origin loses the filter, the cache and the TLS endpoint of Cloudflare.
It needs a certificate that a public client trusts. An origin certificate of Cloudflare is not one.
A firewall of the origin that admits only Cloudflare then stops each client.

### The check of Phase A

1. The export and each page file exist in the private directory. Their paths and sizes are in the private sheet.
2. The count of the plan equals the number of table rows. Each host record is correct.
3. The owner has the inventory, the file `ttl-decisions.txt` and the table of step A8.
4. Each row with `decide`, each feature of step A8 that the zone uses and a DS record are items of the decision list.

End the phase shell with `exit`.

## 3. Phase B: the Porkbun zone state

Phase B reads the zone that Porkbun holds for the domain.
It changes nothing.

### B1. Read the zone

Start a phase shell with the block of step A1.
The request has the form of [the provider file](../../scripts/dns/porkbun.sh): a POST request with the host pair in a JSON body file of the mode 0600.
The path is `dns/retrieve/<apex domain>`. The kit itself reads one name with `dns/retrieveByNameType`.

```sh
records files "$dns_env" porkbun
apex=$(cat "$dns_work/apex")
curl -sS --max-time 30 -o "$dns_out/porkbun-zone-before.json" -H 'Content-Type: application/json' \
    --data "@$dns_work/porkbun.json" "$porkbun_api/dns/retrieve/$apex"
records state porkbun-zone-before.json
```

### B2. Name the state

| Result | Meaning | Next step |
| --- | --- | --- |
| The Porkbun API refuses the request | The zone is not ready, API access is off for the domain, or the pair is wrong. | Stop. Give the message to the owner. Do not repeat the request in a loop. |
| 0 records | The zone exists and is empty. | Phase C. |
| Default records only | Porkbun made records for its parking page: commonly an ALIAS or A record at the apex and a wildcard CNAME. | Step B3. |
| Other records | A person or a tool made records before. | Each one is an item of the decision list. |

Write the state and the counts into the private sheet.

### B3. Default records

A default record at the apex conflicts with the apex records of the plan: the apex then has two different answers.
A wildcard default record answers for each name with no record of its own.
It hides a missing record in the test of step C6, and it sends each unknown name to the parking page.

Name each default record that conflicts in the private sheet, with its id from `porkbun-zone-before.json`.
A default record is removed only after a decision of the owner.
The owner removes it in the Porkbun account, or the lead removes it with one request for each approved id.

Warning: this request deletes one record. Run it only for an id that the owner approved.

```sh
printf 'Id of the one record that the owner approved for removal: '
IFS= read -r record_id
printf '%s\n' "$record_id" | grep -Eqx '[0-9]+'
curl -sS --max-time 30 -o "$dns_work/answer.json" -H 'Content-Type: application/json' \
    --data "@$dns_work/porkbun.json" "$porkbun_api/dns/delete/$apex/$record_id"
records answer
echo "delete: Porkbun removed the record"
```

The way back is a new record with the content of `porkbun-zone-before.json`.

### The check of Phase B

1. The state is one of the four results, and the private sheet names it.
2. Each record of the Porkbun zone has a decision: keep it or remove it.

## 4. Phase C: recreate the record set at Porkbun

Phase C makes each planned record at Porkbun and proves the zone before the switch.
Porkbun does not answer for the domain yet, so no public answer changes.

### C1. The record set

The record set is each row of the plan file with the decision `copy`.
It holds all current records of the zone, and so the host records too.

| Rule | Value |
| --- | --- |
| Name, type and content | The same as at Cloudflare, or the value from the decision of the owner. |
| Priority of an MX or SRV record | The same as at Cloudflare, in the field `prio`. |
| TTL | The same as at Cloudflare. The helper changes no TTL by itself. An automatic TTL and a TTL below 600 are decisions of the owner. |
| Host records | A, the stack address, the TTL 600: the form of [the record set](../options/dns.md#the-record-set). |
| SOA and NS of the apex | Not in the record set. |

For a record whose answer changes with the move, a TTL of 600 lets a correction spread fast. The owner decides it.

### C2. Write the decisions into the plan file

Edit `planned-records.json` in the private directory after the owner decides each item.
Each row with `decide` gets one of two decisions:

- `copy`, with the type, the content and the TTL that Porkbun must hold. A row of an MX or SRV record needs a number in `prio`.
- `drop`, when the owner ends the record.

Rules for the file:

- Do not remove a row. Do not change the field `source` of a row: it links the row to its record of the inventory.
- A row with no Cloudflare-only feature keeps its name, type, content and priority.
- A row keeps its TTL. Only a row of `ttl-decisions.txt` gets another TTL.
- To change a record that has no Cloudflare-only feature, give its row the decision `drop` and add one new row.
- A new row has `"source": "new"` and the decision `copy`. Give it a row number that no other row has.
- A row of a record with structured fields holds them in the field `data`. The gate of step C6 needs each value of `data` in the answer of Porkbun. When the owner changes such a record, change the content and `data` together.
- A field of `data` that is no part of the content of an answer stops the gate of step C6. Remove such a field from the row only with a decision of the owner, and write the decision into the private sheet.
- An answer has one TTL for the records of one name and one type. When their rows have different TTLs, the gate of step C6 stops. Then the owner decides one TTL: the decision `drop` and one new row for each row that changes.

The owner decides the TTL of each row of `ttl-decisions.txt`: a number of seconds that Porkbun can hold.
For a row with a TTL of its own, write the number into the field `ttl` by hand and set the decision `copy`.
The block below writes one TTL into each row that still waits for its TTL only.
Run it only after the owner decides that one TTL for those rows.
Run it in the phase shell of Phase B: it needs the files of the block of step B1.
A row with another open decision stays open.

```sh
printf 'The TTL in seconds that the owner decides for these rows: '
IFS= read -r seconds
records ttl "$seconds"
```

Not verified: Porkbun refuses a TTL below 600, or it holds 600 in its place. Step C5 shows the second case as `ttl differs`.

The next steps stop while a row still has `decide`.
They stop also for a record of the inventory with no row, for an empty plan and for a host record that is not correct.

### C3. Dry-run diff

The diff compares the plan file with the read of step B1, record by record.
It makes no network call.
Phase C uses the phase shell of Phase B.
In a new phase shell, run the block of step B1 first: it makes the credential file and a new read.
Run the block of step B1 also when a record changed after the last read.

```sh
records diff porkbun-zone-before.json diff-before.txt
```

| Count | Meaning |
| --- | --- |
| `same` | Porkbun holds the record one time, with the planned TTL. |
| `ttl differs` | Porkbun holds the record with another TTL. The owner corrects it in the Porkbun account, or accepts it. |
| `duplicate` | Porkbun holds the record more than one time. The owner decides which copy to remove. |
| `create` | Porkbun lacks the record. Step C4 makes it. The rows are in `to-create.json`. |
| `only at Porkbun` | Porkbun holds a record that the plan lacks. The NS records of Porkbun at the apex are expected. Each other one needs a decision. |

The file `diff-before.txt` names each record.
A second line starts with `batch:`. It counts the records of `to-create.json` in two lists:

- `to-create-api.json`: each record whose type has a proof for the API. Step C4b makes these records.
- `to-create-by-hand.txt`: each other record. Step C4c makes these records.

Each run of the diff writes the three lists again from its own read of the zone.

### C4. Create the missing records

The API makes a record of `to-create.json` only when its type has a proof. The owner makes each other record by hand.

| Record type | Proof | Method |
| --- | --- | --- |
| A | [The provider file](../../scripts/dns/porkbun.sh) of the kit makes A records with the request `dns/create/<apex domain>`. | The API, in step C4b. |
| Each other type with a passed probe | The probe of step C4a. | The API, in step C4b. |
| Each other type with no passed probe | None. | By hand in the web form of the Porkbun account, in step C4c. |

### C4a. Probe each record type other than A

A probe is one test record of one type.
The block makes it, reads the zone, compares the record with the request and removes the record.
The probe passes when Porkbun holds exactly one record with the probe name, and it has the content, the priority and the TTL of the request.

The probe record has a harmless name: the label `dns-migration-probe-<type>` below the apex domain.
Service labels that start with `_` stay in front of it.
Its content and its priority are those of the first record of that type in `to-create.json`. Its TTL is 600.
Porkbun does not answer for the domain yet, so a probe record gives no public answer.

The first line of the block names each type that waits for a probe.
One approval covers the probes of these types. Ask for it with the list of the types.
For each type the block sends three requests: `dns/create`, `dns/retrieve` and `dns/delete`.
The last lines read the zone again and repeat the diff. The diff stops when a probe record is still in the zone.

```sh
records probe-types
while read -r kind <&3; do
    records probe-body "$kind"
    curl -sS --max-time 30 -o "$dns_work/answer.json" -H 'Content-Type: application/json' \
        --data "@$dns_work/body.json" "$porkbun_api/dns/create/$apex"
    if records probe-answer "$kind"; then
        curl -sS --max-time 30 -o "$dns_out/porkbun-probe-$kind.json" -H 'Content-Type: application/json' \
            --data "@$dns_work/porkbun.json" "$porkbun_api/dns/retrieve/$apex"
        records probe-compare "$kind" "porkbun-probe-$kind.json"
        while read -r record_id <&4; do
            curl -sS --max-time 30 -o "$dns_work/answer.json" -H 'Content-Type: application/json' \
                --data "@$dns_work/porkbun.json" "$porkbun_api/dns/delete/$apex/$record_id"
            records answer
        done 4<"$dns_work/probe-ids"
    fi
    sleep 1
done 3<"$dns_work/probe-types"
curl -sS --max-time 30 -o "$dns_out/porkbun-zone-before.json" -H 'Content-Type: application/json' \
    --data "@$dns_work/porkbun.json" "$porkbun_api/dns/retrieve/$apex"
records diff porkbun-zone-before.json diff-before.txt
```

| Result line | Meaning |
| --- | --- |
| `probe <type>: passed` | Step C4b makes each record of this type through the API. |
| `probe <type>: FAILED (<reason>)` | Each record of this type is a step by hand, in step C4c. |
| `batch: <n> records through the API (<types>); <n> records by hand (<types>)` | The two lists of the next steps, with the results of the probes. |

The count `only at Porkbun` of the diff must equal the count of step C3. A higher count shows a probe record that Porkbun holds under another name.
A failed removal of a probe record stops the block. Remove the record with the block of step B3 and a new approval. Then run the block again.
With no approval for the probes, do not run the block. Then each record of a type other than A is a step by hand, as the `batch` line of step C3 says.

### C4b. Create through the API

Ask the owner for the approval with the counts of the last `batch` line and the path of `to-create-api.json`.
The block first reads the zone and runs the diff again. So the loop never uses a list of an earlier read.
The block asks for the number of records that the owner approved. With another count in its list, the block ends before the loop and makes no record.
The loop makes records only. It edits no record and deletes no record.
The helper stops for a row whose type has no proof, so the loop makes no such record.
The loop stops at the first refused request. A second run of the block makes only the records that are still missing. It needs the approval of the owner for its own count.

```sh
curl -sS --max-time 30 -o "$dns_out/porkbun-zone-before.json" -H 'Content-Type: application/json' \
    --data "@$dns_work/porkbun.json" "$porkbun_api/dns/retrieve/$apex"
records diff porkbun-zone-before.json diff-before.txt
printf 'Number of records through the API that the owner approved: '
IFS= read -r approved
count=$(records count)
if [ "$count" != "$approved" ]; then
    echo "create: the list holds $count records, not the approved number; no record is made"
    exit 1
fi
index=0
while [ "$index" -lt "$count" ]; do
    records body "$index"
    curl -sS --max-time 30 -o "$dns_work/answer.json" -H 'Content-Type: application/json' \
        --data "@$dns_work/body.json" "$porkbun_api/dns/create/$apex"
    records answer
    index=$((index + 1))
    sleep 1
done
echo "create: $index of $count records"
```

The way back is the removal of each new record by its id, with the request of step B3 and a new approval.
A new record has no public effect before the switch.

### C4c. Make the other records by hand

The file `to-create-by-hand.txt` holds each record whose type has no proof for the API.
The owner makes each row in the web form of the Porkbun account: the name, the type, the content, the priority and the TTL of the row.
After the last record, run step C5 and step C6.
The read of step C5 and the test of step C6 prove each record, also a record that a person made.
A record that the form holds in another way shows in step C5 as `create` or as `only at Porkbun`.

### C5. Read again and compare

```sh
curl -sS --max-time 30 -o "$dns_out/porkbun-zone-after.json" -H 'Content-Type: application/json' \
    --data "@$dns_work/porkbun.json" "$porkbun_api/dns/retrieve/$apex"
records diff porkbun-zone-after.json diff-after.txt
```

The result must be `0 ttl differs`, `0 duplicate` and `0 create`.
Each record of the count `only at Porkbun` must be a record that the owner decided to keep, or an NS record of Porkbun at the apex.

### C6. Test at the Porkbun name servers before the switch

This test is a required gate. Step D4 does not start before it passes.
It asks one name server of Cloudflare and two name servers of Porkbun directly for each planned record.
The direct answers are the only proof that the Porkbun zone answers before the name server change.
No read of the Porkbun API replaces them.
The form of one question is `dig @<porkbun ns> <name> <type>`.

The gate makes two comparisons for each question.

1. The plan: each direct answer of each Porkbun name server must be the planned records of that name and type. It holds each planned record one time and no other record. Each record has the planned content, the planned priority and the planned TTL. For a row with the field `data`, the content of the answer holds each value of `data`. For an SRV record and a CAA record, the helper knows the place of each field in the answer, and each field must be in its place.
2. Cloudflare: the answer of the Porkbun name server must equal the answer of the Cloudflare name server, or the difference must be an expected difference.

An expected difference is a difference of the second comparison only.
It never permits an answer of Porkbun that differs from the plan.
A Porkbun answer that is not the planned record stops the gate, also when its name and type are a line of `expected-differences.txt`.

Before the test, write each expected difference into the private sheet and into the file `expected-differences.txt` of the private directory.
The file has one line for each difference: the name, one blank and the type, as in `names.txt`.

- A proxied record: Cloudflare answers its own address, Porkbun answers the planned content.
- A CNAME at the apex: Cloudflare answers addresses, Porkbun answers through its ALIAS record.
- A record that the owner replaced: Cloudflare answers the old content, Porkbun answers the planned content.
- A TXT record with another split into strings: the joined text must be equal to the planned content.

```sh
records names
printf 'One name server of the Cloudflare zone: '
IFS= read -r old_ns
printf 'One default name server of Porkbun: '
IFS= read -r new_ns
printf 'A second default name server of Porkbun: '
IFS= read -r new_ns_2
compare_servers "$old_ns" "$new_ns" preswitch.txt
compare_servers "$old_ns" "$new_ns_2" preswitch-2.txt
records preswitch preswitch.txt preswitch-2.txt
```

The last command is the gate. It passes when each line is true:

1. Each question of `names.txt` has a direct answer of both name servers of Porkbun.
2. Each direct answer of both name servers of Porkbun is the planned record: the content, the priority, each value of the field `data` and the TTL. No line of `expected-differences.txt` changes this rule.
3. Each difference to the answer of Cloudflare is a line of `expected-differences.txt`.
4. The two result files come from two different name servers of Porkbun.

Each other result stops the block with the text `the switch is BLOCKED`. The file `preswitch-gate.txt` names each finding.
A finding names the record by its name and its type, with one reason: `another record set`, `another TTL`, `another value of the field data` or `no address record`. It holds no content.
The two result files hold both answers of each difference, and after the line `direct:` each record of the Porkbun name server with its TTL. Read each expected difference and each finding there.

An answer that is not the planned record has one of two causes.

- The Porkbun zone holds another record. The owner decides the correction. Then run step C5 and this step again.
- The plan is not the decision of the owner. Correct the plan with step C2. Then run step C3 and each later step again.

An ALIAS record has no planned address. For its question the gate needs one address record or more, each with the planned TTL of the ALIAS record. It compares no content.
The last count of the line `preswitch:` is the number of these questions. The owner reads their answers in the result files.
Not verified: a name server of Porkbun answers the address records of an ALIAS record with the TTL of the ALIAS record. Another TTL stops the gate.

Not verified: the text form of each record type in an answer of `dig` is the form of the plan file. Another form stops the gate. Then the lead stops and reports the record type: the helper needs a rule for that form.
Not verified: a name server of Porkbun answers with the TTL that the record has in the Porkbun API.

Not verified: the name servers of Porkbun answer for the zone before the registrar points to them.
When they give no direct answer, the switch is blocked. The diff of step C5 is not a replacement.
The lead stops and gives the owner this question:

```text
The name servers of Porkbun give no direct answer for the zone before the name server change.
The switch is blocked.
How can the Porkbun zone give direct answers before the change: with a setting of the Porkbun account, or with the support of Porkbun?
```

The procedure continues only after the gate passes.

### C7. What Porkbun cannot reproduce

| Function at Cloudflare | At Porkbun |
| --- | --- |
| The proxy of a record | Not verified: Porkbun has no proxy for a record. The record answers its planned content. |
| CNAME flattening at the apex | Not verified: the ALIAS record type gives the same public answer. |
| Email routing | Not verified: the forward stops for a zone that is not at Cloudflare. A replacement must run before the switch. |
| Each other feature of step A8 | No record holds it. |
| The automatic TTL | A fixed TTL that the owner decides. Not verified: the smallest TTL of Porkbun is 600. |

### The check of Phase C

1. The diff of step C5 shows no missing record, no duplicate and no TTL difference.
2. The gate of step C6 passes at two name servers of Porkbun.
3. The Porkbun zone holds no probe record.
4. The private sheet holds each result and each expected difference.

End the phase shell with `exit`.

## 5. Phase D: the name server change at the registrar

Phase D changes which provider answers for the domain.
Only the owner makes the change.

### D1. The gate before the switch

Each line must be true before the owner changes a name server.

1. The checks of the phases A, B and C pass.
2. Each item of the decision list has a decision. Each replacement service runs.
3. The parent zone holds no DS record, or step D3 is complete.
4. The private sheet holds the two name servers of Cloudflare, for the way back.
5. The Cloudflare zone did not change since Phase A. The block below proves it.
6. The gate of step C6 passes. The block below runs it again.

Start a phase shell with the block of step A1.
The block reads the zone a second time in both forms: the export of step A3 and the JSON list of step A4.
The helper compares the two exports record by record. It leaves out the SOA records only: they hold the serial number of the zone.
A record of another type with the text `SOA` in its content stays in the comparison.
The helper compares the two JSON lists record by record: the name, the type, the content, the TTL, the priority, the proxy state and each field of `data`.
A change of one field of `data` is a difference, also when the content of the record is the same text. The order of the fields in `data` is no difference.

The last command of the block is the gate of step C6, with the result files of that step.
It compares each saved direct answer of Porkbun with the plan file again. So the block stops when a planned record no longer equals its saved answer.
An expected difference permits a difference to the answer of Cloudflare only, as in step C6.
When a record of the Porkbun zone changed after step C6, run the block of step C6 again first: it asks the name servers again.

```sh
records files "$dns_env" cloudflare
zone_id=$(cat "$dns_work/zone-id")
curl -sS --fail --max-time 60 -H "@$dns_work/cloudflare.header" \
    -o "$dns_out/cloudflare-zone-final.bind" "$cloudflare_api/zones/$zone_id/dns_records/export"
list_zone cloudflare-final
records unchanged
records preswitch preswitch.txt preswitch-2.txt
```

The block must print the line `zone: no record changed since the export` and then the line `preswitch:` of the gate.
Each difference stops the block with a nonzero status and the text `the zone CHANGED since the export`.
The file `zone-changes.txt` names each difference.
A changed zone returns the procedure to Phase A.
From this step to the end of the wait of step D5, nobody changes a record at Cloudflare.

### D2. TTLs before the switch

- The TTL of the delegation belongs to the registry. Nobody can lower it.
- A record with the same content at both providers needs no lower TTL: each provider gives the same answer during the wait.
- A proxied record has an automatic TTL at Cloudflare. It cannot be lowered.
- A lower TTL helps only for a plain record whose content changes with the move. Lower it at Cloudflare one full old TTL before the switch.

A TTL change at Cloudflare is a record write. It needs its own approval.
Its form is the request `PATCH zones/<zone id>/dns_records/<record id>` of [the provider file](../../scripts/dns/cloudflare.sh), with the complete record and the new TTL.
Such a write changes the zone, and step D1 stops for each record that changed since Phase A.
So make the write before Phase A, or run Phase A again after it.
The plan file then holds the new TTL of the record, and step C2 gets the decision of the owner for it.

### D3. A DS record

Do this step only when step A5 counts a DS record.

1. The owner removes the DS record of the domain in the Porkbun account. The registry then holds no DS record.
2. The lead repeats the DS read below, in the phase shell of step D1, until the count is 0.
3. Wait one full TTL of the old DS record before step D4. A resolver can hold the old record for that time. The TTL is the number in the DS line of `public-before.txt`.

```sh
apex=$(cat "$dns_work/apex")
parent=${apex#*.}
parent_ns=$(dig +short NS "$parent." | head -n 1)
test -n "$parent_ns"
echo "DS records at the parent zone: $(dig +norecurse +short "@$parent_ns" DS "$apex" | grep -c . || true)"
```

The way back is step D7c. The old DS record of `public-before.txt` fits only the keys of the old Cloudflare zone.
DNSSEC at Porkbun is a separate step after the move.

### D4. The name server change

The owner does this step in the Porkbun account. An agent does not open the account.

1. Open the details of the apex domain.
2. Open the name server setting of the domain.
3. Replace the two name servers of Cloudflare with the default name servers of Porkbun. Write each name as the account shows it.
4. Save. Write the time in UTC into the private sheet.

This page holds no name of a name server: the kit documents none.
The names are `<porkbun ns>` in each command, and the private sheet holds them.

### D5. The wait

The registry publishes the new delegation after the change.
A resolver can hold the old delegation for its full TTL. Step A5 reads that TTL.
During the wait, some resolvers ask Cloudflare and some ask Porkbun.
Both zones hold the same record set, so both give a working answer.

Keep the Cloudflare zone unchanged during the wait: no edit and no deletion.
A record that must change during the wait changes at both providers.

### D6. Verify

The forms of the questions, for two public resolvers:

```text
dig +short NS <apex domain> @1.1.1.1
dig +short NS <apex domain> @8.8.8.8
dig +short A <name>.<EC2_DOMAIN> @1.1.1.1
dig +short A <name>.<EC2_DOMAIN> @8.8.8.8
dig +short MX <apex domain> @1.1.1.1
dig +short TXT <apex domain> @8.8.8.8
dig +short TXT _dmarc.<apex domain> @1.1.1.1
```

The block asks these questions and each other question of `names.txt`. It prints states and counts only.
Start a phase shell with the block of step A1.

```sh
records files "$dns_env"
apex=$(cat "$dns_work/apex")
printf 'One default name server of Porkbun: '
IFS= read -r new_ns
printf 'Stack address: '
IFS= read -r address
expected=$(dig +short "@$new_ns" NS "$apex" | sort)
parent=${apex#*.}
parent_ns=$(dig +short NS "$parent." | head -n 1)
test -n "$parent_ns"
dig +norecurse +noall +authority +answer "@$parent_ns" NS "$apex" >"$dns_out/public-after.txt" 2>&1
if [ -n "$expected" ] && [ "$expected" = "$(awk '$4 == "NS" {print $5}' "$dns_out/public-after.txt" | sort)" ]; then
    echo "delegation at the parent zone: Porkbun"
else
    echo "delegation at the parent zone: not Porkbun yet"
fi
for resolver in 1.1.1.1 8.8.8.8; do
    found=$(dig +short "@$resolver" NS "$apex" | sort)
    if [ -n "$expected" ] && [ "$expected" = "$found" ]; then
        echo "NS at $resolver: Porkbun"
    else
        echo "NS at $resolver: not Porkbun yet"
    fi
    hosts=0
    right=0
    while read -r name; do
        hosts=$((hosts + 1))
        if [ "$(dig +short "@$resolver" A "$name" </dev/null)" = "$address" ]; then
            right=$((right + 1))
        fi
    done <"$dns_work/host-names"
    echo "host records at $resolver: $right of $hosts answer the stack address"
    compare_servers "$resolver" "$new_ns" "after-$resolver.txt"
done
```

The switch is complete when each line is true:

1. The delegation line says `Porkbun`.
2. The NS line of each resolver says `Porkbun`.
3. The host record line of each resolver counts each host record, for example `5 of 5`.
4. The count `different` is 0 at each resolver, and the count with no answer is 0. The questions hold the MX records and the TXT records for mail.

Repeat the block after the TTL of the old delegation. Then no resolver holds the old delegation.
Ask the owner for one test of each service of the decision list: one received mail, one sent mail, each site.

### D7. The way back

The way back sets name servers of Cloudflare again at the registrar.
It needs a Cloudflare zone with each record and each feature of the time before the switch.
No step of this page deletes the Cloudflare zone.

Not verified: how long Cloudflare keeps a zone whose name servers point to another provider.
Not verified: Cloudflare removes such a zone by itself after some time.

So the owner first looks for the zone in the Cloudflare dashboard.
Step D7a is the way back with the zone. Step D7b is the way back after Cloudflare removed the zone.

Warning: after the certificate exists, a way back to Cloudflare stops its renewal. The DNS-01 challenge needs the Porkbun zone to answer.

### D7a. The Cloudflare zone still exists

1. The lead runs the block of step D1 again. The line `zone: no record changed since the export` proves each record and its proxy state.
2. The owner compares each feature of step A8 in the dashboard with the saved values.
3. The owner sets the two name servers of Cloudflare again, in the same setting of the Porkbun account. The names are in the private sheet.
4. The lead repeats step D6 with one name server of Cloudflare in the place of the Porkbun name server.
5. With a DS record that step D3 removed, step D7c follows.

While the zone exists unchanged, this way back needs no record write.
The expected time is the time of the registry for the change, then at most the TTL of the delegation for each resolver.
Both zones answer during that time, so the way back alone stops no service.

### D7b. Cloudflare removed the zone

The BIND export alone does not restore the zone. It holds no setting, and no check proved that it holds the proxy state.
The source of the records is the JSON inventory of step A4. It holds the proxy state and the TTL of each record, and the field `data` of a record with structured fields.
The source of the features is the saved values of step A8.

1. The owner adds the apex domain to the Cloudflare account again. This makes a new zone with a new zone id.
2. The owner writes the name servers that the dashboard shows for the new zone into the private sheet. Not verified: a new zone gets the old name servers.
3. The owner makes a new token with the permission Zone, DNS, Edit on the new zone only. The token of the old zone does not cover the new zone. The new token and the new zone id go into a copy of the private environment file.
4. The owner makes again each feature of step A8 that makes records of its own: the email routing, each public host name of a tunnel, and each custom domain of Pages or of a Worker.
5. The lead reads the new zone and lists each record of the inventory that it lacks. This is the first block below.
6. The lead makes each missing record with its proxy state. This is the second block below.
7. The lead runs the first block again. Its line must start with `restore: 0 records to make`. Each record that is only in the new zone needs a decision of the owner.
8. The owner makes again each other feature of step A8: the page rules, the redirect, transform and origin rules, the Access applications, the choice of CNAME flattening and the zone-level settings.
9. The lead tests the new zone before the change. The test is the comparison of step C6, with one name server of Porkbun as the first server and one name server of the new zone as the second server. A proxied record answers an address of Cloudflare there.
10. The owner sets the name servers of the new zone in the Porkbun account.
11. The lead repeats step D6 with one name server of the new zone in the place of the Porkbun name server.
12. With a DS record that step D3 removed, step D7c follows.

Start a phase shell with the block of step A1.
Give it the copy of the private environment file and the private directory of Phase A: that directory holds the inventory.

```sh
records files "$dns_env" cloudflare
zone_id=$(cat "$dns_work/zone-id")
list_zone cloudflare-restored
records restore
```

The command writes `cloudflare-restore.json` and `restore-diff.txt`.
It prints three counts: the records to make, the number of them with the proxy on, and the records that are only in the new zone.

The new zone holds a record of the inventory only when each part is equal: the name, the type, the content, the TTL, the priority, the proxy state and each field of `data`.
A record of the new zone with another value in one field of `data` is not the record of the inventory.
Then the command plans the record of the inventory with its own `data`, and it counts the record of the new zone as a record that is only in the new zone.
Step 7 gives that record to the owner for a decision: the new zone must not hold both.

Warning: the next block writes records into the new Cloudflare zone. Run it only with the approval of the owner for the count of the line `restore`.

The request is the create request of [the provider file](../../scripts/dns/cloudflare.sh): `POST zones/<new zone id>/dns_records`.
The kit sends it for an A record with no proxy only.
Not verified: this request with the proxy on, with the automatic TTL, with a priority and for each other record type.

```sh
count=$(records count cloudflare-restore.json)
index=0
while [ "$index" -lt "$count" ]; do
    records restore-body "$index"
    curl -sS --max-time 30 -o "$dns_work/answer.json" -H "@$dns_work/cloudflare.header" \
        -H 'Content-Type: application/json' -X POST --data "@$dns_work/body.json" \
        "$cloudflare_api/zones/$zone_id/dns_records"
    records cloudflare-answer
    index=$((index + 1))
    sleep 1
done
echo "restore: $index of $count records"
```

The loop stops at the first refused request.
A record that a feature of Cloudflare manages comes back with its feature in step 4, not with this request.

Not verified: Cloudflare finds and imports records for a new zone by itself. Step 7 shows each such record: it is not in the count to make, or it is only in the new zone.
Not verified: a zone that the registrar does not point to yet holds records and answers a direct question.
The expected time is the time for the steps 1 to 9, then the time of the registry, then at most the TTL of the delegation.
The Porkbun zone answers during that time.

### D7c. A removed DS record

Do this step only when step D3 removed a DS record.
Do it last, after the name servers of Cloudflare answer at both public resolvers in step D6.

1. After step D7a: the owner reads the DNSSEC state of the zone in the Cloudflare dashboard. With DNSSEC on, the dashboard shows the DS record of the zone. It must equal the DS line of `public-before.txt`. Then the owner adds that DS record again in the Porkbun account.
2. After step D7b: the old DS record does not fit the keys of a new zone. Do not add it again. The owner turns DNSSEC on for the new zone. Then the owner adds the new DS record that the dashboard shows in the Porkbun account.
3. The lead repeats the DS read of step D3. The count must be 1 or more.
4. The lead asks a resolver that validates for one name. The form is `dig +dnssec A <name> @1.1.1.1`. The answer must have the flag `ad` and must not have the status `SERVFAIL`.

Warning: a DS record that does not fit the keys of the zone stops each answer for the domain at a resolver that validates. Add a DS record only when line 1 or line 2 shows that it fits.

The way back for this step is the removal of the DS record in the Porkbun account.
Not verified: Cloudflare keeps the keys of a zone while the name servers point to another provider.

## 6. Phase E: the kit

Phase E confirms the host records with the kit and gets the certificate.
Start it only after step D6 passes at both resolvers.
Run the kit commands from the clone of the snapshot that the host runs.

### E1. Print the record set

```sh
set +x
printf 'Private environment file: '
IFS= read -r dns_env
test -f "$dns_env" || exit 1
scripts/aws-host.sh --env-file "$dns_env" dns --plan
```

The steps E2 and E5 use the variable `dns_env` of this block.
The command reads the Elastic IP from the stack through AWS. It calls no DNS provider.
It prints the host records. The address of each line must be the stack address of the private sheet.

### E2. Set the host records at Porkbun

```sh
scripts/aws-host.sh --env-file "$dns_env" dns
```

The command reads each host record at Porkbun. It makes or corrects a record that differs. It deletes no record.
Phase C made the host records, so the expected result is one line with `no change` for each host record.
A line with `done (create)` or `done (edit)` shows a difference that Phase C did not find. Stop, then read the zone and run the diff of step C5 again.

### E3. Repeat the bootstrap

The [bootstrap](host-boot.md) runs on the host, in the root shell of an approved Systems Manager session.
Use `sudo -i` to enter that shell. Then start one new shell with the command `sh`, as in step A1.
A failed bootstrap ends that shell with a nonzero status. Then no later line of the block runs.

```sh
set +x
set -eu
umask 077
printf 'Installed project name: '
IFS= read -r project
printf '%s\n' "$project" | grep -Eqx '[a-z][a-z0-9-]{2,30}'
repo=/opt/$project
stack_env=/etc/$project/stack.env
state=/srv/data/$project
test -d "$repo/.git"
test -f "$stack_env"
bootstrap_log=$(mktemp "$state/dns-bootstrap.XXXXXX")
if sh "$repo/scripts/host-bootstrap.sh" --stack-env "$stack_env" >"$bootstrap_log" 2>&1; then
    echo "bootstrap: done"
else
    echo "bootstrap: FAILED"
    exit 1
fi
```

The block must print `bootstrap: done`.
A failed bootstrap stops the procedure: the block prints `bootstrap: FAILED` and ends its shell with the status 1.
Do not run the block of step E4 after a failed bootstrap.
Read the log file privately. It can hold a name of the domain.
It is the newest file with a name that starts with `dns-bootstrap.` in the data directory of the project.

In the source, a repeat starts the edge services again only when a rendered Pangolin file changed.
A repeat with unchanged files leaves a running Traefik as it is.
Not verified: Traefik asks for the certificate again without a new start.

Not verified: the resolver that Traefik uses for its DNS-01 check.
That resolver can hold the old delegation for its TTL. Then the check asks the old name servers, and the request fails.
Such a failure is no defect of the zone. Repeat the step after the TTL of the old delegation.

### E4. Check the certificate

The [Pangolin page](../options/pangolin.md#files-and-directories) names the certificate store: the Traefik directory `/srv/data/pangolin/traefik`.
The overlay `compose.pangolin.yaml` binds its part `letsencrypt` into Traefik, and Traefik writes the file `acme.json` there.

Warning: `acme.json` holds the private key of the certificate. Never print the file.

The first block reads the store and the Traefik log, in the shell of step E3.
Run it only after the line `bootstrap: done`. It uses the variables of that block.
It prints counts only.

```sh
store=/srv/data/pangolin/traefik/letsencrypt/acme.json
python3 -I - "$store" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        found = (json.load(handle).get("letsencrypt") or {}).get("Certificates") or []
except (OSError, ValueError, AttributeError):
    found = []
print("certificates:", len(found))
for item in found:
    domain = item.get("domain") or {}
    names = [str(domain.get("main", ""))] + [str(name) for name in domain.get("sans") or []]
    print("wildcard name:", "yes" if any(name.startswith("*.") for name in names) else "no")
PY
traefik_log=$(mktemp "$state/dns-traefik.XXXXXX")
cd "$repo"
env -i PATH="$PATH" HOME="$HOME" docker compose --env-file "$state/.env" logs --since 30m traefik \
    >"$traefik_log" 2>&1 || true
echo "log lines with an ACME error: $(grep -Eci 'acme.*error|error.*acme|unable to obtain' "$traefik_log" || true)"
```

The certificate exists when the store shows one certificate with `wildcard name: yes`.

When the store shows no certificate, ask the owner for a new start of Traefik.
It is a service action with its own approval. It interrupts each public route for some seconds.

```sh
cd "$repo"
env -i PATH="$PATH" HOME="$HOME" docker compose --env-file "$state/.env" restart traefik
```

Wait two minutes, then run the first block again.
Do not repeat the restart in a loop: the certificate authority limits failed requests.
The two log files stay on the data volume with the mode 0600. Remove them after the acceptance.

The second block runs on the development machine. It is one public TLS connection to the edge.

```sh
printf 'Complete name of one host record: '
IFS= read -r host_name
openssl s_client -connect "$host_name:443" -servername "$host_name" -verify_hostname "$host_name" \
    </dev/null 2>/dev/null | grep 'Verify return code'
```

The result `Verify return code: 0 (ok)` proves a trusted certificate for that name.

### E5. Verify the host

```sh
scripts/aws-host.sh --env-file "$dns_env" verify
```

The command must end with `verify: pass`.
`verify` makes no public request. Its skip line names the public routes and the certificates.
So step E4 is the proof of the certificate, not this command.

### Next

These steps follow this page. Each one has its own approval.

1. The [first setup of Pangolin](../options/pangolin.md#first-setup-after-boot).
2. The [first login of Copilot](../options/copilot.md), with that option.
3. The [Entra checks](../entra.md).
4. A decision of the owner on the Cloudflare token: delete it, or keep it for the way back.
5. A decision of the owner on the Cloudflare zone, after the wait of step D5: keep it, or delete it.

## 7. The approval table

Each row is one separate question to the owner.
An approval covers the one row only. A read of a private zone is a live step too.
The steps A1, A2, A6, C2 and C3 make no call and need no row.

| Step | Actor | Exact command or form | What it changes | Way back |
| --- | --- | --- | --- | --- |
| A3 | Lead | The block of step A3: `GET zones/<zone id>/dns_records/export`. | Nothing at Cloudflare. One file in the private directory. | Not necessary. |
| A4 | Lead | The block of step A4: `GET zones/<zone id>/dns_records?per_page=100&page=<page>`. | Nothing at Cloudflare. One file for each page. | Not necessary. |
| A5 | Lead | The block of step A5: `dig` to the parent zone and to two public resolvers. | Nothing. | Not necessary. |
| A7, A8 | Owner | The decision list of the private sheet, with each feature of step A8 and each TTL decision. | Nothing at a provider. | A new decision before step C4. |
| B1 | Lead | The block of step B1: `POST dns/retrieve/<apex domain>`. | Nothing at Porkbun. | Not necessary. |
| B3 | Owner, or lead | The Porkbun account, or the block of step B3: `POST dns/delete/<apex domain>/<record id>`, one approval for each id. | It deletes one default record at Porkbun. | A new record with the content of `porkbun-zone-before.json`. |
| C4a | Lead | The block of step C4a: `POST dns/create/<apex domain>`, `POST dns/retrieve/<apex domain>` and `POST dns/delete/<apex domain>/<record id>` for one probe record of each type that waits for a probe. One approval for the list of the types. | One test record of each type at Porkbun, until its removal. No public answer. | The removal of a probe record that stays, with the block of step B3. |
| C4b | Lead | The block of step C4b: `POST dns/retrieve/<apex domain>`, then `POST dns/create/<apex domain>` for each record of `to-create-api.json`. One approval for the count of the `batch` line. | New records at Porkbun. No public answer. | The removal of each new record by its id, with a new approval. |
| C4c | Owner | The web form of the Porkbun account, for each record of `to-create-by-hand.txt`. | New records at Porkbun. No public answer. | The removal of each new record in the Porkbun account. |
| C5 | Lead | The block of step C5: `POST dns/retrieve/<apex domain>`. | Nothing. | Not necessary. |
| C6 | Lead | The block of step C6: `dig @<porkbun ns> <name> <type>` at two name servers of Porkbun, and `dig @<cloudflare ns> <name> <type>`. | Nothing. | Not necessary. |
| D1 | Lead | The block of step D1: the export request of step A3 and the list request of step A4 again. | Nothing at Cloudflare. | Not necessary. |
| D2 | Lead | `PATCH zones/<zone id>/dns_records/<record id>`, only for a TTL that the owner names. | The TTL of one record at Cloudflare. | The same request with the old TTL. |
| D3 | Owner | The Porkbun account: remove the DS record of the domain. Only with a DS record. | DNSSEC of the domain ends. | Step D7c: a DS record that fits the keys of the zone. |
| D4 | Owner | The Porkbun account: the name servers of the domain become the default name servers of Porkbun. | The provider that answers for each record of the domain. | Step D7: name servers of Cloudflare again. |
| D6 | Lead | The block of step D6: `dig` to two public resolvers and to `<porkbun ns>`. | Nothing. | Not necessary. |
| D7a | Owner | The Porkbun account: the two name servers of Cloudflare again. Only for a way back. | The provider that answers for each record of the domain. | Step D4 again. |
| D7b, zone | Owner | The Cloudflare dashboard: a new zone for the apex domain, a new token for it, and each feature of step A8 from the saved values. Only when Cloudflare removed the zone. | A new zone at Cloudflare. No public answer before the name server change. | The removal of the new zone in the dashboard. |
| D7b, read | Lead | The first block of step D7b: `GET zones/<new zone id>/dns_records?per_page=100&page=<page>`. | Nothing at Cloudflare. | Not necessary. |
| D7b, records | Lead | The second block of step D7b: `POST zones/<new zone id>/dns_records` for each record of `cloudflare-restore.json`. | New records in the new Cloudflare zone, with the proxy state of the inventory. | The removal of each new record in the dashboard. |
| D7b, name servers | Owner | The Porkbun account: the name servers of the new Cloudflare zone. | The provider that answers for each record of the domain. | Step D4 again. |
| D7c | Owner | The Porkbun account: the DS record that fits the keys of the Cloudflare zone. Only when step D3 removed a DS record. | DNSSEC of the domain starts again. | The removal of the DS record. |
| E1 | Lead | `scripts/aws-host.sh dns --plan`. It can run before step A6. | Nothing. It reads the stack through AWS. | Not necessary. |
| E2 | Lead | `scripts/aws-host.sh dns`. | It makes or corrects a host record at Porkbun. The expected result is no change. | The old content of the record by hand. The command deletes no record. |
| E3 | Lead | The block of step E3: `host-bootstrap.sh` in the Systems Manager session. | It can write the environment file and start services again. | The [host boot page](host-boot.md) gives the behavior of a repeat. |
| E4, read | Lead | The first block of step E4 on the host, and the second block from the development machine. | Nothing. | Not necessary. |
| E4, restart | Lead | `docker compose restart traefik` in the form of step E4. Only with no certificate. | Each public route stops for some seconds. | Not necessary. |
| E5 | Lead | `scripts/aws-host.sh verify`. | Nothing. It runs read commands on the host. | Not necessary. |

## 8. Not verified

No call to Cloudflare, to Porkbun, to a registrar or to a host ran for this page.

The helper script:

- Not verified: the helper script with a real answer of a provider. Its self-test and the offline run use fixtures and stubs.

Cloudflare:

- Not verified: a token with only the permission Zone, DNS, Edit on one zone passes the export request and the list request.
- Not verified: the form of the export request `GET zones/<zone id>/dns_records/export` and the form of its answer.
- Not verified: the export holds exactly one SOA record, and the serial number is the only part that changes between two exports of an unchanged zone.
- Not verified: the export says which record has the proxy on.
- Not verified: the list request with the fields `per_page` and `page`.
- Not verified: the fields of the JSON list that the helper reads: `result_info`, `id`, `proxied`, `ttl`, `priority`, `data` and the text form of a TXT content.
- Not verified: which record types have the field `data`, and the names of its fields. The self-test uses an SRV record with the fields `priority`, `weight`, `port` and `target`. The helper expects the fields `flags`, `tag` and `value` for a CAA record.
- Not verified: each field of `data` is a part of the content of an answer. The gate of step C6 knows the place of each field for an SRV record and a CAA record only. For each other type it compares the values and not their places.
- Not verified: two reads of an unchanged record give the same values in `data`, and a record that step D7b makes from `data` gets the same `data` in the new zone.
- Not verified: the helper finds each Cloudflare-only feature. It reads the name of a target only.
- Not verified: the feature list of step A8 is complete.
- Not verified: how long Cloudflare keeps a zone whose name servers point to another provider.
- Not verified: Cloudflare removes such a zone by itself.
- Not verified: a new zone gets the old name servers.
- Not verified: the create request `POST zones/<new zone id>/dns_records` with the proxy on, with the automatic TTL, with a priority, with the field `data` and for each record type other than A.
- Not verified: Cloudflare imports records into a new zone by itself.
- Not verified: a Cloudflare zone that the registrar does not point to holds records and answers a direct question.
- Not verified: Cloudflare keeps the DNSSEC keys of a zone while the name servers point to another provider.
- Not verified: email routing stops for a zone that is not at Cloudflare.

Porkbun:

- Not verified: the path `dns/retrieve/<apex domain>` and the form of its answer. The kit uses `dns/retrieveByNameType` only.
- Not verified: the request `dns/create` for a type other than A, for an empty name at the apex, and with the field `prio`. The probe of step C4a tests one record of each type. It does not test each content.
- Not verified: Porkbun accepts the probe name, and a probe record that Porkbun holds has the name of the request.
- Not verified: the path `dns/delete/<apex domain>/<record id>`.
- Not verified: the text form of a TXT content, with no quotes.
- Not verified: the smallest TTL is 600, and what Porkbun does with a lower TTL.
- Not verified: Porkbun has no proxy for a record.
- Not verified: the API changes a zone while the name servers of the domain are at another provider.
- Not verified: the default records of a new zone, and their types.
- Not verified: the ALIAS record type at the apex gives the answer of CNAME flattening.
- Not verified: the name servers of Porkbun answer for the zone before the registrar points to them. Without these answers, step C6 blocks the switch.
- Not verified: a name server of Porkbun answers with the TTL that the record has in the Porkbun API. Another TTL stops the gate of step C6.
- Not verified: the address records that Porkbun answers for an ALIAS record. The gate of step C6 compares no content for them.
- Not verified: the web form of the Porkbun account holds a record in the form that the API reads.
- Not verified: the names of the pages and of the fields in the Porkbun account, and the names of the default name servers.

The registry and the resolvers:

- Not verified: the domain has a DS record. Step A5 reads it.
- Not verified: the TTL of the delegation and the time of the registry for a name server change.
- Not verified: the `dig` commands of this page against real servers. A stub gave the answers.
- Not verified: a name server gives the NS records of a delegated name in the authority part of its answer, in the form that the function `ask` reads.
- Not verified: the answer form of `dig +noall +answer +authority` that the gate of step C6 reads: one line for each record, with the name, the TTL in seconds, the class, the type and the content.
- Not verified: the text form of each record type in that answer is the form of the plan file, and it holds each value of the field `data`. Another form stops the gate of step C6.
- Not verified: the DNSSEC check of step D7c with the flag `ad`.

The host:

- Not verified: a bootstrap repeat gets the certificate without a new start of Traefik.
- Not verified: the form of `acme.json` that the store check reads, and the text of an ACME error in the Traefik log.
- Not verified: the resolver of Traefik for the DNS-01 check, and the limits of the certificate authority.
- Not verified: the Compose commands of step E4 and the TLS check on a real host.
- Not verified: `scripts/aws-host.sh dns` prints `no change` for a record that the helper made.

The limits of [the DNS scripts](../options/dns.md#not-verified), of [the Pangolin edge](../options/pangolin.md) and of [the host boot](host-boot.md#known-limits) apply too.

#!/bin/sh
# The DNS provider cloudflare of scripts/dns-records.sh: the records of the host in one
# zone of Cloudflare, through the Cloudflare API version 4. scripts/dns-records.sh loads
# this file with `.`; the file does not run alone. See docs/options/dns.md.
#
# Names of the environment file:
#   EC2_CLOUDFLARE_API_TOKEN   one API token with the right to edit the DNS records of
#                              one zone (the permission Zone, DNS, Edit on that zone)
#   EC2_CLOUDFLARE_ZONE_ID     the id of the zone that holds EC2_DOMAIN: 32 hexadecimal
#                              digits. It is not a secret.
#
# The requests. The token is in a header file with the mode 0600 (curl -H @file). It is
# in no address and in no argument.
#   GET   zones/<zone>/dns_records?type=A&name.exact=<name>
#                                                       read each A record of one name
#   POST  zones/<zone>/dns_records                      make one A record
#   PATCH zones/<zone>/dns_records/<record>             correct one A record
# A record is correct when its name has one A record with the address, and the record is
# not proxied. A proxied record gives an address of Cloudflare, not the address of the
# host: the certificate of the edge and a tunnel need the address of the host. The script
# makes and corrects each record as not proxied ("DNS only").
# A name with more than one A record stops the script: it deletes no record.
# The script reads the answer of the read request again by name and by type, so a record
# of another name in the answer changes nothing.
#
# Not verified: no real call ran. The tests used a stub of curl.
#   - Not verified: the answers of the Cloudflare API have the form that this file reads.
#   - Not verified: a token with only the permission Zone, DNS, Edit on one zone passes
#     each of the three requests.

# shellcheck disable=SC2034,SC2154 # scripts/dns-records.sh reads and sets these names
[ "${dns_records_loaded:-}" = 1 ] || {
    echo "scripts/dns/cloudflare.sh: scripts/dns-records.sh loads this file; it does not run alone" >&2
    exit 2
}

provider_names='EC2_CLOUDFLARE_API_TOKEN EC2_CLOUDFLARE_ZONE_ID'
provider_tool=curl
cloudflare_api=https://api.cloudflare.com/client/v4

# The writer of the header file and of each request body, and the reader of each answer.
# The token comes on stdin or from the header file, never as an argument.
# shellcheck disable=SC2016 # the text is Python: a comment holds a name between two `
cloudflare_helper='
import json
import os
import re
import sys

START = "Authorization: Bearer "


def stop(text):
    sys.stderr.write("dns-records: " + text + "\n")
    sys.exit(1)


def write_file(path, text):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        handle.write(text)


def header(out):
    token = sys.stdin.read().split("\n")[0]
    # A blank or a control character in the token would start a second header.
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
        stop("EC2_CLOUDFLARE_API_TOKEN holds a character that an API token does not have")
    write_file(out, START + token + "\n")


def answer(path, header_path):
    """The answer of the Cloudflare API as a dict. An error message never shows the token."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        stop("the answer of the Cloudflare API is not JSON")
    if not isinstance(data, dict) or data.get("success") is not True:
        errors = data.get("errors") if isinstance(data, dict) else None
        text = "; ".join(str(e.get("message", "no message")) for e in errors
                         if isinstance(e, dict)) if isinstance(errors, list) else ""
        with open(header_path, encoding="utf-8") as handle:
            token = handle.read().strip()[len(START):]
        if token:
            text = text.replace(token, "<token>")
        stop("the Cloudflare API refuses the request: " + (text or "no message")[:300])
    return data


def state(path, header_path, name, address):
    # An answer with no list of records must not read as "no record": then `apply` makes a
    # second record for a name that has one.
    records = answer(path, header_path).get("result")
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        stop("the answer of the Cloudflare API holds no list of records")
    records = [r for r in records if r.get("type") == "A" and str(r.get("name", "")).lower() == name]
    if not records:
        print("create")
    elif len(records) > 1:
        stop("the name %s has %d A records; delete each record but one by hand, then run "
             "apply again" % (name, len(records)))
    elif records[0].get("content") == address and records[0].get("proxied") is False:
        print("same")
    else:
        # The id goes into the address of the next request.
        record = str(records[0].get("id", ""))
        if not re.fullmatch(r"[0-9a-f]{32}", record):
            stop("the answer of the Cloudflare API holds no record id for " + name)
        print("edit " + record)


def record(out, name, address, ttl):
    write_file(out, json.dumps({"type": "A", "name": name, "content": address,
                                "ttl": int(ttl), "proxied": False}))


mode, args = sys.argv[1], sys.argv[2:]
if mode == "header":
    header(args[0])
elif mode == "answer":
    answer(args[0], args[1])
elif mode == "state":
    state(args[0], args[1], args[2], args[3])
elif mode == "record":
    record(args[0], args[1], args[2], args[3])
else:
    stop("unknown helper mode")
'

cloudflare_helper() {
    python3 -I -c "$cloudflare_helper" "$@"
}

# cloudflare_call <argument>...: one request. The token is in the header file. It is not
# in the address and not in an argument. The last argument is the address.
cloudflare_call() {
    run curl -sS --max-time 30 -o "$cloudflare_answer" -H "@$cloudflare_auth" "$@" ||
        die "the Cloudflare API gives no answer"
}

# cloudflare_send <method> <address>: one request with the record file as its body.
cloudflare_send() {
    cloudflare_call -H 'Content-Type: application/json' -X "$1" --data "@$cloudflare_body" "$2"
}

provider_settings() {
    need EC2_CLOUDFLARE_API_TOKEN
    cloudflare_token=$value
    need EC2_CLOUDFLARE_ZONE_ID
    cloudflare_zone=$value
    # The id goes into the address of each request.
    [ "$value_missing" = 1 ] || case $cloudflare_zone in
        *[!0-9a-f]*) die "EC2_CLOUDFLARE_ZONE_ID is not the id of a zone: 32 hexadecimal digits in lower case" ;;
        ????????????????????????????????) ;;
        *) die "EC2_CLOUDFLARE_ZONE_ID is not the id of a zone: 32 hexadecimal digits in lower case" ;;
    esac
    cloudflare_records=$cloudflare_api/zones/$cloudflare_zone/dns_records
}

provider_begin() {
    if [ "$dry" = 1 ]; then
        cloudflare_auth='<temporary file, mode 0600: the header with the token>'
        cloudflare_body='<temporary file, mode 0600: the record>'
        cloudflare_answer='<temporary file: the answer>'
    else
        cloudflare_auth=$work/auth.header
        cloudflare_body=$work/record.json
        cloudflare_answer=$work/answer.json
        # The token goes to the helper on stdin, so no argument of a process holds it.
        cloudflare_helper header "$cloudflare_auth" <<EOT
$cloudflare_token
EOT
    fi
}

# provider_record <label> <complete name>
provider_record() {
    cloudflare_call "$cloudflare_records?type=A&name.exact=$2"
    if [ "$dry" = 1 ]; then
        cloudflare_send POST "$cloudflare_records"
        cloudflare_send PATCH "$cloudflare_records/<the id of the record>"
        return 0
    fi
    cloudflare_action=$(cloudflare_helper state "$cloudflare_answer" "$cloudflare_auth" "$2" "$address") ||
        die "apply stops at $2: no request changes this record"
    case $cloudflare_action in
        same)
            record_result "$2" 'no change'
            return 0
            ;;
        create)
            cloudflare_helper record "$cloudflare_body" "$2" "$address" "$ttl"
            cloudflare_send POST "$cloudflare_records"
            ;;
        'edit '*)
            cloudflare_helper record "$cloudflare_body" "$2" "$address" "$ttl"
            cloudflare_send PATCH "$cloudflare_records/${cloudflare_action#edit }"
            cloudflare_action=edit
            ;;
        *) die "the helper gives no action for $2" ;;
    esac
    cloudflare_helper answer "$cloudflare_answer" "$cloudflare_auth" || exit 1
    record_result "$2" "done ($cloudflare_action)"
}

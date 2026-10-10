#!/bin/sh
# The DNS provider porkbun of scripts/dns-records.sh: the records of the host through the
# Porkbun API. scripts/dns-records.sh loads this file with `.`; the file does not run
# alone. See docs/options/dns.md.
#
# Names of the environment file:
#   EC2_PORKBUN_API_KEY      the first value of the key pair
#   EC2_PORKBUN_SECRET_KEY   the second value of the same pair
# EC2_DOMAIN must have the form <label>.<domain>: one label, then the domain of the
# Porkbun account. The label is the first label of EC2_DOMAIN.
#
# The requests. Each one is a POST request with the key pair in its JSON body. The body is
# a file with the mode 0600 (curl --data @file). The key pair is in no address and in no
# argument.
#   dns/retrieveByNameType/<domain>/A/<name>.<label>   read each A record of one name
#   dns/create/<domain>                                 make one A record
#   dns/editByNameType/<domain>/A/<name>.<label>        set the address of each A record
#                                                       of one name
# A record is correct when each A record of its name holds the address. Then the name
# gets no second request.
#
# Not verified: no real call ran. The tests used a stub of curl.
#   - Not verified: the answers of the Porkbun API have the form that this file reads.

# shellcheck disable=SC2034,SC2154 # scripts/dns-records.sh reads and sets these names
[ "${dns_records_loaded:-}" = 1 ] || {
    echo "scripts/dns/porkbun.sh: scripts/dns-records.sh loads this file; it does not run alone" >&2
    exit 2
}

provider_names='EC2_PORKBUN_API_KEY EC2_PORKBUN_SECRET_KEY'
provider_tool=curl
porkbun_api=https://api.porkbun.com/api/json/v3

# The writer of each request body and the reader of each answer. The key pair comes on
# stdin or from a body file, never as an argument.
# shellcheck disable=SC2016 # the text is Python: a comment holds a name between two `
porkbun_helper='
import json
import os
import sys


def stop(text):
    sys.stderr.write("dns-records: " + text + "\n")
    sys.exit(1)


def write_file(path, text):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        handle.write(text)


def body(out, fields):
    lines = sys.stdin.read().split("\n")
    data = {"apikey": lines[0], "secretapikey": lines[1]}
    for field in fields:
        key, _, value = field.partition("=")
        data[key] = value
    write_file(out, json.dumps(data))


def answer(path, body_path):
    """The answer of the Porkbun API as a dict. An error message never shows a key."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        stop("the answer of the Porkbun API is not JSON")
    if not isinstance(data, dict) or data.get("status") != "SUCCESS":
        text = str(data.get("message", "no message")) if isinstance(data, dict) else "no message"
        with open(body_path, encoding="utf-8") as handle:
            keys = json.load(handle)
        for name in ("apikey", "secretapikey"):
            if keys.get(name):
                text = text.replace(keys[name], "<" + name + ">")
        stop("the Porkbun API refuses the request: " + text[:300])
    return data


def state(path, body_path, address):
    # An answer with no list of records must not read as "no record": then `apply` makes a
    # second record for a name that has one.
    records = answer(path, body_path).get("records")
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        stop("the answer of the Porkbun API holds no list of records")
    if not records:
        print("create")
    elif all(record.get("content") == address for record in records):
        print("same")
    else:
        print("edit")


mode, args = sys.argv[1], sys.argv[2:]
if mode == "body":
    body(args[0], args[1:])
elif mode == "answer":
    answer(args[0], args[1])
elif mode == "state":
    state(args[0], args[1], args[2])
else:
    stop("unknown helper mode")
'

porkbun_helper() {
    python3 -I -c "$porkbun_helper" "$@"
}

# porkbun_key_body <file> [<field>=<value>...]: write the body of one request. The key
# pair goes to the helper on stdin, so no argument of a process holds a key.
porkbun_key_body() {
    porkbun_helper body "$@" <<EOT
$porkbun_key
$porkbun_secret
EOT
}

# porkbun_post <API path> <body file> <answer file>: one POST request. The key pair is in
# the body file. It is not in the address and not in an argument.
porkbun_post() {
    run curl -sS --max-time 30 -o "$3" -H 'Content-Type: application/json' \
        --data "@$2" "$porkbun_api/$1" ||
        die "the Porkbun API gives no answer for $1"
}

provider_settings() {
    if [ "$domain_missing" = 1 ]; then
        porkbun_label='<label>'
        porkbun_zone='<home domain>'
    else
        case $domain in
            *.*.*) ;;
            *) die "the provider porkbun needs EC2_DOMAIN in the form <label>.<home domain>" ;;
        esac
        porkbun_label=${domain%%.*}
        porkbun_zone=${domain#*.}
    fi
    need EC2_PORKBUN_API_KEY
    porkbun_key=$value
    need EC2_PORKBUN_SECRET_KEY
    porkbun_secret=$value
}

provider_begin() {
    if [ "$dry" = 1 ]; then
        porkbun_auth='<temporary file, mode 0600: the key pair>'
        porkbun_body='<temporary file, mode 0600: the key pair and the record>'
        porkbun_answer='<temporary file: the answer>'
    else
        porkbun_auth=$work/auth.json
        porkbun_body=$work/record.json
        porkbun_answer=$work/answer.json
        porkbun_key_body "$porkbun_auth"
    fi
}

# provider_record <label> <complete name>
provider_record() {
    porkbun_sub=$1.$porkbun_label
    porkbun_post "dns/retrieveByNameType/$porkbun_zone/A/$porkbun_sub" "$porkbun_auth" "$porkbun_answer"
    if [ "$dry" = 1 ]; then
        porkbun_post "dns/create/$porkbun_zone" "$porkbun_body" "$porkbun_answer"
        porkbun_post "dns/editByNameType/$porkbun_zone/A/$porkbun_sub" "$porkbun_body" "$porkbun_answer"
        return 0
    fi
    porkbun_action=$(porkbun_helper state "$porkbun_answer" "$porkbun_auth" "$address") ||
        die "apply stops at $2: no request changes this record"
    case $porkbun_action in
        same)
            record_result "$2" 'no change'
            return 0
            ;;
        create)
            porkbun_key_body "$porkbun_body" "name=$porkbun_sub" type=A "content=$address" "ttl=$ttl"
            porkbun_post "dns/create/$porkbun_zone" "$porkbun_body" "$porkbun_answer"
            ;;
        edit)
            porkbun_key_body "$porkbun_body" "content=$address" "ttl=$ttl"
            porkbun_post "dns/editByNameType/$porkbun_zone/A/$porkbun_sub" "$porkbun_body" "$porkbun_answer"
            ;;
        *) die "the helper gives no action for $2" ;;
    esac
    porkbun_helper answer "$porkbun_answer" "$porkbun_body" || exit 1
    record_result "$2" "done ($porkbun_action)"
}

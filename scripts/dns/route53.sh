#!/bin/sh
# The DNS provider route53 of scripts/dns-records.sh: the records of the host in one
# hosted zone of Amazon Route 53, through the AWS CLI. scripts/dns-records.sh loads this
# file with `.`; the file does not run alone. See docs/options/dns.md.
#
# Names of the environment file:
#   EC2_ROUTE53_PROFILE          the AWS CLI profile of the installer with the Route 53
#                                rights. It is not the provisioner profile: the
#                                provisioner has no Route 53 right.
#   EC2_ROUTE53_HOSTED_ZONE_ID   the id of the hosted zone that holds EC2_DOMAIN
# This file holds no credential and reads none: the AWS CLI reads the credential of the
# profile. The two names are not secrets.
#
# The calls:
#   route53 list-resource-record-sets    read the A record set of one name
#   route53 change-resource-record-sets  make or correct it, with the action UPSERT. The
#                                        change is a file (file://).
# A record is correct when its name has one A record set with the address as its one
# value, with no alias and no routing policy. Then the name gets no second call.
# The script does not wait for the end of a change: that needs the right route53:GetChange.
#
# Not verified: no real call ran. The tests used a stub of the AWS CLI.
#   - Not verified: the answers of Route 53 have the form that this file reads.
#   - Not verified: the AWS CLI needs no region for a Route 53 call.

# shellcheck disable=SC2034,SC2154 # scripts/dns-records.sh reads and sets these names
[ "${dns_records_loaded:-}" = 1 ] || {
    echo "scripts/dns/route53.sh: scripts/dns-records.sh loads this file; it does not run alone" >&2
    exit 2
}

provider_names='EC2_ROUTE53_PROFILE EC2_ROUTE53_HOSTED_ZONE_ID'
provider_tool=aws

# The reader of each answer and the writer of the change file. They hold no credential.
route53_helper='
import json
import os
import sys


def stop(text):
    sys.stderr.write("dns-records: " + text + "\n")
    sys.exit(1)


def state(path, name, address):
    # The call asks for the first record set at the name or after it. So an answer with
    # another name or another type means that the name has no A record set.
    try:
        with open(path, encoding="utf-8") as handle:
            sets = json.load(handle).get("ResourceRecordSets")
    except (OSError, ValueError, AttributeError):
        stop("the answer of Route 53 is not a JSON object")
    if not isinstance(sets, list) or not all(isinstance(item, dict) for item in sets):
        stop("the answer of Route 53 holds no list of record sets")
    found = [item for item in sets
             if str(item.get("Name", "")).lower() == name + "." and item.get("Type") == "A"]
    if not found:
        print("create")
        return
    records = found[0].get("ResourceRecords")
    values = [r.get("Value") for r in records] if isinstance(records, list) else []
    plain = "AliasTarget" not in found[0] and "SetIdentifier" not in found[0]
    print("same" if plain and values == [address] else "edit")


def change(out, name, address, ttl):
    batch = {"Comment": "The A record of the host. scripts/dns-records.sh writes it.",
             "Changes": [{"Action": "UPSERT", "ResourceRecordSet": {
                 "Name": name + ".", "Type": "A", "TTL": int(ttl),
                 "ResourceRecords": [{"Value": address}]}}]}
    with os.fdopen(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        handle.write(json.dumps(batch))


mode, args = sys.argv[1], sys.argv[2:]
if mode == "state":
    state(args[0], args[1], args[2])
elif mode == "change":
    change(args[0], args[1], args[2], args[3])
else:
    stop("unknown helper mode")
'

route53_helper() {
    python3 -I -c "$route53_helper" "$@"
}

# route53_cli <argument>...: one Route 53 call with the profile of the installer.
route53_cli() {
    run aws --profile "$route53_profile" route53 "$@"
}

provider_settings() {
    need EC2_ROUTE53_PROFILE
    route53_profile=$value
    # The name is an argument of each call: it must not start as an option.
    [ "$value_missing" = 1 ] || case $route53_profile in
        [!A-Za-z0-9]* | *[!A-Za-z0-9_.@+=,-]*)
            die "EC2_ROUTE53_PROFILE is not the name of an AWS CLI profile"
            ;;
    esac
    need EC2_ROUTE53_HOSTED_ZONE_ID
    # The console and the API also show the id in the form /hostedzone/<id>.
    route53_zone=${value#/hostedzone/}
    [ "$value_missing" = 1 ] || case $route53_zone in
        '' | *[!A-Z0-9]*) die "EC2_ROUTE53_HOSTED_ZONE_ID is not the id of a hosted zone" ;;
    esac
}

provider_begin() {
    if [ "$dry" = 1 ]; then
        route53_answer='<temporary file: the answer>'
        route53_change='<temporary file, mode 0600: the change>'
    else
        route53_answer=$work/answer.json
        route53_change=$work/change.json
    fi
}

# route53_read <complete name>: read the first record set at the name.
route53_read() {
    route53_cli list-resource-record-sets --hosted-zone-id "$route53_zone" \
        --start-record-name "$1." --start-record-type A --max-items 1 --output json
}

# route53_write: send the change file.
route53_write() {
    route53_cli change-resource-record-sets --hosted-zone-id "$route53_zone" \
        --change-batch "file://$route53_change" --query ChangeInfo.Status --output text
}

# provider_record <label> <complete name>
provider_record() {
    if [ "$dry" = 1 ]; then
        route53_read "$2"
        route53_write
        return 0
    fi
    route53_read "$2" >"$route53_answer" ||
        die "apply stops at $2: Route 53 gives no answer for the read of the record"
    route53_action=$(route53_helper state "$route53_answer" "$2" "$address") ||
        die "apply stops at $2: no call changes this record"
    case $route53_action in
        same)
            record_result "$2" 'no change'
            return 0
            ;;
        create | edit) ;;
        *) die "the helper gives no action for $2" ;;
    esac
    route53_helper change "$route53_change" "$2" "$address" "$ttl"
    route53_write >/dev/null || die "apply stops at $2: Route 53 refuses the change"
    record_result "$2" "done ($route53_action)"
}

#!/bin/sh
# The DNS provider manual of scripts/dns-records.sh: the installer sets each record by
# hand. This provider makes no call and reads no credential. It is the default.
# scripts/dns-records.sh loads this file with `.`; the file does not run alone.
# See docs/options/dns.md.
#
# Names of the environment file: none.
#
# `apply` prints each record of the record set in the form of a zone file:
#   <name>.<EC2_DOMAIN>. 600 IN A <address>
# The installer makes each record at the DNS provider of the domain. A second run prints
# the same lines and changes nothing.

# shellcheck disable=SC2034 # scripts/dns-records.sh reads the names that this file sets
[ "${dns_records_loaded:-}" = 1 ] || {
    echo "scripts/dns/manual.sh: scripts/dns-records.sh loads this file; it does not run alone" >&2
    exit 2
}

provider_names=
provider_tool=

provider_settings() {
    :
}

provider_begin() {
    echo "dns: the provider is manual. This script makes no call. Set each record below by hand at the DNS provider of the domain:"
}

# provider_record <label> <complete name>
provider_record() {
    record_line "$2"
}

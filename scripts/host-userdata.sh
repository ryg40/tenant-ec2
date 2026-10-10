#!/bin/sh
# Prepare a new host of tenant-ec2 for the bootstrap. The user data of the template
# scripts/aws-host.cfn.yaml starts this script one time, at the first boot, as root, from
# the clone of the portable snapshot. A second run changes nothing that is done.
#
# Usage: scripts/host-userdata.sh [<stack values file>]
#
# The steps, in this order:
#   0. docker gate   install startup dependencies and the log driver before packages
#   1. packages      git, Docker, the EFS utilities and the AWS CLI version 2 of Amazon Linux
#                    2023; gettext, diffutils, tar, Python 3 and its module yaml for the
#                    bootstrap
#   2. compose       the Compose plugin from a pinned Docker release, checked by its SHA-256
#   3. swap          one swap file of 8 GiB on the root volume
#   4. data volume   wait for the attachment; make an ext4 file system only when `blkid` and
#                    `wipefs -n` find no signature; mount by UUID with nofail at /srv/data
#   5. EFS           mount the three access points at /srv/efs/<name> with TLS and IAM
#   6. bootstrap     start scripts/host-bootstrap.sh with the stack values file, when the
#                    snapshot has it
#
# Input: the stack values file that the user data writes. The default is
# /etc/tenant-ec2/stack.env. It holds no secret. This script reads these names:
#   EC2_DATA_VOLUME_ID          the id of the data volume
#   EC2_EFS_ID                  the id of the file system
#   EC2_EFS_AP_OPEN_KNOWLEDGE   the id of the access point open-knowledge
#   EC2_EFS_AP_OPENVIKING       the id of the access point openviking
#   EC2_EFS_AP_SHARED           the id of the access point shared
#
# The script finds the data volume by its id: on a Nitro instance, the serial number of the
# device is the id without the hyphen. It does not use a device name.
# Warning: the script makes a file system only when `blkid` and `wipefs -n` find no
# signature on the volume. `wipefs -n` reads the volume and changes nothing. The script
# never formats a volume that holds a file system, a partition table or another signature.

set -eu

# The Compose plugin: the release and the SHA-256 of its file docker-compose-linux-x86_64.
# To renew the pin, take the two values from the release page of Docker Compose.
COMPOSE_VERSION=v5.5.0
COMPOSE_SHA256=c57ab918abd5b05ca7e7d0f275875dd1330a695074f309dc9eab1b49efafcd4b
COMPOSE_URL=https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-x86_64
COMPOSE_DIR=/usr/local/lib/docker/cli-plugins
SWAP_FILE=/swapfile
SWAP_MIB=8192
DATA_DIR=/srv/data
EFS_DIR=/srv/efs

say() {
    echo "host-userdata: $*"
}

die() {
    echo "host-userdata: $*" >&2
    exit 1
}

case ${1:-} in
    -h|--help) sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; exit 0 ;;
esac

stack_env=${1:-/etc/tenant-ec2/stack.env}
repo=$(cd "$(dirname "$0")/.." && pwd)

[ "$(id -u)" -eq 0 ] || die "run this script as root"
[ -r "$stack_env" ] || die "the stack values file $stack_env is not there"
# shellcheck source=/dev/null
. "$stack_env"
for name in EC2_DATA_VOLUME_ID EC2_EFS_ID EC2_EFS_AP_OPEN_KNOWLEDGE EC2_EFS_AP_OPENVIKING EC2_EFS_AP_SHARED; do
    eval "value=\${$name:-}"
    [ -n "$value" ] || die "$stack_env does not set $name"
done

# Add one line to /etc/fstab when no line has this mount point.
fstab_line() {
    awk -v m="$1" '$1 !~ /^#/ && $2 == m { found = 1 } END { exit !found }' /etc/fstab ||
        printf '%s\n' "$2" >>/etc/fstab
}

packages() {
    say "install the packages"
    # The bootstrap needs the last five: envsubst is in gettext, cmp is in diffutils, a
    # kit build uses tar, and the apply of the blueprint reads YAML.
    dnf install -y git docker amazon-efs-utils awscli-2 e2fsprogs util-linux \
        gettext diffutils tar python3 python3-pyyaml
}

docker_engine() {
    install -d -m 0755 /etc/docker
    # The journal then holds the log of each container, with the name of the container
    # as its tag. The bootstrap sets the same two values on a host that has another file.
    [ -f /etc/docker/daemon.json ] ||
        printf '{\n  "log-driver": "journald",\n  "log-opts": {\n    "tag": "{{.Name}}"\n  }\n}\n' >/etc/docker/daemon.json
    # nofail lets the host boot for repair, not Docker start on an underlying directory.
    # Install before the RPM: socket activation must also fail closed at the first boot.
    install -d -m 0755 /etc/systemd/system/docker.service.d
    cat >/etc/systemd/system/docker.service.d/tenant-ec2-storage.conf <<'EOT'
[Unit]
RequiresMountsFor=/srv/data /srv/efs/open-knowledge /srv/efs/openviking /srv/efs/shared
BindsTo=srv-data.mount srv-efs-open\x2dknowledge.mount srv-efs-openviking.mount srv-efs-shared.mount
Requires=tenant-ec2-metadata-guard.service
After=srv-data.mount srv-efs-open\x2dknowledge.mount srv-efs-openviking.mount srv-efs-shared.mount tenant-ec2-metadata-guard.service

[Service]
ExecStartPre=/usr/bin/mountpoint -q /srv/data
ExecStartPre=/usr/bin/mountpoint -q /srv/efs/open-knowledge
ExecStartPre=/usr/bin/mountpoint -q /srv/efs/openviking
ExecStartPre=/usr/bin/mountpoint -q /srv/efs/shared
EOT
    chmod 0644 /etc/systemd/system/docker.service.d/tenant-ec2-storage.conf
}

compose_sum_ok() {
    printf '%s  %s\n' "$COMPOSE_SHA256" "$1" | sha256sum -c --status
}

compose_plugin() {
    if [ -f "$COMPOSE_DIR/docker-compose" ] && compose_sum_ok "$COMPOSE_DIR/docker-compose"; then
        return 0
    fi
    say "install the Compose plugin $COMPOSE_VERSION"
    install -d -m 0755 "$COMPOSE_DIR"
    tmp=$(mktemp "$COMPOSE_DIR/.docker-compose.XXXXXX")
    curl -fsSL --retry 5 --retry-delay 5 -o "$tmp" "$COMPOSE_URL" ||
        { rm -f "$tmp"; die "the download of the Compose plugin $COMPOSE_VERSION failed"; }
    compose_sum_ok "$tmp" ||
        { rm -f "$tmp"; die "the Compose plugin $COMPOSE_VERSION has a wrong SHA-256"; }
    chmod 0755 "$tmp"
    mv "$tmp" "$COMPOSE_DIR/docker-compose"
    docker compose version
}

swap_file() {
    if ! swapon --show=NAME --noheadings | grep -qx "$SWAP_FILE"; then
        if [ ! -f "$SWAP_FILE" ]; then
            say "make the swap file of $SWAP_MIB MiB"
            rm -f "$SWAP_FILE.new"
            (umask 077 && dd if=/dev/zero of="$SWAP_FILE.new" bs=1M count="$SWAP_MIB" status=none)
            mkswap "$SWAP_FILE.new" >/dev/null
            mv "$SWAP_FILE.new" "$SWAP_FILE"
        fi
        swapon "$SWAP_FILE"
    fi
    grep -q "^${SWAP_FILE}[[:space:]]" /etc/fstab || printf '%s none swap sw 0 0\n' "$SWAP_FILE" >>/etc/fstab
}

data_volume() {
    serial=vol${EC2_DATA_VOLUME_ID#vol-}
    n=0
    # The attachment comes after the start of the instance. Wait for the device.
    while :; do
        dev=$(lsblk -dn -o NAME,SERIAL | awk -v s="$serial" '$2 == s { print "/dev/" $1 }')
        [ -z "$dev" ] || break
        n=$((n + 1))
        [ "$n" -le 120 ] || die "the data volume $EC2_DATA_VOLUME_ID is not attached after 600 s"
        sleep 5
    done
    # blkid exits with 2 when the device has no signature. Then wipefs -n lists each
    # signature that it finds, and it changes nothing. Only a volume with no signature in
    # the two tests is empty. A volume with a signature is never formatted.
    rc=0
    blkid -p "$dev" >/dev/null 2>&1 || rc=$?
    case $rc in
        0) ;;
        2) signatures=$(wipefs -n "$dev") || die "wipefs cannot read the data volume $dev: no format"
           [ -z "$signatures" ] || die "wipefs finds a signature on the data volume $dev: no format"
           say "the data volume $dev is empty: make an ext4 file system"
           mkfs.ext4 -q "$dev" ;;
        *) die "blkid cannot read the data volume $dev (exit $rc)" ;;
    esac
    fstype=$(blkid -p -o value -s TYPE "$dev" || true)
    [ "$fstype" = ext4 ] || die "the data volume $dev holds '${fstype:-no file system}', not ext4: no mount"
    uuid=$(blkid -p -o value -s UUID "$dev")
    install -d -m 0755 "$DATA_DIR"
    fstab_line "$DATA_DIR" "UUID=$uuid $DATA_DIR ext4 defaults,nofail 0 2"
    systemctl daemon-reload
    mountpoint -q "$DATA_DIR" || mount "$DATA_DIR"
    say "the data volume $dev is at $DATA_DIR"
}

# Mount one access point: <name> <access point id>.
efs_mount() {
    dir=$EFS_DIR/$1
    install -d -m 0755 "$dir"
    fstab_line "$dir" "$EC2_EFS_ID:/ $dir efs _netdev,nofail,tls,iam,accesspoint=$2 0 0"
    systemctl daemon-reload
    n=0
    # The DNS name of a new mount target can need some minutes.
    until mountpoint -q "$dir" || mount "$dir"; do
        n=$((n + 1))
        [ "$n" -le 30 ] || die "the access point $1 does not mount after 300 s"
        sleep 10
    done
    say "the access point $1 is at $dir"
}

docker_engine
packages
systemctl daemon-reload
compose_plugin
swap_file
data_volume
efs_mount open-knowledge "$EC2_EFS_AP_OPEN_KNOWLEDGE"
efs_mount openviking "$EC2_EFS_AP_OPENVIKING"
efs_mount shared "$EC2_EFS_AP_SHARED"

if [ -f "$repo/scripts/host-bootstrap.sh" ]; then
    say "start scripts/host-bootstrap.sh"
    exec sh "$repo/scripts/host-bootstrap.sh" --stack-env "$stack_env"
fi
say "scripts/host-bootstrap.sh is not in this snapshot: the host is prepared, and the Compose stack is not started"

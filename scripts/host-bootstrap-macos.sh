#!/bin/zsh
# Mac bootstrap. Native execution requires root on macOS and validated inputs.
# --machine: stack-written, root-owned JSON, mode 0644; no service secrets.
# --env-file: rendered service environment or JSON. Default '-' reads stdin.
# --root: isolated filesystem fixture; accepted only with --dry-run/--check-forms.
# --lockdown: the operator approval of the account lockdown after the PIN login check.
# No input file is sourced as shell code. No personal AWS profile is created.
if [[ -o xtrace || -o verbose ]]; then
    unsetopt xtrace verbose
    print -u2 -- 'macos bootstrap: tracing refused'
    exit 1
fi
emulate -R zsh
setopt errexit nounset pipefail
umask 077
ulimit -c 0
repo=${0:A:h:h}

main() {
    local dry=0 forms=0 lockdown=0 root=/ env_file=- machine='' option
    while (( $# )); do
        option=$1
        shift
        case "$option" in
            --dry-run) dry=1 ;;
            --check-forms) forms=1 ;;
            --lockdown) lockdown=1 ;;
            --root|--env-file|--machine)
                (( $# )) || { print -u2 -- 'macos bootstrap: missing argument'; return 2; }
                case "$option" in
                    --root) root=$1 ;;
                    --env-file) env_file=$1 ;;
                    --machine) machine=$1 ;;
                esac
                shift ;;
            *) print -u2 -- 'macos bootstrap: unknown argument'; return 2 ;;
        esac
    done
    [[ "$root" == /* && "$root" != *'/../'* ]] || return 2
    [[ "$root" == / || "$dry$forms" != 00 ]] || {
        print -u2 -- 'macos bootstrap: fixture mutation refused'; return 1
    }
    [[ -n "$machine" ]] || machine=${root%/}/etc/tenant-ec2/machine.json
    local -a mode
    mode=()
    (( forms )) && mode+=(--check-forms)
    (( dry )) && mode+=(--dry-run)
    (( lockdown )) && mode+=(--lockdown)
    # Preserve stdin for private service data; no value enters a process argument.
    python3 -I -B "$repo/config/macos/bootstrap-runtime.py" --root "$root" \
        --machine "$machine" --env-file "$env_file" "${mode[@]}"
}
main "$@"

#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: run_figure8.sh --backend SHM|TCP --policy POLICY

Run one GROMACS PEPSIN cell for OCEAN Figure 8. The surrounding
reproduce_figures_6_9.py collector owns the complete sweep and output files.

Required environment:
  FIG8_SHM_LAUNCHER   Adapter executable for a real SHM backend
  FIG8_TCP_LAUNCHER   Adapter executable for a real TCP backend

Optional environment:
  FIG8_CXLMEMSIM      Policy-capable cxlmemsim_legacy executable
  FIG8_GMX_MPI        MPI-enabled GROMACS executable
  FIG8_TPR            PEPSIN benchMEM.tpr input
  FIG8_STEPS          MD steps (default: 10000)
  FIG8_NTOMP          OpenMP threads per rank (default: 1)
  FIG8_CPUSET         CXLMemSim CPU list (default: 0)
  FIG8_PEBS_PERIOD    PEBS sample period (default: 1000)

Each backend adapter is invoked as:
  <launcher> -- <cxlmemsim_legacy argv...>
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 2
}

require_executable() {
    local name="$1"
    local path="$2"
    [[ -n "$path" && -x "$path" ]] || die "$name must name an executable file: ${path:-<unset>}"
}

require_positive_integer() {
    local name="$1"
    local value="$2"
    [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "$name must be a positive integer: $value"
}

require_no_whitespace() {
    local name="$1"
    local value="$2"
    [[ ! "$value" =~ [[:space:]] ]] || die "$name cannot contain whitespace because cxlmemsim_legacy tokenizes -t: $value"
}

backend=""
policy=""

while (($# > 0)); do
    case "$1" in
        --backend)
            (($# >= 2)) || die "--backend requires a value"
            [[ -z "$backend" ]] || die "--backend may be specified only once"
            backend="$2"
            shift 2
            ;;
        --policy)
            (($# >= 2)) || die "--policy requires a value"
            [[ -z "$policy" ]] || die "--policy may be specified only once"
            policy="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

[[ -n "$backend" ]] || die "--backend is required"
[[ -n "$policy" ]] || die "--policy is required"

shm_launcher="${FIG8_SHM_LAUNCHER:-}"
tcp_launcher="${FIG8_TCP_LAUNCHER:-}"
require_executable "FIG8_SHM_LAUNCHER" "$shm_launcher"
require_executable "FIG8_TCP_LAUNCHER" "$tcp_launcher"
[[ ! "$shm_launcher" -ef "$tcp_launcher" ]] || die "FIG8_SHM_LAUNCHER and FIG8_TCP_LAUNCHER must be distinct executables"

case "$backend" in
    SHM)
        launcher="$shm_launcher"
        ;;
    TCP)
        launcher="$tcp_launcher"
        ;;
    *)
        die "unsupported Figure 8 backend: $backend"
        ;;
esac

case "$policy" in
    Baseline)       policy_tuple="none,none,none,none" ;;
    Interleave)     policy_tuple="interleave,none,none,none" ;;
    NUMA)           policy_tuple="numa,none,none,none" ;;
    Frequency)      policy_tuple="none,frequency,none,none" ;;
    PageTableAware) policy_tuple="none,none,pagetableaware,none" ;;
    FIFO)           policy_tuple="none,none,none,fifo" ;;
    HeatAware)      policy_tuple="none,heataware,none,none" ;;
    Hybrid)         policy_tuple="none,hybrid,none,none" ;;
    Locality)       policy_tuple="none,locality,none,none" ;;
    CacheFrequency) policy_tuple="none,none,none,frequency" ;;
    HugePage)       policy_tuple="none,none,hugepage,none" ;;
    Lifetime)       policy_tuple="none,lifetime,none,none" ;;
    LoadBalance)    policy_tuple="none,loadbalance,none,none" ;;
    *)
        die "unsupported Figure 8 policy: $policy"
        ;;
esac

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cxlmemsim="${FIG8_CXLMEMSIM:-${script_dir}/../../build/cxlmemsim_legacy}"
gmx_mpi="${FIG8_GMX_MPI:-${script_dir}/gmx_mpi}"
tpr="${FIG8_TPR:-${script_dir}/benchMEM.tpr}"
steps="${FIG8_STEPS:-10000}"
ntomp="${FIG8_NTOMP:-1}"
cpuset="${FIG8_CPUSET:-0}"
pebs_period="${FIG8_PEBS_PERIOD:-1000}"
target_home="${FIG8_TARGET_HOME:-${HOME:-/root}}"
target_path="${FIG8_TARGET_PATH:-${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}}"

require_executable "FIG8_CXLMEMSIM" "$cxlmemsim"
require_executable "FIG8_GMX_MPI" "$gmx_mpi"
[[ -r "$tpr" ]] || die "FIG8_TPR must name a readable file: $tpr"
require_executable "env" "/usr/bin/env"

require_positive_integer "FIG8_STEPS" "$steps"
require_positive_integer "FIG8_NTOMP" "$ntomp"
require_positive_integer "FIG8_PEBS_PERIOD" "$pebs_period"
[[ "$cpuset" =~ ^[0-9]+([,-][0-9]+)*$ ]] || die "FIG8_CPUSET must be a CPU list such as 0 or 0,2-3: $cpuset"

require_no_whitespace "FIG8_CXLMEMSIM" "$cxlmemsim"
require_no_whitespace "FIG8_GMX_MPI" "$gmx_mpi"
require_no_whitespace "FIG8_TPR" "$tpr"
require_no_whitespace "FIG8_TARGET_HOME" "$target_home"
require_no_whitespace "FIG8_TARGET_PATH" "$target_path"

target_argv=(
    /usr/bin/env
    "OMP_NUM_THREADS=$ntomp"
    "HOME=$target_home"
    "PATH=$target_path"
    "$gmx_mpi"
    mdrun
    -s "$tpr"
    -nsteps "$steps"
    -resethway
    -ntomp "$ntomp"
    -noconfout
    -noappend
)
printf -v target_command '%s ' "${target_argv[@]}"
target_command="${target_command% }"

command=(
    "$cxlmemsim"
    -c "$cpuset"
    -p "$pebs_period"
    -k "$policy_tuple"
    -t "$target_command"
)

printf 'FIG8_BACKEND=%s\n' "$backend"
printf 'FIG8_POLICY=%s\n' "$policy"
printf 'FIG8_POLICY_TUPLE=%s\n' "$policy_tuple"
printf 'FIG8_BACKEND_LAUNCHER=%s\n' "$launcher"

"$launcher" -- "${command[@]}"

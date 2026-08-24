
[![GitHub Repo stars](https://img.shields.io/github/stars/cxl-emu/OCEAN?style=flat&color=red)](https://github.com/cxl-emu/OCEAN/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/cxl-emu/OCEAN?style=flat&color=yellow)
](https://github.com/cxl-emu/OCEAN/forks)
[![GitHub contributors](https://img.shields.io/github/contributors-anon/cxl-emu/OCEAN?style=flat&color=brightgreen)](https://github.com/cxl-emu/OCEAN/graphs/contributors)
[![GitHub watchers](https://img.shields.io/github/watchers/cxl-emu/OCEAN?style=flat&color=blue)](https://github.com/cxl-emu/OCEAN/watchers)

[![issues](https://img.shields.io/github/issues/cxl-emu/OCEAN?label=issues&color=yellow)](https://github.com/cxl-emu/OCEAN/issues)
[![pull requests](https://img.shields.io/github/issues-pr/cxl-emu/OCEAN?label=pull%20requests&color=brightgreen)](https://github.com/cxl-emu/OCEAN/pulls)
[![Commits](https://img.shields.io/github/commit-activity/t/cxl-emu/OCEAN)](https://github.com/cxl-emu/OCEAN/commits/main/)

![GitHub repo size](https://img.shields.io/github/repo-size/cxl-emu/OCEAN?style=flat&color=red)
![GitHub commit activity](https://img.shields.io/github/commit-activity/m/cxl-emu/OCEAN?style=flat&color=yellow)

![GitHub active maintenance](https://img.shields.io/badge/maintenance-active-brightgreen.svg)
![GitHub last commit](https://img.shields.io/github/last-commit/cxl-emu/OCEAN?style=flat&color=blue)

# OCEAN
OCEAN – <ins>O</ins>pen-source <ins>C</ins>XL <ins>E</ins>mulation at Hyperscale <ins>A</ins>rchitecture and <ins>N</ins>etworking

Compute Express Link (CXL) 3.0 introduces powerful memory pooling and promises to transform datacenter architectures. However, the lack of available CXL 3.0 hardware and the complexity of multi-host configurations pose significant challenges to the community. OCEAN is a comprehensive emulation framework that enables full CXL 3.0 functionality, including multi-host memory sharing and pooling support. OCEAN provides emulation of CXL 3.0 features—such as fabric management, dynamic memory allocation, and coherent memory sharing across multiple hosts—in advance of real hardware availability. An evaluation of OCEAN shows that it achieves performance within about 3x of projected native CXL 3.0 speeds having complete compatibility with existing CXL software stacks. We demonstrate the utility of OCEAN through a case study on Genomics Pipeline, distributed database, LLM workloads, observing up to a 15% improvement in application performance compared to traditional RDMA-based approaches.


# Setup

## Dependencies & Compiliation

In order to run the distributed server (`cxlmemsim_server`) that VMs use to communicate with a shared-memory CXL pool, you need to install prerequisites, configure your host network for multiple VMs, and compile the `cxlmemsim_server`:

```bash
git clone --recurse-submodules https://github.com/cxl-emu/OCEAN.git
cd OCEAN
bash ./script/setup_host.sh
# Assuming 2 hosts simulation. Change this based on the number of hosts you want to simulate. Skip this if you are using multiple physical machines:
bash ./script/setup_network.sh 2

# If you are using multiple physical machines, Run this instead:
bash ./script/setup_optional_cross_machine_network.sh <num_vms> <br_ip_suffix>
# <num_vms>: number of VMs to create on this host
# <br_ip_suffix>: unique host identifier (1-254), used for bridge IP 192.168.100.<br_ip_suffix>
# Example:
# create 1 VM on host 1
bash ./script/setup_optional_cross_machine_network.sh 1 1
# create 1 VM on host 2
bash ./script/setup_optional_cross_machine_network.sh 1 2

mkdir build
cd build
cmake .. -DSERVER_MODE=ON -DCMAKE_CXX_COMPILER=g++-13
make -j$(nproc)
```

## Disk & Kernel Image Setup

In order to actually run any VMs, you need to either obtain the kernel and disk images already formatted, or create them yourself with the setup in `artifact/ocean-qemu-image`. Note that you will need KVM/sudo permissions to build your own images.

### Preconfigured Images

```bash
wget https://asplos.dev/about/bzImage
gdown 1ga5CN3_H1qfReer99w_QcVOYb6R21JHI
cp qemu.img qemu1.img
```

### Rebuilding Images
```bash
cd artifact/ocean-qemu-image
sudo ./build.sh
```

# Running OCEAN

If you want to run OCEAN on your own, you will need to do the following:

```bash
cd build && ./cxlmemsim_server --capacity=1024 ...
```

Then, you should launch the obtained `qemu.img` VM using
```bash
sudo ../qemu_integration/launch_qemu_cxl.sh # login as root with password: victor129
```

This assumes that the disk and kernel images exist in your working directory. Note that for more than 1 VM you will need to do the following:
```bash
cp qemu.img qemu1.img
sudo ../qemu_integration/launch_qemu_cxl1.sh # assuming you are in `OCEAN/build/`
# in qemu; post login
vi /usr/local/bin/*.sh
# change 192.168.100.10 to 1x, where x is the nth VM
# for qemu1.img, should be 192.168.100.11
vi /etc/hostname # change node0 to node{n}, node1 for qemu.img
```

To get CXL functionality within your VM, the disk images should have a service which provisions the CXL device. To validate it is there you should check that `/dev/dax0.0` exists (i.e., `ls /dev/dax0.0`). If it does not exist, check `ps aux | grep dax` to see if the VM is still provisioning.

## Running Workloads

There are several scripts in `ocean-test-scripts` which will allow you select workloads (e.g., STREAM, OSU Allgather, and lat_mem_rd from LMBench). They are mainly for testing, but can be used to run VMs. You can configure the sizing/CXL capacity and other variables.

**Note: for any workloads you have, we *highly* recommend that you compile them without any AVX/SSE instructions. This is because the KVM MMIO backend used to intercept memory requests does not support vectorized instructions.**

You should look at this repo's `ocean-test-scripts/run_lat_mem_rd.py`. You will need to (1) SCP the appropriate binary and (2) SCP `cxlalloc-preload` from `workloads/cxlalloc`. This is done as follows

```bash
cd workloads/cxlalloc
cargo build -p cxlalloc-preload --release # compile the header
scp target/release/libcxlalloc_preload.so root@192.168.100.10:/root
```

Then, you should run the workload as follows, assuming you are in the VM's `root/` directory:
```bash
CXLALLOC_BACKEND=dax LD_PRELOAD=./libcxlalloc_preload.so
```

The device should be configured to use `/dev/dax0.0` by default already. You might need to configure the size available for DAX using the `CXLALLOC_HEAP_SIZE` environment variable.


### Multi-VM/MPI Workloads

For multi-VM workloads, you will need to do the following:
1. Change the hostfile in `node0` (i.e., host 1) to reflect the number of hosts. In your VM, if you have 2 hosts (say: node0 and node1) your hostfile should look like:
```bash
node0 slots=1
node1 slots=1
```
2. Use the correct SHIM header
Basically, in order for OCEAN to work with multi-host workloads, it needs to route memory requests through `/dev/dax0.0` (i.e., CXL) and the dynamic library, `libmpi_cxl_shim.so` does exactly that. If you obtained the disk images via download, this should already be installed. Otherwise you should SCP into all of your hosts by compiling this repo's copy from `OCEAN/workloads/gromacs` as follows:

```bash
cd workloads/gromacs
./build.sh
scp libmpi_cxl_shim.so  root@192.168.100.10:/root
scp libmpi_cxl_shim.so  root@192.168.100.11:/root
```

3. Run the workload using `mpirun`

You now should run the workload on your first host (e.g., node0 in this case):
```bash
mpirun --allow-run-as-root -x CXL_SHIM_TRACE=1 -x CXL_DAX_PATH=/dev/dax0.0 -x LD_PRELOAD=$PWD/libmpi_cxl_shim.so --hostfile ./hostfile ./gromacs-2025.3/build/bin/gmx_mpi mdrun -s benchMEM.tpr -nsteps 10000 -resethway -ntomp 1
```

The files needed to run GROMACS should already be included in preinstalled VMs, but you may need to check VMs built by yourself.


#### Example 2: Running OSU Allgather
```bash
# Inside node0:
export CXL_DAX_PATH="/dev/dax0.0"
export CXL_DAX_RESET=1  # Reset allocation counter on first process
export CXL_SHIM_VERBOSE=1
LD_PRELOAD=/root/libmpi_cxl_shim.so mpirun --allow-run-as-root -np 2 -hostfile hostfile -x CXL_DAX_PATH -x CXL_DAX_RESET -x CXL_SHIM_VERBOSE -x LD_PRELOAD ~/osu-micro-benchmarks/mpi/collective/osu_allgather
```

# Troubleshooting
## Disk/Kernel Image Issues
If the QEMU image and the modified linux kernel download links do not work, they can be also found in Google Drive: https://drive.google.com/drive/folders/15r2wxoU_WFa06n1nBVlafg-69PimOH04?usp=sharing

## I've booted the VM, yet I don't see /dev/dax0.0. Why?
This could be an issue with the server; however, generally speaking it takes a while to provision the desired CXL memory space for the device (e.g., 1024MB takes approx 5 - 6m). We recommend using `ps aux | grep dax` to check ndctl or daxctl have completed the provisioning and suggest not using `daxctl list -H` to confirm the device until those processes are gone, as calling eihter during provisioning may result in a race condition.

## I can't SSH or SCP into the machines for some reason, why?
You should ensure that `PermitRootLogin` and `PasswordAuthentication` are enabled for these VMs. You might need to configure the SSH not to use public key authtication as well.


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

## How to Set up

Particularly, to set up on a node in Blue Ocean, please run
```bash
bash script/setup_host_rocky_blue_ocean.sh
```

For other hosts that use Ubuntu, please run
```bash
bash script/setup_host_ubuntu.sh
```

This should build the server and qemu and download the VM images. 

The remaining things are setup the network (`./script/setup_optional_cross_machine_network.sh`) and the VMs (`./qemu_integration/launch_qemu_cxl1.sh`), as shown below.

```bash

#----------------#
# Set up network
#----------------#

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
```

Before launching the VMs, please keep the server running on the host
```bash
cd submodules/CXLMemSim/build
./cxlmemsim_server --capacity=1024
```

After the server is running, please set up the second VM's network address and hostname by
```bash
#----------------#
# Set up two VMs
#----------------#
cd build
sudo ../qemu_integration/launch_qemu_cxl1.sh # login as root with password: victor129
# in qemu
vi /usr/local/bin/*.sh
# change 192.168.100.10 to 11
# change the bridge IP address to the host's bridge IP.
vi /etc/hostname
# change node0 to node1
shutdown now
```

The VMs should be ready to launch
```bash
# out of qemu
sudo ../qemu_integration/launch_qemu_cxl0.sh 
sudo ../qemu_integration/launch_qemu_cxl1.sh 
```

Make sure "/dev/dax0.0" exists inside both VM:
```bash
ls /dev/dax0.0
```

## GROMACS
Change the hostfile in host 1 to reflect the number of hosts. 
```bash
cd workloads/gromacs
./build.sh
scp libmpi_cxl_shim.so  root@192.168.100.10:/root
scp libmpi_cxl_shim.so  root@192.168.100.11:/root
# Inside host 1:
mpirun --allow-run-as-root -x CXL_SHIM_TRACE=1 -x CXL_DAX_PATH=/dev/dax0.0 -x LD_PRELOAD=$PWD/libmpi_cxl_shim.so --hostfile ./hostfile ./gromacs-2025.3/build/bin/gmx_mpi mdrun -s benchMEM.tpr -nsteps 10000 -resethway -ntomp 1
```


## TIGON
 
```bash
cd workloads/tigon
./scripts/setup.sh HOST
./emulation/image/make_vm_img.sh
sudo ./emulation/start_vms.sh --using-old-img --cxl 0 5 2 0 1 # using 2 hosts
./scripts/setup.sh VMS 2
./scripts/run.sh COMPILE_SYNC 2
./scripts/run_tpcc_dax.sh TwoPLPasha 2 3 mixed 10 15 1 0 1 Clock OnDemand 200000000 1 WriteThrough None 15 5 GROUP_WAL 20000 0 0
```


## OSU Benchmark
Change the hostfile in host 1 to reflect the number of hosts. 
```bash
# Inside host 1:
export CXL_DAX_PATH="/dev/dax0.0"
export CXL_DAX_RESET=1  # Reset allocation counter on first process
export CXL_SHIM_VERBOSE=1
LD_PRELOAD=/root/libmpi_cxl_shim.so mpirun --allow-run-as-root -np 2 -hostfile hostfile -x CXL_DAX_PATH -x CXL_DAX_RESET -x CXL_SHIM_VERBOSE -x LD_PRELOAD ~/osu-micro-benchmarks/mpi/collective/osu_allgather
```

## Troubleshooting
If the QEMU image and the modified linux kernel download links do not work, they can be also found in Google Drive: https://drive.google.com/drive/folders/15r2wxoU_WFa06n1nBVlafg-69PimOH04?usp=sharing

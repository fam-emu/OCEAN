#!/bin/bash
set -eu
set -x

#---------------#
# Install tools
#---------------#

# UV
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.12
source .venv/bin/activate

# Python libraries
uv pip install tomli gdown

# Enable required repos
sudo dnf install -y epel-release
sudo dnf config-manager --set-enabled crb || sudo dnf config-manager --set-enabled powertools

# Core development dependencies
sudo dnf groupinstall -y "Development Tools"   
sudo dnf install -y \
    llvm-devel clang clang-devel \
    libbpf-devel \
    python3-pip \
    boost-devel \
    fmt-devel \
    spdlog-devel \
    librdmacm-devel \
    rdma-core-devel
sudo dnf install -y git
# cannot find cxxopts-devel in Rocky 9.5 for some reason.

## NVIDIA CUDA (requires CUDA repo)
# sudo dnf config-manager --add-repo https://developer.download.nvidia.com/compute/cuda/repos/rhel9/x86_64/cuda-rhel9.repo || true
# sudo dnf install -y cuda-toolkit || true
## Thare no GPUs on Blue Ocean for now. 

# QEMU build dependencies
sudo dnf install -y \
    glib2-devel libgcrypt-devel zlib-devel \
    autoconf automake libtool bison flex pixman-devel bc \
    make ninja-build ncurses-devel elfutils-libelf-devel openssl-devel \
    debootstrap \
    libcap-ng-devel libattr-devel libslirp-devel libpmem-devel

# GCC 13 via gcc-toolset
sudo dnf install -y gcc-toolset-13 gcc-toolset-13-gcc-c++
# Enable the toolset for this script
source /opt/rh/gcc-toolset-13/enable 2>/dev/null

# CMake
mkdir temp || echo "It's okay."
cd temp
culr -OL https://github.com/Kitware/CMake/releases/download/v4.2.3/cmake-4.2.3.tar.gz
tar zxvf cmake-4.2.3.tar.gz 
cd cmake-4.2.3/
sudo ./bootstrap
sudo make -j 4 && sudo make install -j 4
cmake --version
cd ../..
sudo rm -rf temp

#------------------#
# Build CXLMemSim
#------------------#
git submodule update --init --depth 1 submodules/CXLMemSim
cd submodules/CXLMemSim
git submodule update --init --depth 1 lib/qemu 

# Build submodules/CXLMemSim/lib/qemu
cd ./lib/qemu
mkdir -p build || echo "It's okay"
cd build
../configure --prefix=/usr/local --target-list=x86_64-softmmu --enable-debug --enable-libpmem --enable-slirp
make -j 4 && sudo make install -j 4
/usr/local/bin/qemu-system-x86_64 --version
cd ../../..

# Build submodules/CXLMemSim/workloads/gromacs
cd ./workloads/gromacs
sudo dnf install -y openmpi openmpi-devel
export PATH="/usr/lib64/openmpi/bin:$PATH"
export LD_LIBRARY_PATH="/usr/lib64/openmpi/lib:$LD_LIBRARY_PATH"
bash ./build.sh
cd ../../

# Build spdlog, which is for std::format in C++20
mkdir tmp || echo "It's okay"
cd tmp
curl -L https://github.com/gabime/spdlog/archive/refs/tags/v1.17.0.tar.gz -o spdlog_v1.17.0.tar.gz
tar zxvf spdlog_v1.17.0.tar.gz
cd spdlog-1.17.0
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
      -DSPDLOG_USE_STD_FORMAT=ON \
      -DCMAKE_INSTALL_PREFIX=/usr/local
cmake --build build -j 4
sudo cmake --install build -j 4
cd ../..
sudo rm -rf tmp

# Build CXLMemSim
mkdir build || echo "It's okay"
cd build
cmake -G Ninja .. -DSERVER_MODE=ON -DCMAKE_CXX_COMPILER=g++
ninja
cd ../../..  # go to OCEAN root

echo "Built CXLMemSim successfully."


#------------------#
# Copy QEMU images
#------------------#
mkdir build || echo "It's okay"
cd build

# Download bzImage
gdown 1yKD0QG8x-wyFsVV1t5ZhNm_A3zUwlpQe

# Download QEMU image
gdown 1ga5CN3_H1qfReer99w_QcVOYb6R21JHI -O qemu0.img

# Copy
cp qemu0.img qemu1.img

echo "QEMU images should be here."
ls -alFh bzImage qemu0.img qemu1.img 

echo "If they are there, we should be ready to launch ../submodules/CXLMemSim/build/cxlmemsim_server and qemu virtual machines (sudo ../qemu_integration/launch_qemu_cxl0.sh)."

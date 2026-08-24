#!/bin/bash
# Exit immediately if any command fails
set -e 

echo "==== Configuring apt ===="
export DEBIAN_FRONTEND=noninteractive

echo "==== Disk Info ===="
lsblk -o NAME,TYPE,SIZE,MOUNTPOINT

echo "==== Updating and installing base compilation tools ===="
apt-get update
apt-get install -y dselect build-essential cmake 

echo "==== Restoring original packages ===="
xargs apt-get install -y --no-install-recommends --ignore-missing < /tmp/installed_packages.txt
apt-get dselect-upgrade -y

echo "==== Installing Gromacs 2025.3 ===="
wget https://ftp.gromacs.org/gromacs/gromacs-2025.3.tar.gz
tar -xzf gromacs-2025.3.tar.gz
cd gromacs-2025.3
mkdir build && cd build
cmake .. \
   -DGMX_MPI=ON \
   -DGMX_OPENMP=ON \
   -DGMX_GPU=OFF \
   -DGMX_SIMD=AVX2_256 \
   -DCMAKE_C_FLAGS="-fexcess-precision=fast -funroll-all-loops -mavx2 -mfma -O3 -DNDEBUG" \
   -DCMAKE_CXX_FLAGS="-fexcess-precision=fast -funroll-all-loops -mavx2 -mfma -O3 -DNDEBUG -fopenmp" \
   -DCMAKE_C_COMPILER=mpicc \
   -DCMAKE_CXX_COMPILER=mpicxx \
   -DGMX_BUILD_OWN_FFTW=ON \

make -j$(nproc)
make install 

echo "==== Installing OSU Microbenchmarks ===="
cd /root
wget https://mvapich.cse.ohio-state.edu/download/mvapich/osu-micro-benchmarks-7.5.2.tar.gz
tar -xzf osu-micro-benchmarks-7.5.2.tar.gz
cd osu-micro-benchmarks-7.5.2
./configure CC=mpicc CXX=mpicxx --prefix=/opt/osu
make -j$(nproc)
make install
test -x /opt/osu/libexec/osu-micro-benchmarks/mpi/collective/osu_allgather

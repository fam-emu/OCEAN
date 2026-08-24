PACKER_VERSION="1.10.0"

if [ ! -f ./packer ]; then
    wget https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_linux_amd64.zip;
    unzip packer_${PACKER_VERSION}_linux_amd64.zip;
    rm packer_${PACKER_VERSION}_linux_amd64.zip;
fi

# Install the needed plugins
./packer init x86-ocean.pkr.hcl

# Build the image - Pass command line options from this script to build
# command. This can be used to set variable such as qemu path.
./packer build "$@" x86-ocean.pkr.hcl

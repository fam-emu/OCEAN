packer {
  required_plugins {
    qemu = {
      version = ">= 1.0.0"
      source  = "github.com/hashicorp/qemu"
    }
  }
}

variable "iso_checksum" {
  type        = string
  description = "SHA256 checksum or 'file:' URL pointing to a SHA256SUMS file"
  # Packer fetches and matches the checksum automatically from Debian's official file.
  # To hardcode instead: "sha256:<hash>" from
  # https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/SHA256SUMS
  default = "95838884f5ea6c82421dfe6baaa5a639dbbe6756c1e380f9fe7a7cb0c1949d2a"
}

variable "root_password" {
  type        = string
  description = "The root/user password for the VM"
  default     = "victor129"
  sensitive   = true
}

variable "qemu_path" {
  type    = string
  default = "qemu-system-x86_64"
}

variable "kernel_source" {
  type        = string
  description = "Kernel repository used for the candidate image"
  default     = "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git"
}

variable "kernel_ref" {
  type        = string
  description = "Pinned kernel revision compatible with the local QEMU CXL model"
  default     = "v6.16-rc7"
}

source "qemu" "x86-ocean" {
  iso_url      = "https://cdimage.debian.org/mirror/cdimage/archive/13.5.0/amd64/iso-cd/debian-13.5.0-amd64-netinst.iso"
  iso_checksum = var.iso_checksum

  output_directory = "disk-image"
  # Candidate artifacts remain under artifact/ocean-qemu-image/disk-image.  The
  # smoke runner consumes them there and never replaces build/qemu.img.
  vm_name = "qemu.img"

  ssh_username           = "root"
  ssh_password           = var.root_password
  ssh_timeout            = "60m"
  ssh_handshake_attempts = 200

  cpus      = 12
  memory    = 16384
  disk_size = "60G"
  # Boot script uses format=raw; qcow2 would be misread at boot time.
  format           = "raw"
  accelerator      = "kvm"
  qemu_binary      = var.qemu_path
  disk_compression = false

  # Packer's IDE disk appears as /dev/sda, and Debian partitions it as
  # /dev/sda1.  The candidate launcher therefore uses root=/dev/sda1.
  # The default virtio disk would instead appear as /dev/vda.
  disk_interface = "ide"

  headless       = true
  http_directory = "http"

  shutdown_command = "echo '${var.root_password}' | sudo -S shutdown -P now"

  qemuargs = [
    ["-cpu", "host"]
  ]

  boot_wait = "10s"

  boot_command = [
    "<esc><wait>",
    "install auto=true priority=critical ",
    "preseed/url=http://{{ .HTTPIP }}:{{ .HTTPPort }}/preseed.cfg ",
    "locale=en_US keymap=us ",
    "hostname=node0 domain=local ",
    "<enter>"
  ]
}

build {
  sources = ["source.qemu.x86-ocean"]

  provisioner "file" {
    source      = "files/installed_packages.txt"
    destination = "/tmp/installed_packages.txt"
  }

  provisioner "file" {
    source      = "files/cxl-numa-setup.service"
    destination = "/tmp/cxl-numa-setup.service"
  }

  provisioner "file" {
    source      = "files/fixed-numa-setup.sh"
    destination = "/tmp/fixed-numa-setup.sh"
  }

  provisioner "file" {
    source      = "files/enable-cxl-system-ram.sh"
    destination = "/tmp/enable-cxl-system-ram.sh"
  }

  provisioner "shell" {
    inline = [
      "sudo mv /tmp/cxl-numa-setup.service /etc/systemd/system/cxl-numa-setup.service",
      "sudo chmod 644 /etc/systemd/system/cxl-numa-setup.service",
      "sudo mv /tmp/fixed-numa-setup.sh /usr/local/bin/fixed-numa-setup.sh",
      "sudo mv /tmp/enable-cxl-system-ram.sh /usr/local/bin/enable-cxl-system-ram.sh",
      "sudo chmod 755 /usr/local/bin/fixed-numa-setup.sh",
      "sudo systemctl daemon-reload",
      "sudo systemctl enable cxl-numa-setup.service",
    ]
  }

  provisioner "shell" {
    inline = [
      "apt-get update",
      "apt-get install -y build-essential bc bison flex libssl-dev libelf-dev dwarves wget xz-utils rsync kmod git",
      # Linux 6.18's HDM-DB / Back-Invalidate path requires QEMU support that
      # the local CXL model does not yet provide.  Keep this build on the
      # 6.16 line used by the known-working reference kernel until that QEMU
      # capability is backported.
      "git clone --depth 1 --branch ${var.kernel_ref} ${var.kernel_source} linux-cxl-type2",
      "cd linux-cxl-type2",

      # Start from Debian's current kernel config
      "cp /boot/config-$(uname -r) .config",

      # Make required drivers built-in
      "scripts/config --enable DEVTMPFS",
      "scripts/config --enable DEVTMPFS_MOUNT",

      "scripts/config --enable VIRTIO",
      "scripts/config --enable VIRTIO_PCI",
      "scripts/config --enable VIRTIO_BLK",
      "scripts/config --enable VIRTIO_NET",

      "scripts/config --enable EXT4_FS",
      "scripts/config --enable MSDOS_PARTITION",

      "scripts/config --enable ATA",
      # The Packer installer uses the ICH9 IDE path, while the q35 smoke
      # launcher attaches its default disk through ICH9 AHCI.  Both drivers
      # must be built in because QEMU receives no initramfs via -kernel.
      "scripts/config --enable ATA_PIIX",
      "scripts/config --enable SATA_AHCI",
      "scripts/config --enable SCSI",
      "scripts/config --enable BLK_DEV_SD",

      # Optional: remove dependency on external initrd
      #"scripts/config --disable BLK_DEV_INITRD",

      "scripts/config --enable LIBNVDIMM",
      "scripts/config --enable BLK_DEV_PMEM",
      "scripts/config --enable ND_CLAIM",
      "scripts/config --enable NVDIMM_PFN",
      "scripts/config --enable NVDIMM_DAX",
      "scripts/config --enable DAX",
      "scripts/config --enable DEV_DAX",
      "scripts/config --enable DEV_DAX_PMEM",
      "scripts/config --enable DEV_DAX_KMEM",
      "scripts/config --enable DEV_DAX_CXL",

      "scripts/config --enable CXL_BUS",
      "scripts/config --enable CXL_PCI",
      "scripts/config --enable CXL_MEM_RAW_COMMANDS",
      "scripts/config --enable CXL_ACPI",
      "scripts/config --enable CXL_PMEM",
      "scripts/config --enable CXL_MEM",
      "scripts/config --enable CXL_FEATURES",
      "scripts/config --enable CXL_PORT",
      "scripts/config --enable CXL_REGION",
      # Without this the guest cannot run wbinvd, cxl_region_probe() fails with
      # "Failed to synchronize CPU cache state", and the region never enables.
      "scripts/config --enable CXL_REGION_INVALIDATION_TEST",
      "scripts/config --enable CXL_CACHE",
      "scripts/config --enable CXL_TYPE2_ACCEL",
      #"scripts/config --enable CXL_SWITCH_CCI",

      # Debian signs modules and builds BTF; both need keys/pahole we don't want
      # in the way of an out-of-tree rebuild.
      "scripts/config --disable MODULE_SIG",
      "scripts/config --disable DEBUG_INFO_BTF",
      "scripts/config --disable DEBUG_INFO",
      "scripts/config --set-str LOCALVERSION ''",

      "make olddefconfig",

      # olddefconfig can demote a requested option.  Check the boot-critical
      # storage path before spending hours building the kernel in the VM.
      "for sym in ATA ATA_PIIX SATA_AHCI EXT4_FS MSDOS_PARTITION DEVTMPFS DEVTMPFS_MOUNT; do grep -qx \"CONFIG_$sym=y\" .config || { echo \"FATAL: CONFIG_$sym is not =y after olddefconfig:\"; grep -E \"^(# )?CONFIG_$sym\\b\" .config || echo \"  (absent)\"; exit 1; }; done",

      "make -j$(nproc) bzImage",
      "make -j$(nproc) modules",
      # Ground truth ships a populated /lib/modules/<kver>; without it every
      # modprobe in setup_cxl_numa.sh fails and depmod has nothing to read.
      # $(uname -r) here would be the *installer's* kernel, not the one we just
      # built, so let modules_install derive the version itself.
      "make INSTALL_MOD_STRIP=1 modules_install",
      "depmod -a $(make -s kernelrelease)",

      "mkdir -p /boot/custom",
      #"make modules_install INSTALL_MOD_PATH=/mnt/build_rootfs",
      "cp arch/x86/boot/bzImage /boot/custom/bzImage",
      "cp arch/x86/boot/bzImage /tmp/bzImage",
    ]
  }


  # Extract the kernel from the built image so the boot script can use it as
  # ./bzImage. /boot/vmlinuz is a symlink in Debian; copy it to a regular file
  # in /tmp first because SFTP won't follow symlinks.
  #provisioner "shell" {
  #  inline = [
  # Resolve the installed kernel version (exclude rescue kernels)
  #  "KVER=$(ls /boot/vmlinuz-* | grep -v rescue | sort -V | tail -1 | sed 's|/boot/vmlinuz-||')",
  #  "echo \"Staging kernel: $KVER\"",
  #
  #    # Symlink to a well-known name the post-processor will extract
  #    "cp /boot/vmlinuz-$KVER /tmp/bzImage",
  #  ]
  #}
  provisioner "shell" {
    scripts = ["scripts/setup.sh"]
  }
  provisioner "file" {
    direction   = "download"
    source      = "/tmp/bzImage"
    destination = "./disk-image/bzImage"
  }

}

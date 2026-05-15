import { useState, useCallback } from "react";

const STEPS = ["Build host", "Deployment", "Storage", "Network", "Identity", "Extras", "Review & download"];

const icard = (val, icon, title, sub, selected, onClick) => (
  <button key={val} onClick={() => onClick(val)} style={{
    display:"flex", gap:12, alignItems:"flex-start", textAlign:"left",
    padding:"12px 14px", borderRadius:"var(--border-radius-lg)",
    border: selected ? "2px solid var(--color-border-info)" : "0.5px solid var(--color-border-secondary)",
    background: selected ? "var(--color-background-info)" : "var(--color-background-primary)",
    cursor:"pointer", minWidth:0, flex:"1 1 150px"
  }}>
    <i className={`ti ti-${icon}`} style={{fontSize:20, color: selected ? "var(--color-text-info)" : "var(--color-text-secondary)", marginTop:1}} aria-hidden="true"/>
    <span>
      <span style={{display:"block", fontSize:13, fontWeight:500, color: selected ? "var(--color-text-info)" : "var(--color-text-primary)"}}>{title}</span>
      <span style={{display:"block", fontSize:11, color: selected ? "var(--color-text-info)" : "var(--color-text-tertiary)", marginTop:2}}>{sub}</span>
    </span>
  </button>
);

const pill = (val, label, selected, onClick) => (
  <button key={val} onClick={() => onClick(val)} style={{
    padding:"6px 14px", borderRadius:20, fontSize:13, cursor:"pointer",
    border: selected ? "2px solid var(--color-border-info)" : "0.5px solid var(--color-border-secondary)",
    background: selected ? "var(--color-background-info)" : "var(--color-background-primary)",
    color: selected ? "var(--color-text-info)" : "var(--color-text-primary)"
  }}>{label}</button>
);

const multipill = (val, label, arr, setArr) => {
  const on = arr.includes(val);
  return (
    <button key={val} onClick={() => setArr(on ? arr.filter(x=>x!==val) : [...arr, val])} style={{
      padding:"6px 14px", borderRadius:20, fontSize:13, cursor:"pointer",
      border: on ? "2px solid var(--color-border-success)" : "0.5px solid var(--color-border-secondary)",
      background: on ? "var(--color-background-success)" : "var(--color-background-primary)",
      color: on ? "var(--color-text-success)" : "var(--color-text-primary)"
    }}>
      {on && <i className="ti ti-check" style={{fontSize:12, marginRight:4}} aria-hidden="true"/>}{label}
    </button>
  );
};

const field = (label, val, set, placeholder, mono=false) => (
  <div style={{marginBottom:16}}>
    <label style={{display:"block", fontSize:12, color:"var(--color-text-secondary)", marginBottom:4}}>{label}</label>
    <input value={val} onChange={e=>set(e.target.value)} placeholder={placeholder}
      style={{width:"100%", boxSizing:"border-box", fontFamily: mono ? "var(--font-mono)" : "inherit", fontSize:13}} />
  </div>
);

const sectionLabel = (t) => (
  <p style={{fontSize:12, fontWeight:500, color:"var(--color-text-secondary)", textTransform:"uppercase", letterSpacing:"0.06em", margin:"0 0 8px"}}>{t}</p>
);

function generateFiles(cfg) {
  const dasdAddr = cfg.dasdAddr || "0.0.0200";
  const qethDev = cfg.qethDev || "0.0.0600";
  const iface = cfg.iface || "enc600";
  const user = cfg.adminUser || "adminuser";
  const vg = "rhelvg";
  const rootSz = cfg.rootLvSize || "40G";
  const varSz = cfg.varLvSize || "20G";
  const hasLvm = cfg.storage === "lvm_dasd";
  const registry = cfg.registry || "registry.redhat.io";
  const imageTag = cfg.imageTag || "latest";
  const imageName = cfg.imageName || "rhel10-bootc-s390x";
  const hasFcp = cfg.zfcp === "yes";
  const fcp = cfg.zfcpAddr || "0.0.4000,0x5005076305ffd123,0x4023";
  const extraPkgs = cfg.extraPkgs ? cfg.extraPkgs.split(",").map(s=>s.trim()).filter(Boolean) : [];
  const opts = cfg.optional || [];
  const hasQemu = cfg.buildHost === "docker_mac" || cfg.buildHost === "podman_linux";
  const sshMode = cfg.sshAuth || "key_only";
  const deployTarget = cfg.deployTarget || "lpar_dasd";
  const imageType = cfg.imageType || "raw";
  const proxy = cfg.proxy || "";

  const proxyLines = proxy
    ? `\n# Proxy\nENV http_proxy=${proxy} https_proxy=${proxy} no_proxy=localhost,127.0.0.1`
    : "";

  const tempPassBlock = sshMode === "password_temp"
    ? `RUN useradd -m -G wheel ${user} \\\n    && echo '${user}:Ch@ngeMe1st!' | chpasswd \\\n    && chage -d 0 ${user}`
    : `RUN useradd -m -G wheel ${user}`;

  const sshPassLine = sshMode === "password_temp"
    ? `RUN sed -i -e 's/^#\\?PasswordAuthentication .*/PasswordAuthentication yes/' \\\n      -e 's/^#\\?PubkeyAuthentication .*/PubkeyAuthentication yes/' \\\n      -e 's/^#\\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config`
    : `RUN sed -i -e 's/^#\\?PasswordAuthentication .*/PasswordAuthentication no/' \\\n      -e 's/^#\\?PubkeyAuthentication .*/PubkeyAuthentication yes/' \\\n      -e 's/^#\\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config`;

  const extraPkgLines = extraPkgs.length
    ? `      ${extraPkgs.join(" \\\n      ")} \\`
    : "";

  const cloudInitPkg = opts.includes("cloud_init_pkg") ? "      cloud-init \\" : "";
  const qemuAgentPkg = opts.includes("qemu_guest_agent") ? "      qemu-guest-agent \\" : "";
  const lvm2Pkg = hasLvm ? "      lvm2 \\" : "";

  const selinuxMode = opts.includes("selinux_enforcing") ? "enforcing" : "permissive";

  const containerfile = `## ============================================================
## RHEL 10 bootc – s390x image
## Build host : ${cfg.buildHost === "docker_windows" ? "Docker Desktop (Windows)" : cfg.buildHost === "docker_mac" ? "Docker Desktop (Mac)" : "Podman on Linux"}
## Target     : ${deployTarget === "lpar_dasd" ? "IBM Z LPAR (DASD boot)" : deployTarget === "kvm_qcow2" ? "KVM guest (QCOW2)" : "ZD&T (emulated)"}
## Storage    : ${hasLvm ? `LVM on DASD — VG: ${vg}, LVs: root (${rootSz}) + var (${varSz})` : "Single XFS root (LABEL=rootfs)"}
## SSH auth   : ${sshMode === "password_temp" ? "Temp password + SSH key" : sshMode === "cloud_init" ? "cloud-init" : "SSH key only"}
## Admin user : ${user}
## ============================================================

FROM ${registry}/rhel10/rhel-bootc:latest
${proxyLines}
## ── Optional: inject pre-downloaded RPMs ────────────────────
COPY rpms/ /tmp/rpms/
RUN rpm -Uvh /tmp/rpms/*.rpm 2>/dev/null || true

## ── Install packages ────────────────────────────────────────
RUN dnf -y install \\
      openssh-server \\
      vim \\
      curl \\
      chrony \\
      rsyslog \\
      policycoreutils \\
      s390utils-base \\
      zipl \\
      dracut \\
      NetworkManager \\
      util-linux \\
${lvm2Pkg}
${cloudInitPkg}
${qemuAgentPkg}
${extraPkgLines}
    && dnf -y clean all

## ── Enable services ─────────────────────────────────────────
RUN systemctl enable \\
      sshd \\
      rsyslog \\
      chronyd \\
      NetworkManager${opts.includes("cloud_init_pkg") ? " \\\n      cloud-init" : ""}${opts.includes("qemu_guest_agent") ? " \\\n      qemu-guest-agent" : ""}

## ── Admin user: ${user} ─────────────────────────────────────
${tempPassBlock}

## ── SSH hardening ────────────────────────────────────────────
${sshPassLine}
RUN install -d -m 700 -o ${user} -g ${user} /home/${user}/.ssh
COPY ssh/authorized_keys /home/${user}/.ssh/authorized_keys
RUN chmod 600 /home/${user}/.ssh/authorized_keys \\
    && chown ${user}:${user} /home/${user}/.ssh/authorized_keys

## ── s390x dracut driver config ───────────────────────────────
COPY dracut/10-s390x.conf /etc/dracut.conf.d/10-s390x.conf

## ── Filesystem table ─────────────────────────────────────────
COPY fstab /etc/fstab

## ── DASD persistence ─────────────────────────────────────────
COPY dasd.conf /etc/dasd.conf

## ── NetworkManager qeth profile ──────────────────────────────
COPY network/qeth0.nmconnection /etc/NetworkManager/system-connections/qeth0.nmconnection
RUN chmod 600 /etc/NetworkManager/system-connections/qeth0.nmconnection

## ── zipl bootloader config ───────────────────────────────────
COPY zipl/zipl.conf /etc/zipl.conf
${hasLvm ? `
## ── First-boot LVM automation ────────────────────────────────
COPY systemd/firstboot-lvm.service /etc/systemd/system/firstboot-lvm.service
COPY scripts/firstboot-lvm.sh /usr/local/sbin/firstboot-lvm.sh
RUN chmod 0755 /usr/local/sbin/firstboot-lvm.sh \\
    && systemctl enable firstboot-lvm.service
` : ""}
## ── SELinux ──────────────────────────────────────────────────
RUN sed -i 's/^SELINUX=enforcing/SELINUX=${selinuxMode}/' /etc/selinux/config

## ── Rebuild initramfs ────────────────────────────────────────
RUN dracut -f --regenerate-all

## ── SELinux relabel ──────────────────────────────────────────
RUN fixfiles -F relabel

## ── bootc metadata ───────────────────────────────────────────
RUN bootc install-to-filesystem --rootfs /
`;

  const dracut = `# /etc/dracut.conf.d/10-s390x.conf
# s390x drivers for IBM Z LPAR
add_drivers+=" dasd_mod dasd_eckd_mod qdio qeth qeth_l2 zfcp "
${hasLvm ? 'add_dracutmodules+=" lvm "' : ""}
# hostonly=no is critical for cross-arch builds on x86/amd64 hosts
hostonly="no"
omit_drivers+=" floppy "
`;

  const fstab = hasLvm
    ? `/dev/${vg}/root   /       xfs     defaults    0 0\n/dev/${vg}/var    /var    xfs     defaults    0 0\n`
    : `LABEL=rootfs / xfs defaults 0 0\n`;

  const dasdConf = `# /etc/dasd.conf — DASD devices to bring online at boot
${dasdAddr} 1
`;

  const nmconn = `[connection]
id=qeth0
type=ethernet
interface-name=${iface}
autoconnect=true

[ethernet]

[ipv4]
method=dhcp

[ipv6]
method=ignore
`;

  const ziplKernelParams = hasLvm
    ? `root=/dev/${vg}/root rd.dasd=${dasdAddr} rd.lvm.lv=${vg}/root rd.lvm.lv=${vg}/var rd.net=qeth,${qethDev},layer2=1${hasFcp ? ` rd.zfcp=${fcp}` : ""} rhgb quiet`
    : `root=LABEL=rootfs rd.dasd=${dasdAddr} rd.net=qeth,${qethDev},layer2=1${hasFcp ? ` rd.zfcp=${fcp}` : ""} rhgb quiet`;

  const zipl = `[defaultboot]
default = linux

[linux]
target = /boot
kernel = /boot/vmlinuz
ramdisk = /boot/initramfs.img
parameters = "${ziplKernelParams}"
`;

  const firstbootSh = `#!/bin/bash
# /usr/local/sbin/firstboot-lvm.sh — runs once on first boot
# Brings DASD online, formats it, and creates LVM VG + LVs.
# DESTRUCTIVE: erases all data on ${dasdAddr}.

set -euo pipefail
LOG=/var/log/firstboot-lvm.log
exec >> "$LOG" 2>&1

echo "=== firstboot-lvm started: $(date) ==="

DASD_ADDR="${dasdAddr}"
DASD_DEV="/dev/dasda"
VG_NAME="${vg}"
ROOT_LV_SIZE="${rootSz}"
VAR_LV_SIZE="${varSz}"

echo "[1/6] Bringing DASD \${DASD_ADDR} online..."
cio_ignore -r "\${DASD_ADDR}" || true
chccwdev -e "\${DASD_ADDR}"

for i in $(seq 1 20); do
    [ -b "\${DASD_DEV}" ] && break
    sleep 1
done
[ -b "\${DASD_DEV}" ] || { echo "ERROR: \${DASD_DEV} not found"; exit 1; }

echo "[2/6] Low-level formatting \${DASD_DEV}..."
dasdfmt -b 4096 -d cdl -y "\${DASD_DEV}"

echo "[3/6] Partitioning..."
fdasd -a "\${DASD_DEV}"
PART="\${DASD_DEV}1"
for i in $(seq 1 10); do [ -b "\${PART}" ] && break; sleep 1; done

echo "[4/6] Creating LVM PV, VG, and LVs..."
pvcreate "\${PART}"
vgcreate "\${VG_NAME}" "\${PART}"
lvcreate -L "\${ROOT_LV_SIZE}" -n root "\${VG_NAME}"
lvcreate -L "\${VAR_LV_SIZE}"  -n var  "\${VG_NAME}"

echo "[5/6] Formatting XFS..."
mkfs.xfs -f "/dev/\${VG_NAME}/root"
mkfs.xfs -f "/dev/\${VG_NAME}/var"

echo "[6/6] Disabling service..."
systemctl disable firstboot-lvm.service

echo "=== firstboot-lvm complete: $(date) ==="
`;

  const firstbootService = `[Unit]
Description=First-boot DASD + LVM provisioning
After=network.target
Before=local-fs.target
ConditionPathExists=!/var/lib/firstboot-lvm.done
DefaultDependencies=no

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/firstboot-lvm.sh
ExecStartPost=/bin/touch /var/lib/firstboot-lvm.done
RemainAfterExit=yes
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
`;

  const authKeys = `# Replace this placeholder with your real SSH public key.
# Generate: ssh-keygen -t ed25519 -C "${user}@workstation" -f ~/.ssh/${user}_ibmz
# Then: cat ~/.ssh/${user}_ibmz.pub

ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLEKEYHERE ${user}@workstation
`;

  const buildScript = `#!/bin/bash
# build-and-deploy.sh — RHEL 10 bootc s390x master build script
set -euo pipefail

IMAGE_NAME="${imageName}"
IMAGE_TAG="${imageTag}"
FULL_IMAGE="\${IMAGE_NAME}:\${IMAGE_TAG}"
OUTPUT_DIR="/var/tmp/bootc-output"
RH_REGISTRY="${registry}"
BUILDER_IMAGE="\${RH_REGISTRY}/rhel10/bootc-image-builder:latest"
DEPLOY_HOST=""   # set to e.g. root@ibmz-host to enable auto scp

preflight() {
    echo "==> Pre-flight"
    command -v podman >/dev/null 2>&1 || { echo "ERROR: podman not found"; exit 1; }
    if grep -q "EXAMPLEKEYHERE" ssh/authorized_keys 2>/dev/null; then
        echo "ERROR: replace ssh/authorized_keys with your real public key"
        exit 1
    fi
    mkdir -p "\${OUTPUT_DIR}"
    echo "    OK"
}
${hasQemu ? `
setup_qemu() {
    echo "==> Enabling QEMU binfmt for s390x cross-arch build"
    if command -v qemu-s390x-static >/dev/null 2>&1; then
        podman run --rm --privileged multiarch/qemu-user-static --reset -p yes 2>/dev/null || true
    else
        echo "    WARN: qemu-user-static not found — install it for reliable cross-arch builds"
    fi
}
` : ""}
build_image() {
    echo "==> [1/4] Building \${FULL_IMAGE} (linux/s390x)"
    podman build \\
        --platform linux/s390x \\
        --tls-verify=false \\
        --volume /etc/pki/entitlement:/etc/pki/entitlement:ro \\
        --volume /etc/rhsm:/etc/rhsm:ro \\
        --volume /etc/yum.repos.d/redhat.repo:/etc/yum.repos.d/redhat.repo:ro \\
        --network=host \\
        -t "\${FULL_IMAGE}" \\
        -f containerfile/Containerfile \\
        .
}

build_disk_image() {
    echo "==> [2/4] Converting to ${imageType.toUpperCase()} via bootc-image-builder"
    podman run --rm -it \\
        --privileged \\
        --security-opt seccomp=unconfined \\
        -v /var/lib/containers:/var/lib/containers \\
        -v "\${OUTPUT_DIR}:/output" \\
        "\${BUILDER_IMAGE}" \\
        --type ${imageType} \\
        --target-arch s390x \\
        "\${FULL_IMAGE}"
    echo "    Output: \${OUTPUT_DIR}"
    ls -lh "\${OUTPUT_DIR}"
}

deploy() {
    echo "==> [3/4] Deploy"
    IMG=\$(find "\${OUTPUT_DIR}" -name "*.${imageType}" | head -1)
    if [ -z "\${DEPLOY_HOST}" ]; then
        echo "    DEPLOY_HOST not set. Manual deploy:"
        echo "    scp \${IMG} root@<ibmz-host>:/var/lib/images/"
        echo "    Then dd if=\${IMG##*/} of=/dev/dasda bs=64M status=progress && sync"
    else
        scp "\${IMG}" "\${DEPLOY_HOST}:/var/lib/images/"
    fi
}

main() {
    preflight
    podman login "\${RH_REGISTRY}"
${hasQemu ? "    setup_qemu" : ""}
    build_image
    build_disk_image
    deploy
    echo ""
    echo "Done. First login: ssh ${user}@<ip>"${sshMode === "password_temp" ? `\n    echo "Default password: Ch@ngeMe1st! (expires on first login)"` : ""}
}

main "\$@"
`;

  const readme = `# RHEL 10 bootc s390x — build context

## Configuration

| Setting | Value |
|---------|-------|
| Build host | ${cfg.buildHost === "docker_windows" ? "Docker Desktop (Windows)" : cfg.buildHost === "docker_mac" ? "Docker Desktop (Mac)" : "Podman on Linux"} |
| Deploy target | ${deployTarget === "lpar_dasd" ? "IBM Z LPAR (DASD)" : deployTarget === "kvm_qcow2" ? "KVM guest" : "ZD&T"} |
| Image type | ${imageType.toUpperCase()} |
| Storage | ${hasLvm ? `LVM on DASD (VG: ${vg}, root: ${rootSz}, var: ${varSz})` : "Single XFS root"} |
| Admin user | ${user} |
| SSH auth | ${sshMode === "password_temp" ? "Temp password + SSH key" : sshMode === "cloud_init" ? "cloud-init" : "SSH key only"} |
| DASD device | ${dasdAddr} |
| qeth channel | ${qethDev} / ${iface} |

## Before building

1. **Replace \`ssh/authorized_keys\`** with your real public key:
   \`\`\`bash
   ssh-keygen -t ed25519 -C "${user}@workstation" -f ~/.ssh/${user}_ibmz
   cat ~/.ssh/${user}_ibmz.pub > ssh/authorized_keys
   \`\`\`

2. **Confirm device addresses** in \`dasd.conf\`, \`zipl/zipl.conf\`, and \`network/qeth0.nmconnection\` match your LPAR.

3. **Log in to Red Hat registry:**
   \`\`\`bash
   podman login ${registry}
   \`\`\`

## Build

\`\`\`bash
chmod +x scripts/build-and-deploy.sh
sudo ./scripts/build-and-deploy.sh
\`\`\`

## Directory layout

\`\`\`
.
├── containerfile/Containerfile
├── dracut/10-s390x.conf
├── network/qeth0.nmconnection
├── ssh/authorized_keys        ← REPLACE THIS
├── zipl/zipl.conf
├── systemd/firstboot-lvm.service
├── scripts/
│   ├── firstboot-lvm.sh
│   └── build-and-deploy.sh
├── rpms/                      ← drop local RPMs here (optional)
├── dasd.conf
└── fstab
\`\`\`
`;

  return {
    "containerfile/Containerfile": containerfile,
    "dracut/10-s390x.conf": dracut,
    "fstab": fstab,
    "dasd.conf": dasdConf,
    "network/qeth0.nmconnection": nmconn,
    "zipl/zipl.conf": zipl,
    ...(hasLvm ? {
      "scripts/firstboot-lvm.sh": firstbootSh,
      "systemd/firstboot-lvm.service": firstbootService,
    } : {}),
    "ssh/authorized_keys": authKeys,
    "scripts/build-and-deploy.sh": buildScript,
    "rpms/.gitkeep": "",
    "README.md": readme,
  };
}

async function buildTar(files) {
  const enc = new TextEncoder();
  const chunks = [];

  function pad(n, len) {
    return String(n).padStart(len, "0");
  }

  function toOctal(n, len) {
    return n.toString(8).padStart(len, "0");
  }

  function addEntry(name, content, isDir = false) {
    const data = isDir ? new Uint8Array(0) : enc.encode(content);
    const size = data.length;
    const header = new Uint8Array(512);
    const writeStr = (s, off, len) => {
      const b = enc.encode(s.slice(0, len));
      header.set(b, off);
    };
    writeStr(name.slice(0, 99), 0, 100);
    writeStr(isDir ? "0000755\0" : "0000644\0", 100, 8);
    writeStr("0000000\0", 108, 8);
    writeStr("0000000\0", 116, 8);
    writeStr(toOctal(size, 11) + "\0", 124, 12);
    writeStr(toOctal(Math.floor(Date.now() / 1000), 11) + "\0", 136, 12);
    writeStr(" ".repeat(8), 148, 8);
    header[156] = isDir ? 0x35 : 0x30;
    writeStr("ustar  \0", 257, 8);
    let checksum = 0;
    for (let i = 0; i < 512; i++) checksum += header[i];
    writeStr(toOctal(checksum, 6) + "\0 ", 148, 8);
    chunks.push(header);
    if (size > 0) {
      chunks.push(data);
      const pad512 = (512 - (size % 512)) % 512;
      if (pad512) chunks.push(new Uint8Array(pad512));
    }
  }

  const dirs = new Set();
  for (const path of Object.keys(files)) {
    const parts = path.split("/");
    for (let i = 1; i < parts.length; i++) {
      dirs.add(parts.slice(0, i).join("/") + "/");
    }
  }
  for (const d of [...dirs].sort()) addEntry("rhel10-s390x-build/" + d, "", true);
  for (const [path, content] of Object.entries(files)) {
    addEntry("rhel10-s390x-build/" + path, content);
  }
  chunks.push(new Uint8Array(1024));

  const total = chunks.reduce((s, c) => s + c.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) { out.set(c, off); off += c.length; }
  return out;
}

function download(data, filename, mime) {
  const blob = new Blob([data], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export default function App() {
  const [step, setStep] = useState(0);
  const [cfg, setCfg] = useState({
    buildHost: "podman_linux",
    deployTarget: "lpar_dasd",
    imageType: "raw",
    storage: "lvm_dasd",
    rootLvSize: "40G",
    varLvSize: "20G",
    dasdAddr: "0.0.0200",
    qethDev: "0.0.0600",
    iface: "enc600",
    zfcp: "no",
    zfcpAddr: "",
    adminUser: "",
    sshAuth: "key_only",
    optional: [],
    extraPkgs: "",
    proxy: "",
    registry: "registry.redhat.io",
    imageName: "rhel10-bootc-s390x",
    imageTag: "latest",
  });
  const [preview, setPreview] = useState(null);
  const [downloading, setDownloading] = useState(false);

  const set = (k) => (v) => setCfg(c => ({ ...c, [k]: v }));
  const setStr = (k) => (e) => setCfg(c => ({ ...c, [k]: e.target.value }));

  const canNext = () => {
    if (step === 4 && !cfg.adminUser.trim()) return false;
    return true;
  };

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const files = generateFiles(cfg);
      const tar = await buildTar(files);
      download(tar, "rhel10-s390x-build-context.tar", "application/x-tar");
    } finally {
      setDownloading(false);
    }
  };

  const handlePreview = (filename) => {
    const files = generateFiles(cfg);
    setPreview({ filename, content: files[filename] || "" });
  };

  const sLabel = (s, i) => {
    const done = i < step;
    const active = i === step;
    return (
      <div key={i} style={{display:"flex", alignItems:"center", gap:6, flex:"0 0 auto"}}>
        <div style={{
          width:22, height:22, borderRadius:"50%", display:"flex", alignItems:"center", justifyContent:"center",
          fontSize:11, fontWeight:500,
          background: done ? "var(--color-background-success)" : active ? "var(--color-background-info)" : "var(--color-background-secondary)",
          color: done ? "var(--color-text-success)" : active ? "var(--color-text-info)" : "var(--color-text-tertiary)",
          border: active ? "2px solid var(--color-border-info)" : "0.5px solid var(--color-border-secondary)"
        }}>
          {done ? <i className="ti ti-check" style={{fontSize:11}} aria-hidden="true"/> : i+1}
        </div>
        <span style={{fontSize:12, color: active ? "var(--color-text-primary)" : "var(--color-text-tertiary)", fontWeight: active ? 500 : 400}}>{s}</span>
        {i < STEPS.length-1 && <div style={{width:16, height:1, background:"var(--color-border-tertiary)", margin:"0 2px"}}/>}
      </div>
    );
  };

  const stepContent = () => {
    switch(step) {
      case 0: return (
        <div>
          {sectionLabel("Where will you build this image?")}
          <div style={{display:"flex", gap:8, flexWrap:"wrap", marginBottom:20}}>
            {icard("podman_linux","brand-debian","Podman on Linux","RHEL / Fedora host",cfg.buildHost==="podman_linux",set("buildHost"))}
            {icard("docker_windows","brand-windows","Docker Desktop (Windows)","Via PowerShell script",cfg.buildHost==="docker_windows",set("buildHost"))}
            {icard("docker_mac","apple","Docker Desktop (Mac)","QEMU binfmt cross-build",cfg.buildHost==="docker_mac",set("buildHost"))}
          </div>
          {sectionLabel("Red Hat registry")}
          <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:12}}>
            <div>{field("Registry URL", cfg.registry, set("registry"), "registry.redhat.io", true)}</div>
            <div>{field("Image name", cfg.imageName, set("imageName"), "rhel10-bootc-s390x", true)}</div>
          </div>
          {field("Image tag", cfg.imageTag, set("imageTag"), "latest", true)}
          {cfg.buildHost !== "podman_linux" && (
            <div style={{marginTop:8, padding:"10px 12px", borderRadius:"var(--border-radius-md)", background:"var(--color-background-warning)", border:"0.5px solid var(--color-border-warning)"}}>
              <p style={{margin:0, fontSize:13, color:"var(--color-text-warning)"}}>
                <i className="ti ti-alert-triangle" style={{fontSize:14, marginRight:6, verticalAlign:-2}} aria-hidden="true"/>
                {cfg.buildHost === "docker_windows" ? "Docker Desktop on Windows requires WSL2 and the generated PowerShell build script." : "Docker Desktop on Mac uses QEMU binfmt. Install qemu-user-static and run the reset command before building."}
              </p>
            </div>
          )}
        </div>
      );

      case 1: return (
        <div>
          {sectionLabel("Target deployment environment")}
          <div style={{display:"flex", gap:8, flexWrap:"wrap", marginBottom:20}}>
            {icard("lpar_dasd","server","LPAR (DASD boot)","Bare metal IBM Z partition",cfg.deployTarget==="lpar_dasd",set("deployTarget"))}
            {icard("kvm_qcow2","cpu","KVM (QCOW2)","KVM guest on IBM Z",cfg.deployTarget==="kvm_qcow2",set("deployTarget"))}
            {icard("zdt","box","ZD&T (emulated)","z Development & Test",cfg.deployTarget==="zdt",set("deployTarget"))}
          </div>
          {sectionLabel("Output disk image format")}
          <div style={{display:"flex", gap:8, flexWrap:"wrap", marginBottom:20}}>
            {pill("raw","RAW — for DASD dd deploy",cfg.imageType==="raw",set("imageType"))}
            {pill("qcow2","QCOW2 — for KVM / ZD&T",cfg.imageType==="qcow2",set("imageType"))}
          </div>
          {cfg.deployTarget === "kvm_qcow2" && cfg.imageType === "raw" && (
            <div style={{padding:"10px 12px", borderRadius:"var(--border-radius-md)", background:"var(--color-background-warning)", border:"0.5px solid var(--color-border-warning)"}}>
              <p style={{margin:0, fontSize:13, color:"var(--color-text-warning)"}}>
                <i className="ti ti-alert-triangle" style={{fontSize:14, marginRight:6, verticalAlign:-2}} aria-hidden="true"/>
                KVM guests work better with QCOW2. Consider switching the image format.
              </p>
            </div>
          )}
        </div>
      );

      case 2: return (
        <div>
          {sectionLabel("Storage layout")}
          <div style={{display:"flex", gap:8, flexWrap:"wrap", marginBottom:20}}>
            {icard("lvm_dasd","layers-intersect","LVM on DASD","VG with root + var LVs",cfg.storage==="lvm_dasd",set("storage"))}
            {icard("single_xfs","database","Single XFS root","LABEL=rootfs, no LVM",cfg.storage==="single_xfs",set("storage"))}
          </div>
          {cfg.storage === "lvm_dasd" && (
            <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:12}}>
              {field("Root LV size", cfg.rootLvSize, set("rootLvSize"), "40G", true)}
              {field("/var LV size", cfg.varLvSize, set("varLvSize"), "20G", true)}
            </div>
          )}
          {sectionLabel("DASD device address")}
          {field("DASD address", cfg.dasdAddr, set("dasdAddr"), "0.0.0200", true)}
          {sectionLabel("FCP / zFCP storage")}
          <div style={{display:"flex", gap:8, flexWrap:"wrap", marginBottom: cfg.zfcp==="yes" ? 12 : 0}}>
            {pill("no","No — DASD only",cfg.zfcp==="no",set("zfcp"))}
            {pill("yes","Yes — include zFCP",cfg.zfcp==="yes",set("zfcp"))}
          </div>
          {cfg.zfcp === "yes" && field("zFCP address (device,WWPN,LUN)", cfg.zfcpAddr, set("zfcpAddr"), "0.0.4000,0x5005076305ffd123,0x4023", true)}
        </div>
      );

      case 3: return (
        <div>
          {sectionLabel("qeth network interface")}
          <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:12}}>
            {field("qeth channel address", cfg.qethDev, set("qethDev"), "0.0.0600", true)}
            {field("Interface name", cfg.iface, set("iface"), "enc600", true)}
          </div>
          <div style={{padding:"10px 12px", borderRadius:"var(--border-radius-md)", background:"var(--color-background-secondary)", border:"0.5px solid var(--color-border-tertiary)", marginBottom:16}}>
            <p style={{margin:0, fontSize:13, color:"var(--color-text-secondary)"}}>
              <i className="ti ti-info-circle" style={{fontSize:14, marginRight:6, verticalAlign:-2}} aria-hidden="true"/>
              qeth channel addresses come in pairs: the base address you enter here implies channels at +1 and +2. For example, <code style={{fontFamily:"var(--font-mono)", fontSize:12}}>0.0.0600</code> uses <code style={{fontFamily:"var(--font-mono)", fontSize:12}}>0600</code>, <code style={{fontFamily:"var(--font-mono)", fontSize:12}}>0601</code>, and <code style={{fontFamily:"var(--font-mono)", fontSize:12}}>0602</code>.
            </p>
          </div>
          {sectionLabel("HTTP proxy (optional)")}
          {field("Proxy URL", cfg.proxy, set("proxy"), "http://10.2.16.17:8080 — leave blank if none", true)}
        </div>
      );

      case 4: return (
        <div>
          {sectionLabel("Admin user")}
          {field("Username", cfg.adminUser, set("adminUser"), "e.g. britley", true)}
          {!cfg.adminUser.trim() && (
            <p style={{fontSize:12, color:"var(--color-text-danger)", margin:"-8px 0 12px"}}>Username is required</p>
          )}
          {sectionLabel("SSH authentication")}
          <div style={{display:"flex", gap:8, flexWrap:"wrap", marginBottom:16}}>
            {icard("key_only","key","SSH key only","Inject pubkey at build time",cfg.sshAuth==="key_only",set("sshAuth"))}
            {icard("password_temp","lock","Temp password + key","Forced change on first login",cfg.sshAuth==="password_temp",set("sshAuth"))}
            {icard("cloud_init","cloud","cloud-init","Dynamic provisioning",cfg.sshAuth==="cloud_init",set("sshAuth"))}
          </div>
          {cfg.sshAuth === "password_temp" && (
            <div style={{padding:"10px 12px", borderRadius:"var(--border-radius-md)", background:"var(--color-background-warning)", border:"0.5px solid var(--color-border-warning)", marginBottom:12}}>
              <p style={{margin:0, fontSize:13, color:"var(--color-text-warning)"}}>
                <i className="ti ti-alert-triangle" style={{fontSize:14, marginRight:6, verticalAlign:-2}} aria-hidden="true"/>
                Default temp password is <code style={{fontFamily:"var(--font-mono)", fontSize:12}}>Ch@ngeMe1st!</code> — expires on first login. Disable password auth once your SSH key is confirmed.
              </p>
            </div>
          )}
          <div style={{padding:"10px 12px", borderRadius:"var(--border-radius-md)", background:"var(--color-background-secondary)", border:"0.5px solid var(--color-border-tertiary)"}}>
            <p style={{margin:0, fontSize:13, color:"var(--color-text-secondary)"}}>
              <i className="ti ti-info-circle" style={{fontSize:14, marginRight:6, verticalAlign:-2}} aria-hidden="true"/>
              The <code style={{fontFamily:"var(--font-mono)", fontSize:12}}>ssh/authorized_keys</code> file in the build context contains a placeholder. Replace it with your real public key before running the build.
            </p>
          </div>
        </div>
      );

      case 5: return (
        <div>
          {sectionLabel("Optional components")}
          <div style={{display:"flex", gap:8, flexWrap:"wrap", marginBottom:20}}>
            {multipill("selinux_enforcing","SELinux enforcing",cfg.optional,set("optional"))}
            {multipill("cloud_init_pkg","cloud-init package",cfg.optional,set("optional"))}
            {multipill("qemu_guest_agent","qemu-guest-agent",cfg.optional,set("optional"))}
            {multipill("fips","FIPS mode",cfg.optional,set("optional"))}
          </div>
          {sectionLabel("Extra packages (comma-separated)")}
          {field("Additional dnf packages", cfg.extraPkgs, set("extraPkgs"), "e.g. tmux, strace, tcpdump")}
          {cfg.optional.includes("fips") && (
            <div style={{padding:"10px 12px", borderRadius:"var(--border-radius-md)", background:"var(--color-background-warning)", border:"0.5px solid var(--color-border-warning)", marginBottom:12}}>
              <p style={{margin:0, fontSize:13, color:"var(--color-text-warning)"}}>
                <i className="ti ti-alert-triangle" style={{fontSize:14, marginRight:6, verticalAlign:-2}} aria-hidden="true"/>
                FIPS mode requires additional kernel parameters and dracut configuration. Enable it manually post-install with <code style={{fontFamily:"var(--font-mono)", fontSize:12}}>fips-mode-setup --enable</code> and a full reboot.
              </p>
            </div>
          )}
        </div>
      );

      case 6: {
        const files = generateFiles(cfg);
        const fileList = Object.keys(files).filter(f => f !== "rpms/.gitkeep");
        return (
          <div>
            <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:8, marginBottom:16}}>
              {[
                ["Build host", cfg.buildHost === "docker_windows" ? "Docker (Windows)" : cfg.buildHost === "docker_mac" ? "Docker (Mac)" : "Podman (Linux)"],
                ["Deploy target", cfg.deployTarget === "lpar_dasd" ? "LPAR / DASD" : cfg.deployTarget === "kvm_qcow2" ? "KVM guest" : "ZD&T"],
                ["Image format", cfg.imageType.toUpperCase()],
                ["Storage", cfg.storage === "lvm_dasd" ? `LVM (${cfg.rootLvSize} root / ${cfg.varLvSize} var)` : "Single XFS"],
                ["Admin user", cfg.adminUser || "(not set)"],
                ["SSH auth", cfg.sshAuth === "password_temp" ? "Temp pw + key" : cfg.sshAuth === "cloud_init" ? "cloud-init" : "Key only"],
                ["DASD", cfg.dasdAddr],
                ["qeth", `${cfg.qethDev} / ${cfg.iface}`],
              ].map(([k,v]) => (
                <div key={k} style={{padding:"8px 10px", borderRadius:"var(--border-radius-md)", background:"var(--color-background-secondary)"}}>
                  <div style={{fontSize:11, color:"var(--color-text-tertiary)", marginBottom:2}}>{k}</div>
                  <div style={{fontSize:13, fontWeight:500, fontFamily:"var(--font-mono)"}}>{v}</div>
                </div>
              ))}
            </div>
            {sectionLabel("Files in build context")}
            <div style={{display:"flex", flexDirection:"column", gap:4, marginBottom:16}}>
              {fileList.map(f => (
                <div key={f} style={{display:"flex", alignItems:"center", justifyContent:"space-between", padding:"6px 10px", borderRadius:"var(--border-radius-md)", border:"0.5px solid var(--color-border-tertiary)", background:"var(--color-background-primary)"}}>
                  <span style={{fontSize:13, fontFamily:"var(--font-mono)", color:"var(--color-text-primary)"}}>{f}</span>
                  <button onClick={() => handlePreview(f)} style={{fontSize:12, padding:"2px 10px", borderRadius:6, border:"0.5px solid var(--color-border-secondary)", background:"transparent", cursor:"pointer", color:"var(--color-text-secondary)"}}>
                    <i className="ti ti-eye" style={{fontSize:13, marginRight:4, verticalAlign:-1}} aria-hidden="true"/>preview
                  </button>
                </div>
              ))}
            </div>
            {!cfg.adminUser.trim() && (
              <div style={{padding:"10px 12px", borderRadius:"var(--border-radius-md)", background:"var(--color-background-danger)", border:"0.5px solid var(--color-border-danger)", marginBottom:12}}>
                <p style={{margin:0, fontSize:13, color:"var(--color-text-danger)"}}>
                  <i className="ti ti-alert-triangle" style={{fontSize:14, marginRight:6, verticalAlign:-2}} aria-hidden="true"/>
                  Admin username is empty — go back to Identity and fill it in.
                </p>
              </div>
            )}
            <button onClick={handleDownload} disabled={!cfg.adminUser.trim() || downloading}
              style={{width:"100%", padding:"12px", borderRadius:"var(--border-radius-lg)", fontSize:14, fontWeight:500, cursor: cfg.adminUser.trim() ? "pointer" : "not-allowed", background: cfg.adminUser.trim() ? "var(--color-background-info)" : "var(--color-background-secondary)", color: cfg.adminUser.trim() ? "var(--color-text-info)" : "var(--color-text-tertiary)", border: cfg.adminUser.trim() ? "2px solid var(--color-border-info)" : "0.5px solid var(--color-border-secondary)"}}>
              <i className="ti ti-download" style={{fontSize:16, marginRight:8, verticalAlign:-2}} aria-hidden="true"/>
              {downloading ? "Generating..." : "Download rhel10-s390x-build-context.tar"}
            </button>
          </div>
        );
      }
    }
  };

  return (
    <div style={{padding:"1rem 0", maxWidth:680}}>
      <h2 style={{visibility:"hidden", position:"absolute"}}>RHEL 10 bootc s390x image builder</h2>

      <div style={{marginBottom:24}}>
        <div style={{display:"flex", alignItems:"center", gap:4, marginBottom:6, flexWrap:"wrap"}}>
          <i className="ti ti-settings" style={{fontSize:18, color:"var(--color-text-secondary)"}} aria-hidden="true"/>
          <span style={{fontSize:16, fontWeight:500}}>RHEL 10 bootc s390x — image builder</span>
        </div>
        <div style={{display:"flex", alignItems:"center", flexWrap:"wrap", gap:2, rowGap:8}}>
          {STEPS.map((s,i) => sLabel(s,i))}
        </div>
      </div>

      <div style={{background:"var(--color-background-primary)", border:"0.5px solid var(--color-border-tertiary)", borderRadius:"var(--border-radius-lg)", padding:"1.25rem"}}>
        <p style={{fontSize:14, fontWeight:500, margin:"0 0 16px", color:"var(--color-text-primary)"}}>{STEPS[step]}</p>
        {stepContent()}
      </div>

      <div style={{display:"flex", justifyContent:"space-between", marginTop:12}}>
        <button onClick={() => setStep(s => Math.max(0,s-1))} disabled={step===0}
          style={{padding:"8px 18px", borderRadius:"var(--border-radius-md)", fontSize:13, cursor: step===0 ? "not-allowed" : "pointer", opacity: step===0 ? 0.4 : 1}}>
          <i className="ti ti-arrow-left" style={{fontSize:13, marginRight:6, verticalAlign:-1}} aria-hidden="true"/>Back
        </button>
        {step < STEPS.length-1 && (
          <button onClick={() => canNext() && setStep(s => s+1)} disabled={!canNext()}
            style={{padding:"8px 18px", borderRadius:"var(--border-radius-md)", fontSize:13, cursor: canNext() ? "pointer" : "not-allowed", opacity: canNext() ? 1 : 0.4, background:"var(--color-background-info)", color:"var(--color-text-info)", border:"2px solid var(--color-border-info)"}}>
            Next<i className="ti ti-arrow-right" style={{fontSize:13, marginLeft:6, verticalAlign:-1}} aria-hidden="true"/>
          </button>
        )}
      </div>

      {preview && (
        <div style={{marginTop:20, background:"var(--color-background-primary)", border:"0.5px solid var(--color-border-tertiary)", borderRadius:"var(--border-radius-lg)", overflow:"hidden"}}>
          <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", padding:"10px 14px", borderBottom:"0.5px solid var(--color-border-tertiary)", background:"var(--color-background-secondary)"}}>
            <span style={{fontSize:13, fontFamily:"var(--font-mono)", color:"var(--color-text-secondary)"}}>{preview.filename}</span>
            <button onClick={() => setPreview(null)} style={{background:"transparent", border:"none", cursor:"pointer", padding:4, color:"var(--color-text-secondary)"}}>
              <i className="ti ti-x" style={{fontSize:16}} aria-hidden="true"/>
            </button>
          </div>
          <pre style={{margin:0, padding:"12px 14px", fontSize:11.5, lineHeight:1.6, overflowX:"auto", background:"#1e1e1e", color:"#a8ff60", fontFamily:"var(--font-mono)", whiteSpace:"pre-wrap", wordBreak:"break-all"}}>
            {preview.content}
          </pre>
        </div>
      )}
    </div>
  );
}

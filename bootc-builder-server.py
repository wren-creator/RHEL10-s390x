#!/usr/bin/env python3
"""
bootc-builder-server.py
Run on your zLinux build host:
    python3 bootc-builder-server.py
Then open http://<host-ip>:8080 from any browser on your network.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import html
import sys

PORT = 8080

# ── Embedded HTML UI ──────────────────────────────────────────────────────────

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RHEL 10 bootc · s390x Image Builder</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@600;900&display=swap');

  :root {
    --green:      #39ff14;
    --green-dim:  #1a7a00;
    --green-glow: rgba(57,255,20,0.18);
    --amber:      #ffb700;
    --red:        #ff3c3c;
    --bg:         #080e08;
    --bg1:        #0d150d;
    --bg2:        #101a10;
    --border:     #1e3a1e;
    --text:       #c8e8c8;
    --text-dim:   #4a7a4a;
    --mono:       'Share Tech Mono', monospace;
    --display:    'Orbitron', sans-serif;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--mono);
    font-size: 14px;
    min-height: 100vh;
    background-image:
      repeating-linear-gradient(
        0deg,
        transparent,
        transparent 2px,
        rgba(57,255,20,0.015) 2px,
        rgba(57,255,20,0.015) 4px
      );
  }

  /* scanline flicker */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 1px,
      rgba(0,0,0,0.08) 1px,
      rgba(0,0,0,0.08) 2px
    );
    pointer-events: none;
    z-index: 9999;
  }

  header {
    border-bottom: 1px solid var(--border);
    padding: 28px 40px 20px;
    position: relative;
    overflow: hidden;
  }

  header::after {
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--green), transparent);
  }

  .header-label {
    font-family: var(--display);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.3em;
    color: var(--green-dim);
    text-transform: uppercase;
    margin-bottom: 6px;
  }

  h1 {
    font-family: var(--display);
    font-size: clamp(18px, 3vw, 28px);
    font-weight: 900;
    color: var(--green);
    text-shadow: 0 0 20px var(--green-glow), 0 0 40px rgba(57,255,20,0.08);
    letter-spacing: 0.05em;
    line-height: 1.2;
  }

  .subtitle {
    margin-top: 6px;
    color: var(--text-dim);
    font-size: 12px;
    letter-spacing: 0.08em;
  }

  .container {
    max-width: 860px;
    margin: 0 auto;
    padding: 32px 40px 60px;
  }

  /* ── Sections ── */
  .section {
    border: 1px solid var(--border);
    border-radius: 2px;
    margin-bottom: 24px;
    background: var(--bg1);
    position: relative;
    overflow: hidden;
  }

  .section::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, var(--green-dim), transparent);
  }

  .section-header {
    padding: 12px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--bg2);
  }

  .section-num {
    font-family: var(--display);
    font-size: 10px;
    font-weight: 600;
    color: var(--green);
    background: rgba(57,255,20,0.08);
    border: 1px solid var(--green-dim);
    border-radius: 2px;
    padding: 2px 7px;
    letter-spacing: 0.1em;
  }

  .section-title {
    font-family: var(--display);
    font-size: 11px;
    font-weight: 600;
    color: var(--text);
    letter-spacing: 0.15em;
    text-transform: uppercase;
  }

  .section-body {
    padding: 22px 20px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px 28px;
  }

  .section-body.single { grid-template-columns: 1fr; }
  .section-body.triple { grid-template-columns: 1fr 1fr 1fr; }

  /* ── Fields ── */
  .field { display: flex; flex-direction: column; gap: 6px; }

  .field label {
    font-size: 11px;
    letter-spacing: 0.12em;
    color: var(--text-dim);
    text-transform: uppercase;
  }

  .field input, .field select {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 2px;
    color: var(--green);
    font-family: var(--mono);
    font-size: 14px;
    padding: 9px 12px;
    outline: none;
    transition: border-color 0.15s, box-shadow 0.15s;
    width: 100%;
    appearance: none;
    -webkit-appearance: none;
  }

  .field select {
    cursor: pointer;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%231a7a00'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 12px center;
    padding-right: 32px;
  }

  .field input:focus, .field select:focus {
    border-color: var(--green-dim);
    box-shadow: 0 0 0 2px rgba(57,255,20,0.08), inset 0 0 8px rgba(57,255,20,0.04);
  }

  .field input::placeholder { color: #2a4a2a; }

  .field .hint {
    font-size: 11px;
    color: #2e5a2e;
    line-height: 1.5;
  }

  /* ── Warning / info boxes ── */
  .warn {
    background: rgba(255,183,0,0.06);
    border: 1px solid rgba(255,183,0,0.25);
    border-radius: 2px;
    padding: 10px 14px;
    font-size: 12px;
    color: var(--amber);
    line-height: 1.6;
    grid-column: 1 / -1;
  }

  .warn::before { content: '⚠ '; }

  /* ── Toggle switch ── */
  .toggle-row {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 10px 0 2px;
    grid-column: 1 / -1;
  }

  .toggle-switch {
    position: relative;
    width: 46px;
    height: 24px;
    flex-shrink: 0;
  }

  .toggle-switch input[type=checkbox] { display: none; }

  .toggle-track {
    position: absolute;
    inset: 0;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    cursor: pointer;
    transition: background 0.2s, border-color 0.2s, box-shadow 0.2s;
  }

  .toggle-switch input:checked ~ .toggle-track {
    background: var(--green-dim);
    border-color: var(--green-dim);
    box-shadow: 0 0 10px var(--green-glow);
  }

  .toggle-track::after {
    content: '';
    position: absolute;
    top: 3px; left: 3px;
    width: 16px; height: 16px;
    background: var(--text-dim);
    border-radius: 50%;
    transition: transform 0.2s, background 0.2s;
  }

  .toggle-switch input:checked ~ .toggle-track::after {
    transform: translateX(22px);
    background: var(--green);
  }

  .toggle-info { flex: 1; }
  .toggle-title { font-size: 13px; color: var(--text); margin-bottom: 3px; }
  .toggle-hint  { font-size: 11px; color: var(--text-dim); line-height: 1.5; }
  .toggle-hint code {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--green-dim);
    background: rgba(57,255,20,0.06);
    padding: 1px 4px;
    border-radius: 2px;
  }

  /* ── Generate button ── */
  .generate-wrap {
    display: flex;
    justify-content: center;
    margin: 8px 0 32px;
  }

  button[type=submit] {
    font-family: var(--display);
    font-size: 13px;
    font-weight: 900;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    background: transparent;
    color: var(--green);
    border: 2px solid var(--green-dim);
    border-radius: 2px;
    padding: 14px 48px;
    cursor: pointer;
    position: relative;
    overflow: hidden;
    transition: color 0.2s, border-color 0.2s, box-shadow 0.2s;
  }

  button[type=submit]::before {
    content: '';
    position: absolute;
    inset: 0;
    background: var(--green);
    transform: scaleX(0);
    transform-origin: left;
    transition: transform 0.2s;
    z-index: -1;
  }

  button[type=submit]:hover {
    color: var(--bg);
    border-color: var(--green);
    box-shadow: 0 0 24px var(--green-glow);
  }

  button[type=submit]:hover::before { transform: scaleX(1); }

  /* ── Output script ── */
  .output-wrap {
    display: none;
  }

  .output-wrap.visible { display: block; }

  .output-header {
    border: 1px solid var(--border);
    border-bottom: none;
    border-radius: 2px 2px 0 0;
    padding: 10px 20px;
    background: var(--bg2);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .output-title {
    font-family: var(--display);
    font-size: 11px;
    font-weight: 600;
    color: var(--green);
    letter-spacing: 0.15em;
    text-transform: uppercase;
  }

  .copy-btn {
    font-family: var(--mono);
    font-size: 12px;
    background: rgba(57,255,20,0.08);
    color: var(--green);
    border: 1px solid var(--green-dim);
    border-radius: 2px;
    padding: 5px 14px;
    cursor: pointer;
    letter-spacing: 0.06em;
    transition: background 0.15s;
  }

  .copy-btn:hover { background: rgba(57,255,20,0.18); }

  .output-script {
    border: 1px solid var(--border);
    border-radius: 0 0 2px 2px;
    padding: 24px;
    background: #040804;
    color: #7dff6b;
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1.7;
    white-space: pre;
    overflow-x: auto;
    max-height: 600px;
    overflow-y: auto;
  }

  /* scrollbar */
  .output-script::-webkit-scrollbar { width: 6px; height: 6px; }
  .output-script::-webkit-scrollbar-track { background: #040804; }
  .output-script::-webkit-scrollbar-thumb { background: var(--green-dim); border-radius: 3px; }

  .comment  { color: #2e6e2e; }
  .section-line { color: #1a5a1a; }

  /* ── Footer ── */
  footer {
    border-top: 1px solid var(--border);
    padding: 16px 40px;
    color: var(--text-dim);
    font-size: 11px;
    letter-spacing: 0.06em;
    display: flex;
    justify-content: space-between;
  }

  /* ── Responsive ── */
  @media (max-width: 600px) {
    .container { padding: 20px 16px 40px; }
    header { padding: 20px 16px 16px; }
    .section-body { grid-template-columns: 1fr; }
    .section-body.triple { grid-template-columns: 1fr; }
    footer { flex-direction: column; gap: 4px; }
  }
</style>
</head>
<body>

<header>
  <div class="header-label">IBM Z · RHEL 10 · Image Mode</div>
  <h1>bootc s390x Image Builder</h1>
  <div class="subtitle">generates a complete build + dd deploy script for your LPAR</div>
</header>

<div class="container">
<form method="POST" action="/generate" id="form">

  <!-- ── 01 Identity ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-num">01</span>
      <span class="section-title">Admin Identity</span>
    </div>
    <div class="section-body">
      <div class="field">
        <label>Admin username</label>
        <input type="text" name="admin_user" value="britley" required
               placeholder="e.g. britley">
        <span class="hint">Created in wheel group, SSH key injected at build time</span>
      </div>
      <div class="field">
        <label>SSH public key</label>
        <input type="text" name="ssh_pubkey"
               placeholder="ssh-ed25519 AAAA... user@host"
               required>
        <span class="hint">Paste your full public key — written to ~/.ssh/authorized_keys</span>
      </div>
    </div>
  </div>

  <!-- ── 02 Storage ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-num">02</span>
      <span class="section-title">DASD Storage</span>
    </div>
    <div class="section-body">
      <div class="field">
        <label>Boot DASD address</label>
        <input type="text" name="boot_dasd" value="0.0.0200"
               pattern="[0-9a-fA-F]\.[0-9a-fA-F]\.[0-9a-fA-F]{4}"
               required placeholder="0.0.0200">
        <span class="hint">Goes into dasd.conf and zipl.conf — the DASD the OS boots from</span>
      </div>
      <div class="field">
        <label>DD target DASD device</label>
        <input type="text" name="dd_dasd" value="/dev/dasda"
               required placeholder="/dev/dasda">
        <span class="hint">Block device to write the RAW image to (e.g. /dev/dasda, /dev/dasdb)</span>
      </div>
      <div class="field">
        <label>Storage layout</label>
        <select name="storage_layout">
          <option value="lvm">LVM on DASD (root + /var LVs)</option>
          <option value="single">Single XFS root (no LVM)</option>
        </select>
      </div>
      <div class="field" id="vg-name-field">
        <label>LVM volume group name</label>
        <input type="text" name="vg_name" value="rhelvg" placeholder="rhelvg">
        <span class="hint">Used in fstab and zipl kernel parameters</span>
      </div>
      <div class="warn">
        dasdfmt is destructive — the DD target DASD will be fully overwritten. Confirm the device address before running the script.
      </div>
    </div>
  </div>

  <!-- ── 03 Network ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-num">03</span>
      <span class="section-title">qeth Network</span>
    </div>
    <div class="section-body triple">
      <div class="field">
        <label>qeth base channel</label>
        <input type="text" name="qeth_channel" value="0.0.0600"
               pattern="[0-9a-fA-F]\.[0-9a-fA-F]\.[0-9a-fA-F]{4}"
               required placeholder="0.0.0600">
        <span class="hint">Channels 0600, 0601, 0602 will be used</span>
      </div>
      <div class="field">
        <label>Interface name</label>
        <input type="text" name="iface" value="enc600"
               required placeholder="enc600">
        <span class="hint">NM connection interface-name</span>
      </div>
      <div class="field">
        <label>IP configuration</label>
        <select name="ip_method">
          <option value="dhcp">DHCP</option>
          <option value="static">Static (edit script after)</option>
        </select>
      </div>
    </div>
  </div>

  <!-- ── 04 Build ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-num">04</span>
      <span class="section-title">Build Options</span>
    </div>
    <div class="section-body">
      <div class="field">
        <label>Output image name</label>
        <input type="text" name="image_name" value="rhel10-bootc-s390x"
               required placeholder="rhel10-bootc-s390x">
        <span class="hint">Local tag used during build — not pushed to a registry</span>
      </div>
      <div class="field">
        <label>Image tag</label>
        <input type="text" name="image_tag" value="latest"
               required placeholder="latest">
      </div>
      <div class="field">
        <label>Output directory</label>
        <input type="text" name="output_dir" value="/var/tmp/bootc-output"
               required placeholder="/var/tmp/bootc-output">
        <span class="hint">Where bootc-image-builder writes the RAW image</span>
      </div>
      <div class="field">
        <label>HTTP proxy (optional)</label>
        <input type="text" name="proxy" placeholder="http://10.0.0.1:8080">
        <span class="hint">Leave blank if not needed</span>
      </div>
    </div>
  </div>

  <!-- ── 05 Security ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-num">05</span>
      <span class="section-title">Security</span>
    </div>
    <div class="section-body">
      <div class="field">
        <label>SELinux mode</label>
        <select name="selinux_mode" id="selinux-select" onchange="onSelinuxChange(this)">
          <option value="enforcing">Enforcing — full policy enforcement</option>
          <option value="permissive" selected>Permissive — log only (recommended for first boot)</option>
          <option value="disabled">Disabled — no SELinux</option>
        </select>
        <span class="hint">Written to /etc/selinux/config — switch to enforcing after first boot is stable</span>
      </div>
      <div></div>
      <div class="warn" id="selinux-warn" style="display:none;">
        Disabling SELinux removes all mandatory access control. Use Permissive for initial deployment and switch to Enforcing once the system is validated.
      </div>
      <div class="toggle-row">
        <label class="toggle-switch">
          <input type="checkbox" name="fips" id="fips-chk" value="on" onchange="onFipsChange(this)">
          <span class="toggle-track"></span>
        </label>
        <div class="toggle-info">
          <div class="toggle-title">FIPS 140-2 mode</div>
          <div class="toggle-hint">Adds <code>fips=1</code> to zipl kernel parameters, installs <code>crypto-policies-scripts</code>, adds the <code>fips</code> dracut module, and runs <code>update-crypto-policies --set FIPS</code> at build time</div>
        </div>
      </div>
      <div class="warn" id="fips-warn" style="display:none;">
        FIPS restricts allowed algorithms and key sizes. Confirm your SSH key type is FIPS-compatible (RSA ≥ 2048 or ed25519). A full re-IPL is required after first boot to activate the FIPS kernel parameter.
      </div>
    </div>
  </div>

  <div class="generate-wrap">
    <button type="submit">&#x25B6;&nbsp; Generate Script</button>
  </div>

</form>

<!-- ── Script output ── -->
<div class="output-wrap" id="output-wrap">
  <div class="output-header">
    <span class="output-title">Generated Script — build-and-deploy.sh</span>
    <button class="copy-btn" onclick="copyScript()">[ copy ]</button>
  </div>
  <pre class="output-script" id="script-out">__SCRIPT_PLACEHOLDER__</pre>
</div>

</div><!-- /container -->

<footer>
  <span>RHEL 10 bootc · IBM Z s390x</span>
  <span>base image: registry.redhat.io/rhel10/rhel-bootc:latest</span>
</footer>

<script>
// Show output if server returned a script
(function(){
  var el = document.getElementById('script-out');
  if (el && el.textContent.trim() && el.textContent.trim() !== '__SCRIPT_PLACEHOLDER__') {
    document.getElementById('output-wrap').classList.add('visible');
    el.scrollIntoView({behavior:'smooth', block:'start'});
  }
})();

function copyScript() {
  var text = document.getElementById('script-out').textContent;
  navigator.clipboard.writeText(text).then(function(){
    var btn = document.querySelector('.copy-btn');
    btn.textContent = '[ copied! ]';
    setTimeout(function(){ btn.textContent = '[ copy ]'; }, 2000);
  });
}

function onFipsChange(el) {
  document.getElementById('fips-warn').style.display = el.checked ? 'block' : 'none';
}

function onSelinuxChange(el) {
  document.getElementById('selinux-warn').style.display = el.value === 'disabled' ? 'block' : 'none';
}
</script>

</body>
</html>
"""

# ── Script generator ──────────────────────────────────────────────────────────

def generate_script(p):
    admin_user    = p.get('admin_user',    ['britley'])[0].strip()
    ssh_pubkey    = p.get('ssh_pubkey',    [''])[0].strip()
    boot_dasd     = p.get('boot_dasd',    ['0.0.0200'])[0].strip()
    dd_dasd       = p.get('dd_dasd',      ['/dev/dasda'])[0].strip()
    storage       = p.get('storage_layout',['lvm'])[0].strip()
    vg_name       = p.get('vg_name',      ['rhelvg'])[0].strip()
    qeth_channel  = p.get('qeth_channel', ['0.0.0600'])[0].strip()
    iface         = p.get('iface',        ['enc600'])[0].strip()
    ip_method     = p.get('ip_method',    ['dhcp'])[0].strip()
    image_name    = p.get('image_name',   ['rhel10-bootc-s390x'])[0].strip()
    image_tag     = p.get('image_tag',    ['latest'])[0].strip()
    output_dir    = p.get('output_dir',   ['/var/tmp/bootc-output'])[0].strip()
    proxy         = p.get('proxy',        [''])[0].strip()
    selinux_mode  = p.get('selinux_mode', ['permissive'])[0].strip()
    fips          = p.get('fips',         ['off'])[0].strip()

    full_image  = f"{image_name}:{image_tag}"
    builder_img = "registry.redhat.io/rhel10/bootc-image-builder:latest"
    base_img    = "registry.redhat.io/rhel10/rhel-bootc:latest"

    # derive dasd device path from boot_dasd address for dasdfmt
    dasd_dev_short = boot_dasd.split('.')[-1]  # e.g. 0200
    # dd_dasd is already a /dev path

    proxy_block = ""
    if proxy:
        proxy_block = f"""
export http_proxy="{proxy}"
export https_proxy="{proxy}"
export no_proxy="localhost,127.0.0.1,registry.redhat.io"
"""

    fips_param = " fips=1" if fips == "on" else ""

    pkgs = [
        "      openssh-server", "      vim", "      curl", "      chrony",
        "      rsyslog", "      policycoreutils", "      s390utils-base",
        "      zipl", "      dracut", "      NetworkManager", "      util-linux",
    ]
    if storage == "lvm":
        pkgs.append("      lvm2")
    if fips == "on":
        pkgs.append("      crypto-policies-scripts")
    pkgs.append("      qemu-guest-agent")
    pkg_install_lines = " \\\n".join(pkgs) + " \\"

    fips_policy_block = "\n# FIPS crypto policy\nRUN update-crypto-policies --set FIPS" if fips == "on" else ""

    if storage == 'lvm':
        fstab_content = f"/dev/{vg_name}/root   /       xfs  defaults  0 0\\n/dev/{vg_name}/var    /var    xfs  defaults  0 0"
        zipl_params   = f"root=/dev/{vg_name}/root rd.dasd={boot_dasd} rd.lvm.lv={vg_name}/root rd.lvm.lv={vg_name}/var rd.net=qeth,{qeth_channel},layer2=1{fips_param}"
        firstboot_section = f"""
# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 · Write firstboot-lvm.sh
# ─────────────────────────────────────────────────────────────────────────────
log "Writing firstboot-lvm.sh..."
cat > "${{BUILD_CTX}}/scripts/firstboot-lvm.sh" << 'FBEOF'
#!/bin/bash
set -euo pipefail
LOG=/var/log/firstboot-lvm.log
exec >> "$LOG" 2>&1
echo "=== firstboot-lvm started: $(date) ==="

DASD_ADDR="{boot_dasd}"
DASD_DEV="{dd_dasd}"
VG_NAME="{vg_name}"

echo "[1/6] Bringing DASD online..."
cio_ignore -r "$DASD_ADDR" || true
chccwdev -e "$DASD_ADDR"
for i in $(seq 1 20); do [ -b "$DASD_DEV" ] && break; sleep 1; done
[ -b "$DASD_DEV" ] || {{ echo "ERROR: $DASD_DEV not found"; exit 1; }}

echo "[2/6] Low-level formatting..."
dasdfmt -b 4096 -d cdl -y "$DASD_DEV"

echo "[3/6] Partitioning..."
fdasd -a "$DASD_DEV"
PART="${{DASD_DEV}}1"
for i in $(seq 1 10); do [ -b "$PART" ] && break; sleep 1; done

echo "[4/6] Creating LVM PV, VG, LVs..."
pvcreate "$PART"
vgcreate "$VG_NAME" "$PART"
lvcreate -L 40G -n root "$VG_NAME"
lvcreate -L 20G -n var  "$VG_NAME"

echo "[5/6] Formatting XFS..."
mkfs.xfs -f "/dev/$VG_NAME/root"
mkfs.xfs -f "/dev/$VG_NAME/var"

echo "[6/6] Disabling service..."
systemctl disable firstboot-lvm.service
touch /var/lib/firstboot-lvm.done
echo "=== firstboot-lvm complete: $(date) ==="
FBEOF
chmod +x "${{BUILD_CTX}}/scripts/firstboot-lvm.sh"

cat > "${{BUILD_CTX}}/systemd/firstboot-lvm.service" << 'SVCEOF'
[Unit]
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
SVCEOF
"""
        firstboot_containerfile = f"""# First-boot LVM automation
COPY scripts/firstboot-lvm.sh /usr/local/sbin/firstboot-lvm.sh
COPY systemd/firstboot-lvm.service /etc/systemd/system/firstboot-lvm.service
RUN chmod 0755 /usr/local/sbin/firstboot-lvm.sh \\\\
    && systemctl enable firstboot-lvm.service"""
    else:
        fstab_content  = "LABEL=rootfs / xfs defaults 0 0"
        zipl_params    = f"root=LABEL=rootfs rd.dasd={boot_dasd} rd.net=qeth,{qeth_channel},layer2=1{fips_param}"
        firstboot_section = ""
        firstboot_containerfile = "# No LVM — single XFS root, no firstboot service needed"

    script = f"""#!/bin/bash
# =============================================================================
# build-and-deploy.sh
# Generated by bootc-builder-server
#
# RHEL 10 bootc s390x — build and deploy to DASD
# Admin user  : {admin_user}
# Boot DASD   : {boot_dasd}
# DD target   : {dd_dasd}
# qeth channel: {qeth_channel} / {iface}
# Storage     : {"LVM on DASD (VG: " + vg_name + ")" if storage == "lvm" else "Single XFS root"}
# SELinux     : {selinux_mode}
# FIPS        : {"enabled" if fips == "on" else "disabled"}
# Base image  : {base_img}
# =============================================================================
set -euo pipefail

# ─── Colour helpers ───────────────────────────────────────────────────────────
RED='\\033[0;31m'; YEL='\\033[1;33m'; GRN='\\033[0;32m'; CYN='\\033[0;36m'; NC='\\033[0m'
log()  {{ echo -e "${{GRN}}[+]${{NC}} $*"; }}
warn() {{ echo -e "${{YEL}}[!]${{NC}} $*"; }}
err()  {{ echo -e "${{RED}}[✗]${{NC}} $*" >&2; exit 1; }}
step() {{ echo -e "\\n${{CYN}}══${{NC}} $* ${{CYN}}══${{NC}}"; }}

BUILD_CTX="/var/tmp/bootc-build-ctx"
OUTPUT_DIR="{output_dir}"
IMAGE_NAME="{image_name}"
IMAGE_TAG="{image_tag}"
FULL_IMAGE="${{IMAGE_NAME}}:${{IMAGE_TAG}}"
BUILDER_IMAGE="{builder_img}"
ADMIN_USER="{admin_user}"
BOOT_DASD="{boot_dasd}"
DD_TARGET="{dd_dasd}"
{proxy_block}
# ─────────────────────────────────────────────────────────────────────────────
# STEP 0 · Pre-flight checks
# ─────────────────────────────────────────────────────────────────────────────
step "Pre-flight checks"
command -v podman >/dev/null 2>&1 || err "podman not found — install it first"
[ "$(id -u)" -eq 0 ] || err "This script must run as root (or with sudo)"
[ -b "$DD_TARGET" ] || err "DD target $DD_TARGET is not a block device — check device address"
mkdir -p "$BUILD_CTX/dracut" "$BUILD_CTX/network" "$BUILD_CTX/ssh" \\
         "$BUILD_CTX/zipl" "$BUILD_CTX/scripts" "$BUILD_CTX/systemd" \\
         "$BUILD_CTX/rpms" "$OUTPUT_DIR"
log "Pre-flight OK"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 · Registry login
# ─────────────────────────────────────────────────────────────────────────────
step "Registry login"
log "Logging in to registry.redhat.io..."
podman login registry.redhat.io || err "Registry login failed"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 · Write build context files
# ─────────────────────────────────────────────────────────────────────────────
step "Writing build context to $BUILD_CTX"

log "Writing dracut/10-s390x.conf..."
cat > "${{BUILD_CTX}}/dracut/10-s390x.conf" << 'EOF'
add_drivers+=" dasd_mod dasd_eckd_mod qdio qeth qeth_l2 zfcp "
{"add_dracutmodules+=\" lvm \"" if storage == "lvm" else ""}
{"add_dracutmodules+=\" fips \"" if fips == "on" else ""}
hostonly="no"
omit_drivers+=" floppy "
EOF

log "Writing network/qeth0.nmconnection..."
cat > "${{BUILD_CTX}}/network/qeth0.nmconnection" << 'EOF'
[connection]
id=qeth0
type=ethernet
interface-name={iface}
autoconnect=true

[ipv4]
method={ip_method}

[ipv6]
method=ignore
EOF
chmod 600 "${{BUILD_CTX}}/network/qeth0.nmconnection"

log "Writing fstab..."
printf '{fstab_content}\\n' > "${{BUILD_CTX}}/fstab"

log "Writing dasd.conf..."
printf '{boot_dasd} 1\\n' > "${{BUILD_CTX}}/dasd.conf"

log "Writing zipl/zipl.conf..."
cat > "${{BUILD_CTX}}/zipl/zipl.conf" << 'EOF'
[defaultboot]
default = linux

[linux]
target = /boot
kernel = /boot/vmlinuz
ramdisk = /boot/initramfs.img
parameters = "{zipl_params}"
EOF

log "Writing ssh/authorized_keys..."
cat > "${{BUILD_CTX}}/ssh/authorized_keys" << 'EOF'
{ssh_pubkey}
EOF
chmod 600 "${{BUILD_CTX}}/ssh/authorized_keys"
{firstboot_section}
# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 · Write Containerfile
# ─────────────────────────────────────────────────────────────────────────────
step "Writing Containerfile"
cat > "${{BUILD_CTX}}/Containerfile" << 'CFEOF'
FROM {base_img}

# Optional local RPMs
COPY rpms/ /tmp/rpms/
RUN rpm -Uvh /tmp/rpms/*.rpm 2>/dev/null || true

# Install packages
RUN dnf -y install \\
{pkg_install_lines}
  && dnf -y clean all
{fips_policy_block}

# Enable services
RUN systemctl enable sshd rsyslog chronyd NetworkManager

# Admin user: {admin_user}
RUN useradd -m -G wheel {admin_user} \\
    && echo '{admin_user}:Ch@ngeMe1st!' | chpasswd \\
    && chage -d 0 {admin_user}

# SSH hardening
RUN sed -i \\
      -e 's/^#\\?PasswordAuthentication .*/PasswordAuthentication yes/' \\
      -e 's/^#\\?PubkeyAuthentication .*/PubkeyAuthentication yes/' \\
      -e 's/^#\\?PermitRootLogin .*/PermitRootLogin no/' \\
      /etc/ssh/sshd_config
RUN install -d -m 700 -o {admin_user} -g {admin_user} /home/{admin_user}/.ssh
COPY ssh/authorized_keys /home/{admin_user}/.ssh/authorized_keys
RUN chmod 600 /home/{admin_user}/.ssh/authorized_keys \\
    && chown {admin_user}:{admin_user} /home/{admin_user}/.ssh/authorized_keys

# s390x configs
COPY dracut/10-s390x.conf /etc/dracut.conf.d/10-s390x.conf
COPY fstab /etc/fstab
COPY dasd.conf /etc/dasd.conf
COPY network/qeth0.nmconnection /etc/NetworkManager/system-connections/qeth0.nmconnection
RUN chmod 600 /etc/NetworkManager/system-connections/qeth0.nmconnection
COPY zipl/zipl.conf /etc/zipl.conf

{firstboot_containerfile}

# SELinux: {selinux_mode}
RUN sed -i 's/^SELINUX=.*/SELINUX={selinux_mode}/' /etc/selinux/config

# Rebuild initramfs
RUN dracut -f --regenerate-all

# SELinux relabel
RUN fixfiles -F relabel
CFEOF
log "Containerfile written"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 · podman build
# ─────────────────────────────────────────────────────────────────────────────
step "Building container image: $FULL_IMAGE"
podman build \\
    --platform linux/s390x \\
    --tls-verify=false \\
    --volume /etc/pki/entitlement:/etc/pki/entitlement:ro \\
    --volume /etc/rhsm:/etc/rhsm:ro \\
    --volume /etc/yum.repos.d/redhat.repo:/etc/yum.repos.d/redhat.repo:ro \\
    --network=host \\
    -t "$FULL_IMAGE" \\
    -f "${{BUILD_CTX}}/Containerfile" \\
    "$BUILD_CTX"
log "Container image built: $FULL_IMAGE"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 · bootc-image-builder → RAW image
# ─────────────────────────────────────────────────────────────────────────────
step "Running bootc-image-builder → RAW"
podman run --rm -it \\
    --privileged \\
    --security-opt seccomp=unconfined \\
    -v /var/lib/containers:/var/lib/containers \\
    -v "${{OUTPUT_DIR}}:/output" \\
    "$BUILDER_IMAGE" \\
    --type raw \\
    --target-arch s390x \\
    "$FULL_IMAGE"

RAW_IMAGE=$(find "$OUTPUT_DIR" -name "*.raw" | head -1)
[ -f "$RAW_IMAGE" ] || err "RAW image not found in $OUTPUT_DIR"
log "RAW image: $RAW_IMAGE ($(du -sh "$RAW_IMAGE" | cut -f1))"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 · Bring DASD online
# ─────────────────────────────────────────────────────────────────────────────
step "Bringing DASD $BOOT_DASD online"
cio_ignore -r "$BOOT_DASD" 2>/dev/null || true
chccwdev -e "$BOOT_DASD"
for i in $(seq 1 20); do
    [ -b "$DD_TARGET" ] && break
    warn "Waiting for $DD_TARGET... ($i/20)"
    sleep 1
done
[ -b "$DD_TARGET" ] || err "$DD_TARGET did not appear after 20 seconds"
lsdasd | grep -i "${{BOOT_DASD##*.}}" || warn "Could not confirm DASD status — check lsdasd manually"
log "DASD online: $DD_TARGET"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 · Low-level format (dasdfmt) + partition
# ─────────────────────────────────────────────────────────────────────────────
step "Formatting DASD (CDL, 4096b blocks)"
warn "This will ERASE all data on $DD_TARGET"
read -r -p "Type YES to continue: " CONFIRM
[ "$CONFIRM" = "YES" ] || err "Aborted by user"
dasdfmt -b 4096 -d cdl -y "$DD_TARGET"
fdasd -a "$DD_TARGET"
log "DASD formatted and partitioned"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 8 · dd image to DASD
# ─────────────────────────────────────────────────────────────────────────────
step "Writing RAW image to $DD_TARGET"
dd if="$RAW_IMAGE" of="$DD_TARGET" bs=64M status=progress
sync
log "dd complete — write buffers flushed"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 9 · Install zipl bootloader
# ─────────────────────────────────────────────────────────────────────────────
step "Installing zipl bootloader"
MOUNT_PT="/mnt/bootc-zipl"
mkdir -p "$MOUNT_PT"
mount "${{DD_TARGET}}1" "$MOUNT_PT" || mount "${{DD_TARGET}}p1" "$MOUNT_PT" || \\
    err "Could not mount $DD_TARGET partition — check fdasd output"
zipl --verbose --target "$MOUNT_PT"
umount "$MOUNT_PT"
log "zipl installed"

# ─────────────────────────────────────────────────────────────────────────────
# DONE
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${{GRN}}══════════════════════════════════════════════════════${{NC}}"
echo -e "${{GRN}}  Build and deploy complete${{NC}}"
echo -e "${{GRN}}══════════════════════════════════════════════════════${{NC}}"
echo ""
echo "  IPL address : ${{BOOT_DASD##*.}}"
echo "  First login : ssh ${{ADMIN_USER}}@<lpar-ip>"
echo "  Default pw  : Ch@ngeMe1st!  (expires on first login)"
echo ""
echo "  IPL the LPAR from HMC: Load → Normal → ${{BOOT_DASD##*.}}"
echo ""
"""
    return script


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} → {fmt % args}")

    def send_page(self, script_content=""):
        body = PAGE.replace(
            '__SCRIPT_PLACEHOLDER__',
            html.escape(script_content) if script_content else '__SCRIPT_PLACEHOLDER__'
        )
        data = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if urlparse(self.path).path in ('/', '/index.html'):
            self.send_page()
        else:
            self.send_error(404)

    def do_POST(self):
        if urlparse(self.path).path != '/generate':
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length', 0))
        raw    = self.rfile.read(length).decode('utf-8')
        params = parse_qs(raw)
        script = generate_script(params)
        self.send_page(script)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    host = '0.0.0.0'
    print(f"\n  RHEL 10 bootc s390x builder")
    print(f"  ─────────────────────────────")
    print(f"  Listening on  http://0.0.0.0:{PORT}")
    print(f"  Open from your workstation:")

    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        print(f"  → http://{ip}:{PORT}")
    except Exception:
        print(f"  → http://<this-host-ip>:{PORT}")

    print(f"\n  Ctrl-C to stop\n")
    try:
        HTTPServer((host, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        sys.exit(0)

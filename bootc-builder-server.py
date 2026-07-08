#!/usr/bin/env python3
"""
bootc-builder-server.py
Run on your zLinux build host:
    python3 bootc-builder-server.py
Then open http://<host-ip>:8080 from any browser on your network.
"""

from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import html
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid

PORT = 8080

# Name of the isolated buildx builder used for cross-arch (docker path).
BUILDX_BUILDER_NAME = "mainframe-builder"
# Where per-build RAW/image artifacts are written (one subdir per job).
OUTPUT_ROOT = "/var/tmp/bootc-output"

jobs = {}  # job_id → {'lines': [], 'done': False, 'rc': None, 'artifact': None, ...}


# ── Infrastructure Automation Engine ──────────────────────────────────────────
# Cross-compiling s390x on a non-Z host needs QEMU binfmt emulation. These helpers
# detect the host/engine and self-heal the emulation layer (spec milestone 1).

def detect_engine():
    """Return the preferred container engine name: 'docker', 'podman', or None."""
    for eng in ('docker', 'podman'):
        if shutil.which(eng):
            return eng
    return None


def detect_native_arch():
    """Host machine architecture, e.g. 's390x', 'x86_64', 'aarch64'."""
    return platform.machine()


def build_mode_for(arch):
    """'native' when the host already is the target arch, else 'cross'."""
    return 'native' if detect_native_arch() == arch else 'cross'


def _binfmt_registered(target):
    """True if a QEMU binfmt handler for the target arch is installed."""
    return os.path.exists(f'/proc/sys/fs/binfmt_misc/qemu-{target}')


def _run_streamed(cmd, emit):
    """Run cmd, emit each stdout/stderr line, return the exit code."""
    emit(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        for line in proc.stdout:
            emit(line.rstrip('\n'))
        proc.wait()
        return proc.returncode
    except FileNotFoundError as exc:
        emit(f"[error] {exc}")
        return 127


def ensure_build_engine(engine, arch, emit):
    """Idempotent, self-healing setup of the cross-compile build engine.

    Modeled on the spec's enforce_mainframe_infrastructure(). Emits progress
    lines via emit(line). Returns True when the engine is ready to build `arch`.
    """
    mode = build_mode_for(arch)
    emit(f"[infra] target arch : {arch}")
    emit(f"[infra] host arch   : {detect_native_arch()}")
    emit(f"[infra] engine      : {engine or 'none found'}")
    emit(f"[infra] build mode  : {mode}")

    if mode == 'native':
        emit("[infra] Native architecture — no QEMU emulation required.")
        emit("[infra] Platform state optimal. Builder ready.")
        return True

    if engine is None:
        emit("[infra] No container engine found — install docker or podman first.")
        return False

    if engine == 'docker':
        return _ensure_docker_buildx(arch, emit)
    return _ensure_podman_binfmt(arch, emit)


def _ensure_docker_buildx(arch, emit):
    """Ensure an isolated docker-container buildx builder registers linux/<arch>."""
    platform_tag = f"linux/{arch}"
    check = subprocess.run(
        ['docker', 'buildx', 'inspect', BUILDX_BUILDER_NAME],
        capture_output=True, text=True,
    )
    if platform_tag in check.stdout:
        emit(f"[infra] buildx '{BUILDX_BUILDER_NAME}' already registers {platform_tag} — no change.")
        return True

    emit(f"[infra] Realignment required — registering {platform_tag} execution layer...")
    subprocess.run(['docker', 'buildx', 'rm', BUILDX_BUILDER_NAME],
                   capture_output=True, text=True)
    steps = [
        ['docker', 'run', '--privileged', '--rm', 'tonistiigi/binfmt', '--install', 'all'],
        ['docker', 'buildx', 'create', '--name', BUILDX_BUILDER_NAME,
         '--driver', 'docker-container', '--use'],
        ['docker', 'buildx', 'inspect', '--bootstrap'],
    ]
    for cmd in steps:
        rc = _run_streamed(cmd, emit)
        if rc != 0:
            emit(f"[infra] Step failed (rc={rc}) — aborting engine prep.")
            return False
    emit("[infra] Mainframe cross-compilation layer established.")
    return True


def _ensure_podman_binfmt(arch, emit):
    """Ensure QEMU binfmt handlers are registered for podman cross-builds."""
    if _binfmt_registered(arch):
        emit(f"[infra] binfmt handler qemu-{arch} already registered — no change.")
        return True

    emit(f"[infra] Registering QEMU binfmt handlers for {arch}...")
    rc = _run_streamed(
        ['podman', 'run', '--rm', '--privileged',
         'multiarch/qemu-user-static', '--reset', '-p', 'yes'],
        emit,
    )
    if rc != 0:
        emit(f"[infra] binfmt registration failed (rc={rc}).")
        return False
    if _binfmt_registered(arch):
        emit(f"[infra] qemu-{arch} now registered. Builder ready.")
        return True
    emit(f"[infra] Warning: qemu-{arch} still not visible after registration.")
    return False


def run_engine_job(job_id, arch):
    """Streamed job wrapper around ensure_build_engine() for the web UI."""
    job = jobs[job_id]

    def emit(line):
        with job['lock']:
            job['lines'].append(line)

    try:
        engine = detect_engine()
        ok = ensure_build_engine(engine, arch, emit)
        with job['lock']:
            job['done'] = True
            job['rc'] = 0 if ok else 1
    except Exception as exc:
        with job['lock']:
            job['lines'].append(f'[server error] {exc}')
            job['done'] = True
            job['rc'] = 1


def run_preflight():
    """Check build host readiness. Returns list of {name, ok, detail} dicts."""
    import socket as _socket
    checks = []

    engine = detect_engine()
    engine_path = shutil.which(engine) if engine else None
    checks.append({
        'name': 'container engine',
        'ok': engine is not None,
        'detail': f'{engine} — {engine_path}' if engine else 'neither docker nor podman found',
    })

    host_arch = detect_native_arch()
    checks.append({
        'name': 'host architecture',
        'ok': True,
        'detail': (f'{host_arch} — native s390x builds run without emulation'
                   if host_arch == 's390x'
                   else f'{host_arch} — s390x targets cross-compile under QEMU'),
    })

    if host_arch != 's390x':
        emu_ok = _binfmt_registered('s390x')
        checks.append({
            'name': 'QEMU s390x emulation',
            'ok': emu_ok,
            'detail': ('binfmt qemu-s390x registered'
                       if emu_ok else 'not registered — click "Prepare Build Engine"'),
        })

    ent_dir = '/etc/pki/entitlement'
    try:
        pems = [f for f in os.listdir(ent_dir) if f.endswith('.pem')]
        ent_ok = len(pems) > 0
        ent_detail = f'{len(pems)} cert(s) found' if ent_ok else 'empty — run subscription-manager register'
    except FileNotFoundError:
        ent_ok, ent_detail = False, f'{ent_dir} missing — run subscription-manager register'
    checks.append({'name': 'RHEL entitlement certs', 'ok': ent_ok, 'detail': ent_detail})

    rhsm_ok = os.path.isfile('/etc/rhsm/rhsm.conf')
    checks.append({
        'name': '/etc/rhsm config',
        'ok': rhsm_ok,
        'detail': 'present' if rhsm_ok else 'missing — system may not be subscribed',
    })

    repo_ok = os.path.isfile('/etc/yum.repos.d/redhat.repo')
    checks.append({
        'name': 'redhat.repo',
        'ok': repo_ok,
        'detail': 'present' if repo_ok else 'missing — run subscription-manager refresh',
    })

    try:
        sock = _socket.create_connection(('registry.redhat.io', 443), timeout=5)
        sock.close()
        reg_ok, reg_detail = True, 'reachable'
    except Exception as exc:
        reg_ok, reg_detail = False, str(exc)
    checks.append({'name': 'registry.redhat.io reachable', 'ok': reg_ok, 'detail': reg_detail})

    if engine:
        try:
            r = subprocess.run(
                [engine, 'login', '--get-login', 'registry.redhat.io'],
                capture_output=True, text=True, timeout=5,
            )
            login_ok = r.returncode == 0
            login_detail = (r.stdout.strip() if login_ok
                            else f'not logged in — run: {engine} login registry.redhat.io')
        except Exception as exc:
            login_ok, login_detail = False, str(exc)
    else:
        login_ok, login_detail = False, 'no container engine found'
    checks.append({'name': 'registry.redhat.io login', 'ok': login_ok, 'detail': login_detail})

    return checks


def _find_artifact(out_dir, fmt):
    """Locate the produced image file (bootc-image-builder may nest it in subdirs)."""
    matches = []
    for root, _dirs, files in os.walk(out_dir):
        for name in files:
            if name.endswith(f'.{fmt}'):
                matches.append(os.path.join(root, name))
    if not matches:
        return None
    # Newest by mtime wins.
    return max(matches, key=lambda p: os.path.getmtime(p))


def run_build_job(job_id, script, out_dir, fmt):
    """Run a build script in a background thread, capturing output line by line.

    On success, records the produced image path so it can be downloaded. The
    script also prints an `ARTIFACT_PATH=...` sentinel we prefer over scanning.
    """
    job = jobs[job_id]
    script_path = f'/var/tmp/bootc-build-{job_id}.sh'
    try:
        with open(script_path, 'w') as fh:
            fh.write(script)
        os.chmod(script_path, 0o700)
        proc = subprocess.Popen(
            ['bash', script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        artifact = None
        for line in proc.stdout:
            line = line.rstrip('\n')
            if line.startswith('ARTIFACT_PATH='):
                candidate = line.split('=', 1)[1].strip()
                if candidate and os.path.isfile(candidate):
                    artifact = candidate
            with job['lock']:
                job['lines'].append(line)
        proc.wait()
        if artifact is None:
            artifact = _find_artifact(out_dir, fmt)
        with job['lock']:
            job['done'] = True
            job['rc'] = proc.returncode
            if proc.returncode == 0 and artifact:
                job['artifact'] = artifact
                job['artifact_name'] = os.path.basename(artifact)
    except Exception as exc:
        with job['lock']:
            job['lines'].append(f'[server error] {exc}')
            job['done'] = True
            job['rc'] = 1
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass

# ── Embedded HTML UI ──────────────────────────────────────────────────────────

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RHEL 10 · Image Mode Studio</title>
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

  /* ── Preflight panel ── */
  .preflight-body { padding: 16px 20px; }

  .preflight-item {
    display: grid;
    grid-template-columns: 18px 230px 1fr;
    align-items: baseline;
    gap: 0 12px;
    padding: 5px 0;
    border-bottom: 1px solid rgba(30,58,30,0.5);
    font-size: 12px;
  }
  .preflight-item:last-child { border-bottom: none; }
  .preflight-icon { font-size: 13px; font-weight: 700; text-align: center; }
  .preflight-item.ok   .preflight-icon { color: var(--green); }
  .preflight-item.fail .preflight-icon { color: var(--red); }
  .preflight-item.ok   .preflight-name { color: var(--text); }
  .preflight-item.fail .preflight-name { color: var(--red); }
  .preflight-detail { color: var(--text-dim); font-family: var(--mono); word-break: break-all; }

  .preflight-run-btn {
    font-family: var(--mono);
    font-size: 12px;
    background: rgba(57,255,20,0.06);
    color: var(--green);
    border: 1px solid var(--green-dim);
    border-radius: 2px;
    padding: 4px 14px;
    cursor: pointer;
    letter-spacing: 0.06em;
    transition: background 0.15s;
  }
  .preflight-run-btn:hover { background: rgba(57,255,20,0.18); }
  .preflight-run-btn:disabled { opacity: 0.4; cursor: default; }

  /* ── Build status badge ── */
  .build-status { font-family: var(--mono); font-size: 12px; color: var(--text-dim); letter-spacing: 0.06em; }
  .build-status.running { color: var(--amber); }
  .build-status.done    { color: var(--green); }
  .build-status.failed  { color: var(--red); }

  /* ── Engine badge ── */
  .engine-badge {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    color: var(--text-dim);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 2px 8px;
  }
  .engine-badge.native { color: var(--green); border-color: var(--green-dim); }
  .engine-badge.cross  { color: var(--amber); border-color: rgba(255,183,0,0.35); }

  /* ── Deliverable / download ── */
  .deliverable {
    border: 1px solid var(--green-dim);
    border-top: none;
    border-radius: 0 0 2px 2px;
    background: rgba(57,255,20,0.04);
    padding: 18px 24px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    align-items: flex-start;
  }
  a.download-btn {
    font-family: var(--display);
    font-size: 13px;
    font-weight: 900;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    text-decoration: none;
    color: var(--bg);
    background: var(--green);
    border: 2px solid var(--green);
    border-radius: 2px;
    padding: 12px 40px;
    box-shadow: 0 0 24px var(--green-glow);
    transition: box-shadow 0.2s, transform 0.1s;
  }
  a.download-btn:hover { box-shadow: 0 0 36px rgba(57,255,20,0.4); transform: translateY(-1px); }

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

  button.build-now {
    font-family: var(--display);
    font-size: 13px;
    font-weight: 900;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    background: transparent;
    color: var(--amber);
    border: 2px solid rgba(255,183,0,0.35);
    border-radius: 2px;
    padding: 14px 48px;
    cursor: pointer;
    transition: color 0.2s, border-color 0.2s, background 0.2s, box-shadow 0.2s;
  }
  button.build-now:hover {
    color: var(--bg);
    border-color: var(--amber);
    background: var(--amber);
    box-shadow: 0 0 24px rgba(255,183,0,0.25);
  }
  button.build-now:disabled { opacity: 0.4; cursor: not-allowed; }

  /* ── Arch cards ── */
  .arch-card {
    font-family: var(--mono);
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
    padding: 12px 18px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 2px;
    color: var(--text-dim);
    cursor: pointer;
    transition: border-color 0.15s, box-shadow 0.15s, color 0.15s;
    min-width: 150px;
  }
  .arch-card:hover {
    border-color: var(--green-dim);
    color: var(--text);
  }
  .arch-card.selected {
    border-color: var(--green);
    color: var(--green);
    box-shadow: 0 0 12px var(--green-glow);
  }
  .arch-card-title { font-size: 13px; font-weight: bold; }
  .arch-card-sub   { font-size: 11px; color: var(--text-dim); }
  .arch-card.selected .arch-card-sub { color: var(--green-dim); }

  /* ── Format pills ── */
  .fmt-pill {
    font-family: var(--mono);
    font-size: 12px;
    padding: 6px 14px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 2px;
    color: var(--text-dim);
    cursor: pointer;
    letter-spacing: 0.05em;
    transition: border-color 0.15s, color 0.15s, box-shadow 0.15s;
  }
  .fmt-pill:hover { border-color: var(--green-dim); color: var(--text); }
  .fmt-pill.selected {
    border-color: var(--green);
    color: var(--green);
    box-shadow: 0 0 8px var(--green-glow);
  }

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
  <div class="header-label">RHEL 10 · Image Mode · Multi-Arch Studio</div>
  <h1>Image Mode Studio</h1>
  <div class="subtitle">Phase A — configure → prepare engine → build → download the RAW image · Phase B — dd to DASD on the Z host</div>
</header>

<div class="container">

<!-- ── Pre-flight checks ── -->
<div class="section" style="margin-bottom:24px;">
  <div class="section-header" style="justify-content:space-between;">
    <div style="display:flex;align-items:center;gap:10px;">
      <span class="section-num">✓</span>
      <span class="section-title">Pre-flight Checks</span>
    </div>
    <button class="preflight-run-btn" id="preflight-btn" type="button" onclick="runPreflight()">[ run checks ]</button>
  </div>
  <div id="preflight-results" class="preflight-body" style="display:none;"></div>
</div>

<!-- ── Build Engine (Infrastructure Automation) ── -->
<div class="section" style="margin-bottom:24px;">
  <div class="section-header" style="justify-content:space-between;">
    <div style="display:flex;align-items:center;gap:10px;">
      <span class="section-num">⚙</span>
      <span class="section-title">Build Engine</span>
      <span class="engine-badge" id="engine-badge">detecting…</span>
    </div>
    <button class="preflight-run-btn" id="engine-btn" type="button" onclick="prepareEngine()">[ prepare build engine ]</button>
  </div>
  <div class="preflight-body" style="padding:14px 20px;">
    <div class="toggle-hint" style="line-height:1.6;">
      Cross-compiling <code>s390x</code> on a non-Z host needs QEMU emulation. This self-heals the
      <code>binfmt</code> / <code>buildx</code> layer (docker or podman, auto-detected). A native
      s390x host skips emulation entirely.
    </div>
    <pre class="output-script" id="engine-out" style="display:none;white-space:pre-wrap;max-height:240px;margin-top:12px;"></pre>
    <span class="build-status" id="engine-status" style="display:block;margin-top:8px;"></span>
  </div>
</div>

<form method="POST" action="/generate" id="form">

  <!-- ── 00 Build Target ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-num">00</span>
      <span class="section-title">Build Target</span>
    </div>
    <div class="section-body single">
      <div>
        <p style="font-size:11px;color:var(--text-dim);letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px;">Architecture</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px;">
          <button type="button" class="arch-card selected" data-arch="s390x" onclick="onArchChange(this)">
            <span style="font-size:16px;">⬡</span>
            <span class="arch-card-title">IBM Z — s390x</span>
            <span class="arch-card-sub">LPAR / KVM / ZD&amp;T</span>
          </button>
          <button type="button" class="arch-card" data-arch="x86_64" onclick="onArchChange(this)">
            <span style="font-size:16px;">□</span>
            <span class="arch-card-title">x86_64</span>
            <span class="arch-card-sub">PC / VM / cloud</span>
          </button>
          <button type="button" class="arch-card" data-arch="aarch64" onclick="onArchChange(this)">
            <span style="font-size:16px;">◇</span>
            <span class="arch-card-title">aarch64</span>
            <span class="arch-card-sub">ARM64 / cloud</span>
          </button>
        </div>
        <input type="hidden" name="arch" id="arch-val" value="s390x">
        <p style="font-size:11px;color:var(--text-dim);letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px;">Output format</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button type="button" class="fmt-pill selected" data-fmt="raw" onclick="onFmtChange(this)">RAW — block device</button>
          <button type="button" class="fmt-pill" data-fmt="qcow2" onclick="onFmtChange(this)">QCOW2 — KVM / ZD&amp;T</button>
          <button type="button" class="fmt-pill" data-fmt="vmdk" onclick="onFmtChange(this)">VMDK — VMware</button>
          <button type="button" class="fmt-pill" data-fmt="iso" onclick="onFmtChange(this)">ISO — bootable</button>
        </div>
        <input type="hidden" name="output_format" id="fmt-val" value="raw">
        <div class="warn" id="fmt-non-raw-note" style="display:none;margin-top:14px;">
          Non-RAW formats produce an image file only — no block device deploy step is generated. Transfer or import the output file into your target environment.
        </div>
      </div>
    </div>
  </div>

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

  <!-- ── 02 Storage (s390x) ── -->
  <div class="section" id="s390x-storage">
    <div class="section-header">
      <span class="section-num">02</span>
      <span class="section-title">DASD Storage</span>
      <span style="font-size:10px;color:var(--green-dim);margin-left:auto;letter-spacing:.08em;">IBM Z ONLY</span>
    </div>
    <div class="section-body">
      <div class="field">
        <label>Boot DASD address</label>
        <input type="text" name="boot_dasd" value="0.0.0200"
               pattern="[0-9a-fA-F]\.[0-9a-fA-F]\.[0-9a-fA-F]{4}"
               id="boot_dasd" placeholder="0.0.0200">
        <span class="hint">Baked into dasd.conf / zipl.conf and the Phase B deploy snippet — the DASD the OS boots from</span>
      </div>
      <div class="field">
        <label>DD target DASD device</label>
        <input type="text" name="dd_dasd" value="/dev/dasda"
               id="dd_dasd" placeholder="/dev/dasda">
        <span class="hint">Where you'll <code>dd</code> the RAW in Phase B on the Z host (e.g. /dev/dasda)</span>
      </div>
      <div class="field">
        <label>Storage layout</label>
        <select name="storage_layout" id="storage_layout">
          <option value="lvm">LVM on DASD (root + /var LVs)</option>
          <option value="single">Single XFS root (no LVM)</option>
        </select>
      </div>
      <div class="field" id="vg-name-field">
        <label>LVM volume group name</label>
        <input type="text" name="vg_name" value="rhelvg" id="vg_name" placeholder="rhelvg">
        <span class="hint">Used in fstab and zipl kernel parameters</span>
      </div>
      <div class="warn">
        These addresses configure the image and the Phase B deploy snippet — nothing is written to a DASD on this build host. On the Z host, <code>dasdfmt</code> is destructive: it fully erases the target DASD.
      </div>
    </div>
  </div>

  <!-- ── 02 Storage (non-s390x) ── -->
  <div class="section" id="generic-storage" style="display:none;">
    <div class="section-header">
      <span class="section-num">02</span>
      <span class="section-title">Storage</span>
    </div>
    <div class="section-body">
      <div class="field">
        <label>Target block device</label>
        <input type="text" name="target_disk" value="/dev/sda"
               placeholder="/dev/sda">
        <span class="hint">Block device to dd the RAW image to (for RAW output only)</span>
      </div>
      <div class="field">
        <label>Storage layout</label>
        <select name="storage_layout_x86" id="storage_layout_x86">
          <option value="single">Single XFS root (no LVM)</option>
        </select>
        <span class="hint">LVM on x86/aarch64 requires a second disk — coming in a future release</span>
      </div>
    </div>
  </div>

  <!-- ── 03 Network (s390x) ── -->
  <div class="section" id="s390x-network">
    <div class="section-header">
      <span class="section-num">03</span>
      <span class="section-title">qeth Network</span>
      <span style="font-size:10px;color:var(--green-dim);margin-left:auto;letter-spacing:.08em;">IBM Z ONLY</span>
    </div>
    <div class="section-body triple">
      <div class="field">
        <label>qeth base channel</label>
        <input type="text" name="qeth_channel" value="0.0.0600"
               pattern="[0-9a-fA-F]\.[0-9a-fA-F]\.[0-9a-fA-F]{4}"
               placeholder="0.0.0600">
        <span class="hint">Channels 0600, 0601, 0602 will be used</span>
      </div>
      <div class="field">
        <label>Interface name</label>
        <input type="text" name="iface" value="enc600"
               placeholder="enc600">
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

  <!-- ── 03 Network (non-s390x) ── -->
  <div class="section" id="generic-network" style="display:none;">
    <div class="section-header">
      <span class="section-num">03</span>
      <span class="section-title">Network</span>
    </div>
    <div class="section-body">
      <div class="field">
        <label>Network interface name</label>
        <input type="text" name="nic" value="eth0" placeholder="eth0">
        <span class="hint">NM connection interface-name (e.g. eth0, ens3, enp0s3)</span>
      </div>
      <div class="field">
        <label>IP configuration</label>
        <select name="ip_method_x86">
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
          <div class="toggle-hint" id="fips-hint">Adds <code>fips=1</code> to kernel parameters, installs <code>crypto-policies-scripts</code>, adds the <code>fips</code> dracut module, and runs <code>update-crypto-policies --set FIPS</code> at build time</div>
        </div>
      </div>
      <div class="warn" id="fips-warn" style="display:none;">
        FIPS restricts allowed algorithms and key sizes. Confirm your SSH key type is FIPS-compatible (RSA ≥ 2048 or ed25519). A full re-IPL is required after first boot to activate the FIPS kernel parameter.
      </div>
    </div>
  </div>

  <div class="generate-wrap" style="gap:14px;">
    <button type="submit">&#x25B6;&nbsp; Generate Script</button>
    <button type="button" class="build-now" id="build-btn" onclick="buildNow()">⚙&nbsp; Build Image</button>
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

<!-- ── Build terminal ── -->
<div class="output-wrap" id="build-wrap">
  <div class="output-header">
    <span class="output-title">Build Output — Phase A</span>
    <span class="build-status" id="build-status"></span>
  </div>
  <pre class="output-script" id="build-out" style="white-space:pre-wrap;"></pre>
  <div class="deliverable" id="deliverable" style="display:none;">
    <a class="download-btn" id="download-link" href="#" download>&#x2B07; Download image</a>
    <div class="toggle-hint" style="line-height:1.6;">
      <strong style="color:var(--text);">Phase B — deploy on the Z host:</strong>
      copy this image to a Linux-on-Z host with the DASD attached, then
      <code>dasdfmt</code> → <code>fdasd</code> → <code>dd</code> → <code>zipl</code>.
      The exact commands (with your DASD addresses) are in the generated script's
      <code>PHASE B</code> block — click <em>Generate Script</em> to see them.
    </div>
  </div>
</div>

</div><!-- /container -->

<footer>
  <span>RHEL 10 · Image Mode Studio</span>
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

function onArchChange(btn) {
  // Update card selection
  document.querySelectorAll('.arch-card').forEach(function(c) { c.classList.remove('selected'); });
  btn.classList.add('selected');
  var arch = btn.dataset.arch;
  document.getElementById('arch-val').value = arch;

  var isS390x = (arch === 's390x');

  // Show/hide storage and network sections
  document.getElementById('s390x-storage').style.display  = isS390x ? '' : 'none';
  document.getElementById('generic-storage').style.display = isS390x ? 'none' : '';
  document.getElementById('s390x-network').style.display   = isS390x ? '' : 'none';
  document.getElementById('generic-network').style.display  = isS390x ? 'none' : '';

  // Limit format choices for s390x (vmdk not useful on IBM Z)
  document.querySelectorAll('.fmt-pill').forEach(function(p) {
    var fmt = p.dataset.fmt;
    if (!isS390x && fmt === 'raw') {
      // raw is always available
    }
    // vmdk is fine for all arches with bootc-image-builder
  });
}

function onFmtChange(btn) {
  document.querySelectorAll('.fmt-pill').forEach(function(p) { p.classList.remove('selected'); });
  btn.classList.add('selected');
  var fmt = btn.dataset.fmt;
  document.getElementById('fmt-val').value = fmt;
  var isRaw = (fmt === 'raw');
  document.getElementById('fmt-non-raw-note').style.display = isRaw ? 'none' : 'block';
  // Hide target-disk section for non-raw (nothing to dd)
  var genStor = document.getElementById('generic-storage');
  if (genStor.style.display !== 'none') {
    genStor.style.display = isRaw ? '' : 'none';
  }
}

function onFipsChange(el) {
  document.getElementById('fips-warn').style.display = el.checked ? 'block' : 'none';
}

function onSelinuxChange(el) {
  document.getElementById('selinux-warn').style.display = el.value === 'disabled' ? 'block' : 'none';
}

async function runPreflight() {
  var btn = document.getElementById('preflight-btn');
  var results = document.getElementById('preflight-results');
  btn.textContent = '[ checking... ]';
  btn.disabled = true;
  try {
    var res = await fetch('/preflight');
    var checks = await res.json();
    results.innerHTML = checks.map(function(c) {
      return '<div class="preflight-item ' + (c.ok ? 'ok' : 'fail') + '">' +
        '<span class="preflight-icon">' + (c.ok ? '✓' : '✗') + '</span>' +
        '<span class="preflight-name">' + c.name + '</span>' +
        '<span class="preflight-detail">' + c.detail + '</span>' +
        '</div>';
    }).join('');
    results.style.display = 'block';
  } catch(e) {
    results.innerHTML = '<div class="preflight-item fail">' +
      '<span class="preflight-icon">✗</span>' +
      '<span class="preflight-name">request failed</span>' +
      '<span class="preflight-detail">' + e + '</span></div>';
    results.style.display = 'block';
  }
  btn.textContent = '[ run checks ]';
  btn.disabled = false;
}

async function buildNow() {
  var btn = document.getElementById('build-btn');
  var statusEl = document.getElementById('build-status');
  var outEl = document.getElementById('build-out');
  var wrap = document.getElementById('build-wrap');

  btn.disabled = true;
  btn.textContent = '⚙ Starting...';

  var params = new URLSearchParams(new FormData(document.getElementById('form'))).toString();
  var deliverable = document.getElementById('deliverable');
  deliverable.style.display = 'none';

  try {
    var res = await fetch('/build', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: params,
    });
    var data = await res.json();

    wrap.classList.add('visible');
    outEl.textContent = '';
    statusEl.className = 'build-status running';
    statusEl.textContent = 'running...';
    wrap.scrollIntoView({behavior: 'smooth', block: 'start'});

    var es = new EventSource('/stream/' + data.job_id);
    es.onmessage = function(e) {
      var msg = JSON.parse(e.data);
      if (msg.done) {
        es.close();
        if (msg.rc === 0) {
          statusEl.className = 'build-status done';
          statusEl.textContent = '✓ complete';
          if (msg.artifact) {
            var link = document.getElementById('download-link');
            link.href = '/download/' + data.job_id;
            link.textContent = '⬇ Download ' + (msg.artifact_name || 'image');
            deliverable.style.display = 'flex';
          }
        } else {
          statusEl.className = 'build-status failed';
          statusEl.textContent = '✗ failed (rc=' + msg.rc + ')';
        }
        btn.disabled = false;
        btn.textContent = '⚙ Build Image';
      } else {
        outEl.textContent += msg.line + '\n';
        outEl.scrollTop = outEl.scrollHeight;
      }
    };
    es.onerror = function() {
      es.close();
      statusEl.className = 'build-status failed';
      statusEl.textContent = '✗ stream disconnected';
      btn.disabled = false;
      btn.textContent = '⚙ Build Image';
    };
  } catch(e) {
    statusEl.className = 'build-status failed';
    statusEl.textContent = '✗ ' + e;
    btn.disabled = false;
    btn.textContent = '⚙ Build Image';
  }
}

// ── Build engine (Infrastructure Automation) ──
async function refreshEngineBadge() {
  var badge = document.getElementById('engine-badge');
  try {
    var checks = await (await fetch('/preflight')).json();
    var find = function(n) { var c = checks.find(function(x){return x.name===n;}); return c ? c.detail : ''; };
    var engine = (find('container engine').split(' ')[0]) || '?';
    var hostArch = (find('host architecture').split(' ')[0]) || '?';
    var native = (hostArch === 's390x');
    badge.classList.remove('native', 'cross');
    if (native) {
      badge.classList.add('native');
      badge.textContent = engine + ' · native ' + hostArch;
    } else {
      badge.classList.add('cross');
      var emu = find('QEMU s390x emulation');
      var emuOk = emu.indexOf('registered') === 0;
      badge.textContent = engine + ' · cross · qemu ' + (emuOk ? '✓' : '✗');
    }
  } catch(e) {
    badge.textContent = 'unknown';
  }
}

async function prepareEngine() {
  var btn = document.getElementById('engine-btn');
  var out = document.getElementById('engine-out');
  var statusEl = document.getElementById('engine-status');
  btn.disabled = true;
  btn.textContent = '[ preparing... ]';
  out.style.display = 'block';
  out.textContent = '';
  statusEl.className = 'build-status running';
  statusEl.textContent = 'running...';

  var arch = document.getElementById('arch-val').value;
  try {
    var res = await fetch('/engine/prepare', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: 'arch=' + encodeURIComponent(arch),
    });
    var data = await res.json();
    var es = new EventSource('/stream/' + data.job_id);
    es.onmessage = function(e) {
      var msg = JSON.parse(e.data);
      if (msg.done) {
        es.close();
        if (msg.rc === 0) {
          statusEl.className = 'build-status done';
          statusEl.textContent = '✓ engine ready';
        } else {
          statusEl.className = 'build-status failed';
          statusEl.textContent = '✗ prep failed (rc=' + msg.rc + ')';
        }
        btn.disabled = false;
        btn.textContent = '[ prepare build engine ]';
        refreshEngineBadge();
      } else {
        out.textContent += msg.line + '\n';
        out.scrollTop = out.scrollHeight;
      }
    };
    es.onerror = function() {
      es.close();
      statusEl.className = 'build-status failed';
      statusEl.textContent = '✗ stream disconnected';
      btn.disabled = false;
      btn.textContent = '[ prepare build engine ]';
    };
  } catch(e) {
    statusEl.className = 'build-status failed';
    statusEl.textContent = '✗ ' + e;
    btn.disabled = false;
    btn.textContent = '[ prepare build engine ]';
  }
}

// Populate the engine badge on load.
refreshEngineBadge();
</script>

</body>
</html>
"""

# ── Script generator ──────────────────────────────────────────────────────────

def generate_script(p):
    # ── Parameters ────────────────────────────────────────────────────────────
    admin_user    = p.get('admin_user',    ['bootcadmin'])[0].strip()
    ssh_pubkey    = p.get('ssh_pubkey',    [''])[0].strip()
    arch          = p.get('arch',          ['s390x'])[0].strip()
    output_format = p.get('output_format', ['raw'])[0].strip()
    # s390x-specific
    boot_dasd     = p.get('boot_dasd',     ['0.0.0200'])[0].strip()
    dd_dasd       = p.get('dd_dasd',       ['/dev/dasda'])[0].strip()
    qeth_channel  = p.get('qeth_channel',  ['0.0.0600'])[0].strip()
    iface         = p.get('iface',         ['enc600'])[0].strip()
    ip_method     = p.get('ip_method',     ['dhcp'])[0].strip()
    storage       = p.get('storage_layout',['lvm'])[0].strip()
    vg_name       = p.get('vg_name',       ['rhelvg'])[0].strip()
    # non-s390x
    target_disk   = p.get('target_disk',   ['/dev/sda'])[0].strip()
    nic           = p.get('nic',           ['eth0'])[0].strip()
    ip_method_x86 = p.get('ip_method_x86', ['dhcp'])[0].strip()
    # shared build options
    image_name    = p.get('image_name',    ['rhel10-bootc'])[0].strip()
    image_tag     = p.get('image_tag',     ['latest'])[0].strip()
    output_dir    = p.get('output_dir',    ['/var/tmp/bootc-output'])[0].strip()
    proxy         = p.get('proxy',         [''])[0].strip()
    selinux_mode  = p.get('selinux_mode',  ['permissive'])[0].strip()
    fips          = p.get('fips',          ['off'])[0].strip()

    is_s390x = (arch == 's390x')
    is_raw   = (output_format == 'raw')

    # Which engine / build mode is this host? Determines cross-compile plumbing.
    engine = detect_engine() or 'podman'
    mode   = build_mode_for(arch)          # 'native' or 'cross'
    is_cross = (mode == 'cross')

    builder_img = "registry.redhat.io/rhel10/bootc-image-builder:latest"
    base_img    = "registry.redhat.io/rhel10/rhel-bootc:latest"

    proxy_block = ""
    if proxy:
        proxy_block = f"""
export http_proxy="{proxy}"
export https_proxy="{proxy}"
export no_proxy="localhost,127.0.0.1,registry.redhat.io"
"""

    fips_param = " fips=1" if fips == "on" else ""

    # ── Arch-specific: packages ────────────────────────────────────────────────
    pkgs = [
        "      openssh-server", "      vim", "      curl", "      chrony",
        "      rsyslog", "      policycoreutils", "      dracut",
        "      NetworkManager", "      util-linux",
    ]
    if is_s390x:
        pkgs += ["      s390utils-base", "      zipl"]
    elif arch == 'x86_64':
        pkgs += ["      grub2", "      grub2-pc", "      grub2-efi-x64", "      grub2-efi-x64-modules"]
    else:  # aarch64
        pkgs += ["      grub2-efi-aa64", "      grub2-efi-aa64-modules"]
    if is_s390x and storage == "lvm":
        pkgs.append("      lvm2")
    if fips == "on":
        pkgs.append("      crypto-policies-scripts")
    pkgs.append("      qemu-guest-agent")
    pkg_install_lines = " \\\n".join(pkgs) + " \\"

    fips_policy_block = "\n# FIPS crypto policy\nRUN update-crypto-policies --set FIPS" if fips == "on" else ""

    # ── Arch-specific: dracut config ───────────────────────────────────────────
    dracut_s390x_line = 'add_drivers+=" dasd_mod dasd_eckd_mod qdio qeth qeth_l2 zfcp "' if is_s390x else ''
    dracut_lvm_line   = 'add_dracutmodules+=" lvm "'  if (is_s390x and storage == "lvm") else ''
    dracut_fips_line  = 'add_dracutmodules+=" fips "' if fips == "on" else ''

    # ── Arch-specific: network ─────────────────────────────────────────────────
    if is_s390x:
        net_filename = 'qeth0.nmconnection'
        net_id       = 'qeth0'
        net_iface    = iface
        net_ipmethod = ip_method
    else:
        net_filename = 'eth0.nmconnection'
        net_id       = 'eth0'
        net_iface    = nic
        net_ipmethod = ip_method_x86

    # ── Arch-specific: bootloader / DASD files ─────────────────────────────────
    if is_s390x:
        if storage == 'lvm':
            zipl_params = (f"root=/dev/{vg_name}/root rd.dasd={boot_dasd} "
                           f"rd.lvm.lv={vg_name}/root rd.lvm.lv={vg_name}/var "
                           f"rd.net=qeth,{qeth_channel},layer2=1{fips_param}")
        else:
            zipl_params = (f"root=LABEL=rootfs rd.dasd={boot_dasd} "
                           f"rd.net=qeth,{qeth_channel},layer2=1{fips_param}")

        dasd_write_step = (f'log "Writing dasd.conf..."\n'
                           f"printf '{boot_dasd} 1\\n' > \"${{BUILD_CTX}}/dasd.conf\"")
        zipl_write_step = f"""log "Writing zipl/zipl.conf..."
cat > "${{BUILD_CTX}}/zipl/zipl.conf" << 'EOF'
[defaultboot]
default = linux

[linux]
target = /boot
kernel = /boot/vmlinuz
ramdisk = /boot/initramfs.img
parameters = "{zipl_params}"
EOF"""
        dasd_cf_copy = "COPY dasd.conf /etc/dasd.conf"
        zipl_cf_copy = "COPY zipl/zipl.conf /etc/zipl.conf"
    else:
        dasd_write_step = ''
        zipl_write_step = ''
        dasd_cf_copy    = ''
        zipl_cf_copy    = ''

    # ── Storage: LVM firstboot (s390x only) ────────────────────────────────────
    if is_s390x and storage == 'lvm':
        fstab_content = f"/dev/{vg_name}/root   /       xfs  defaults  0 0\\n/dev/{vg_name}/var    /var    xfs  defaults  0 0"
        firstboot_section = f"""
# ─────────────────────────────────────────────────────────────────────────────
# STEP 2b · Write firstboot-lvm.sh
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
        firstboot_containerfile = """# First-boot LVM automation
COPY scripts/firstboot-lvm.sh /usr/local/sbin/firstboot-lvm.sh
COPY systemd/firstboot-lvm.service /etc/systemd/system/firstboot-lvm.service
RUN chmod 0755 /usr/local/sbin/firstboot-lvm.sh \\\\
    && systemctl enable firstboot-lvm.service"""
    else:
        fstab_content = "LABEL=rootfs / xfs defaults 0 0"
        firstboot_section = ""
        firstboot_containerfile = "# Single XFS root — no firstboot LVM service"

    # ── Phase A ends at a downloadable image ───────────────────────────────────
    # This host builds and produces the image file only — it never writes to a
    # physical device (you cannot dd to a DASD from a build/Windows host). The
    # dasdfmt/fdasd/dd/zipl write is Phase B, run manually on the Z host below.
    deploy_section = ""

    done_block = f"""echo ""
echo -e "${{GRN}}══════════════════════════════════════════════════════${{NC}}"
echo -e "${{GRN}}  {output_format.upper()} image ready — Phase A complete${{NC}}"
echo -e "${{GRN}}══════════════════════════════════════════════════════${{NC}}"
echo ""
echo "  Output : $OUTPUT_IMAGE"
echo "  Size   : $(du -sh "$OUTPUT_IMAGE" | cut -f1)"
echo "ARTIFACT_PATH=$OUTPUT_IMAGE"
echo ""
echo "  Download the image from the web UI, then deploy on the Z host (Phase B)."
echo ""
"""

    # ── Phase B reference (NOT executed here) ──────────────────────────────────
    if is_s390x and is_raw:
        phase_b_snippet = f"""
# ═══════════════════════════════════════════════════════════════════════════
# PHASE B — DEPLOY ON THE IBM Z HOST  (run manually where the DASD is attached)
# ═══════════════════════════════════════════════════════════════════════════
# Not run here. Copy the RAW image to your Linux-on-Z host, then, as root:
#
#   BOOT_DASD={boot_dasd}
#   DD_TARGET={dd_dasd}
#   RAW=/path/to/{image_name}.raw
#
#   chccwdev -e "$BOOT_DASD"
#   dasdfmt -b 4096 -d cdl -y "$DD_TARGET"          # DESTRUCTIVE — erases the DASD
#   fdasd -a "$DD_TARGET"
#   dd if="$RAW" of="$DD_TARGET" bs=64M status=progress && sync
#   mount "${{DD_TARGET}}1" /mnt && zipl -V -t /mnt && umount /mnt
#   # IPL from the HMC:  Load → Normal → {boot_dasd.split('.')[-1]}
"""
    elif is_raw:
        phase_b_snippet = f"""
# ═══════════════════════════════════════════════════════════════════════════
# PHASE B — WRITE TO TARGET DISK  (run manually on the target host)
# ═══════════════════════════════════════════════════════════════════════════
#   RAW=/path/to/{image_name}.raw
#   dd if="$RAW" of={target_disk} bs=64M status=progress && sync
"""
    else:
        phase_b_snippet = ""

    # ── Preflight (build host produces a file — no target device needed) ───────
    preflight_disk_check = ('log "Build host produces an image file — '
                            'no target block device required here (see Phase B)"')

    # ── Script-level vars ──────────────────────────────────────────────────────
    arch_vars = ''

    # ── STEP 4/5 engine plumbing (docker buildx vs podman; native vs cross) ────
    # Entitlement certs only exist on a subscribed RHEL host (native mode).
    ent_mounts = ""
    if not is_cross:
        ent_mounts = (
            "    --volume /etc/pki/entitlement:/etc/pki/entitlement:ro \\\n"
            "    --volume /etc/rhsm:/etc/rhsm:ro \\\n"
            "    --volume /etc/yum.repos.d/redhat.repo:/etc/yum.repos.d/redhat.repo:ro \\\n"
        )

    if engine == 'docker':
        build_note = ('log "Cross-compiling linux/{a} under QEMU via buildx — host RHEL '
                      'entitlements not mounted; relying on base-image content + registry login"'
                      ).format(a=arch) if is_cross else 'true'
        build_step = f"""{build_note}
docker buildx build \\
    --builder {BUILDX_BUILDER_NAME} \\
    --platform linux/{arch} \\
    --load \\
    -t "$FULL_IMAGE" \\
    -f "${{BUILD_CTX}}/Containerfile" \\
    "$BUILD_CTX\""""
    else:  # podman
        build_step = f"""podman build \\
    --platform linux/{arch} \\
    --tls-verify=false \\
{ent_mounts}    --network=host \\
    -t "$FULL_IMAGE" \\
    -f "${{BUILD_CTX}}/Containerfile" \\
    "$BUILD_CTX\""""

    # bootc-image-builder: podman reads the freshly built image from local storage.
    containers_mount = ("    -v /var/lib/containers:/var/lib/containers \\\n"
                        if engine == 'podman' else "")
    bib_note = ('warn "bootc-image-builder is best supported under podman; under docker the '
                'target image must be in containers-storage or pushed to a registry first"'
                if engine == 'docker' else 'true')
    imagebuilder_step = f"""{bib_note}
"$ENGINE" run --rm \\
    --privileged \\
    --security-opt seccomp=unconfined \\
{containers_mount}    -v "${{OUTPUT_DIR}}:/output" \\
    "$BUILDER_IMAGE" \\
    --type {output_format} \\
    --target-arch {arch} \\
    "$FULL_IMAGE\""""

    storage_label = (f"LVM on {'DASD' if is_s390x else 'disk'} (VG: {vg_name})"
                     if (is_s390x and storage == 'lvm') else "Single XFS root")

    # ── Assemble script ─────────────────────────────────────────────────────────
    script = f"""#!/bin/bash
# =============================================================================
# build-and-deploy.sh  —  Generated by bootc-builder-server
#
# Arch        : {arch}
# Output      : {output_format.upper()}
# Admin user  : {admin_user}
# Storage     : {storage_label}
# SELinux     : {selinux_mode}
# FIPS        : {"enabled" if fips == "on" else "disabled"}
{'# Boot DASD   : ' + boot_dasd + chr(10) + '# qeth channel: ' + qeth_channel + ' / ' + iface if is_s390x else '# Target disk : ' + target_disk + chr(10) + '# NIC         : ' + nic}
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
ENGINE="{engine}"
BUILD_MODE="{mode}"          # native | cross (cross = QEMU-emulated s390x build)
{arch_vars}
{proxy_block}
# ─────────────────────────────────────────────────────────────────────────────
# STEP 0 · Pre-flight checks
# ─────────────────────────────────────────────────────────────────────────────
step "Pre-flight checks ($ENGINE, $BUILD_MODE build of linux/{arch})"
command -v "$ENGINE" >/dev/null 2>&1 || err "$ENGINE not found — install it first"
[ "$(id -u)" -eq 0 ] || err "This script must run as root (or with sudo)"
{preflight_disk_check}
mkdir -p "$BUILD_CTX/dracut" "$BUILD_CTX/network" "$BUILD_CTX/ssh" \\
         "$BUILD_CTX/zipl" "$BUILD_CTX/scripts" "$BUILD_CTX/systemd" \\
         "$BUILD_CTX/rpms" "$OUTPUT_DIR"
log "Pre-flight OK"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 · Registry login
# ─────────────────────────────────────────────────────────────────────────────
step "Registry login"
log "Logging in to registry.redhat.io..."
"$ENGINE" login registry.redhat.io || err "Registry login failed"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 · Write build context files
# ─────────────────────────────────────────────────────────────────────────────
step "Writing build context to $BUILD_CTX"

log "Writing dracut/bootc.conf..."
cat > "${{BUILD_CTX}}/dracut/bootc.conf" << 'EOF'
{dracut_s390x_line}
{dracut_lvm_line}
{dracut_fips_line}
hostonly="no"
omit_drivers+=" floppy "
EOF

log "Writing network/{net_filename}..."
cat > "${{BUILD_CTX}}/network/{net_filename}" << 'EOF'
[connection]
id={net_id}
type=ethernet
interface-name={net_iface}
autoconnect=true

[ipv4]
method={net_ipmethod}

[ipv6]
method=ignore
EOF
chmod 600 "${{BUILD_CTX}}/network/{net_filename}"

log "Writing fstab..."
printf '{fstab_content}\\n' > "${{BUILD_CTX}}/fstab"

{dasd_write_step}

{zipl_write_step}

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

# Dracut / initramfs config
COPY dracut/bootc.conf /etc/dracut.conf.d/bootc.conf
COPY fstab /etc/fstab
COPY network/{net_filename} /etc/NetworkManager/system-connections/{net_filename}
RUN chmod 600 /etc/NetworkManager/system-connections/{net_filename}
{dasd_cf_copy}
{zipl_cf_copy}

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
# STEP 4 · Build container image ({engine}, {mode} build of linux/{arch})
# ─────────────────────────────────────────────────────────────────────────────
step "Building container image: $FULL_IMAGE"
{build_step}
log "Container image built: $FULL_IMAGE"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 · bootc-image-builder → {output_format.upper()} image
# ─────────────────────────────────────────────────────────────────────────────
step "Running bootc-image-builder → {output_format.upper()}"
{imagebuilder_step}

OUTPUT_IMAGE=$(find "$OUTPUT_DIR" -name "*.{output_format}" | head -1)
[ -f "$OUTPUT_IMAGE" ] || err "{output_format.upper()} image not found in $OUTPUT_DIR"
log "{output_format.upper()} image: $OUTPUT_IMAGE ($(du -sh "$OUTPUT_IMAGE" | cut -f1))"
{deploy_section}
# ─────────────────────────────────────────────────────────────────────────────
# DONE
# ─────────────────────────────────────────────────────────────────────────────
{done_block}{phase_b_snippet}"""
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
        path = urlparse(self.path).path
        if path in ('/', '/index.html'):
            self.send_page()
        elif path == '/preflight':
            self.handle_preflight()
        elif path.startswith('/stream/'):
            self.handle_stream(path[len('/stream/'):])
        elif path.startswith('/download/'):
            self.handle_download(path[len('/download/'):])
        else:
            self.send_error(404)

    def do_POST(self):
        path   = urlparse(self.path).path
        length = int(self.headers.get('Content-Length', 0))
        raw    = self.rfile.read(length).decode('utf-8')
        params = parse_qs(raw)
        if path == '/generate':
            self.send_page(generate_script(params))
        elif path == '/build':
            job_id = uuid.uuid4().hex[:8]
            out_dir = os.path.join(OUTPUT_ROOT, job_id)
            fmt = params.get('output_format', ['raw'])[0].strip()
            # Isolate each build's artifacts so downloads never collide.
            params['output_dir'] = [out_dir]
            script = generate_script(params)
            jobs[job_id] = {
                'lines': [], 'done': False, 'rc': None, 'lock': threading.Lock(),
                'artifact': None, 'artifact_name': None,
            }
            threading.Thread(
                target=run_build_job, args=(job_id, script, out_dir, fmt), daemon=True,
            ).start()
            self._send_json({'job_id': job_id})
        elif path == '/engine/prepare':
            arch = params.get('arch', ['s390x'])[0].strip()
            job_id = uuid.uuid4().hex[:8]
            jobs[job_id] = {
                'lines': [], 'done': False, 'rc': None, 'lock': threading.Lock(),
                'artifact': None, 'artifact_name': None,
            }
            threading.Thread(
                target=run_engine_job, args=(job_id, arch), daemon=True,
            ).start()
            self._send_json({'job_id': job_id})
        else:
            self.send_error(404)

    def _send_json(self, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_preflight(self):
        body = json.dumps(run_preflight()).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_stream(self, job_id):
        if job_id not in jobs:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        job  = jobs[job_id]
        sent = 0
        try:
            while True:
                with job['lock']:
                    lines = list(job['lines'])
                    done  = job['done']
                    rc    = job['rc']
                    artifact_name = job.get('artifact_name')
                while sent < len(lines):
                    self.wfile.write(f'data: {json.dumps({"line": lines[sent]})}\n\n'.encode())
                    self.wfile.flush()
                    sent += 1
                if done:
                    payload = {"done": True, "rc": rc,
                               "artifact": bool(artifact_name),
                               "artifact_name": artifact_name}
                    self.wfile.write(f'data: {json.dumps(payload)}\n\n'.encode())
                    self.wfile.flush()
                    break
                time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected

    def handle_download(self, job_id):
        job = jobs.get(job_id)
        if not job:
            self.send_error(404, "Unknown job")
            return
        with job['lock']:
            artifact = job.get('artifact')
            name = job.get('artifact_name') or 'image.raw'
        if not artifact or not os.path.isfile(artifact):
            self.send_error(404, "No artifact for this job")
            return
        size = os.path.getsize(artifact)
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Disposition', f'attachment; filename="{name}"')
        self.send_header('Content-Length', str(size))
        self.end_headers()
        try:
            with open(artifact, 'rb') as fh:
                while True:
                    chunk = fh.read(1024 * 1024)  # 1 MiB — never load the whole RAW
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client cancelled the download


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    host = '0.0.0.0'
    print(f"\n  RHEL 10 · Image Mode Studio (s390x multi-arch)")
    print(f"  ───────────────────────────────────────────────")
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
        ThreadingHTTPServer((host, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        sys.exit(0)

# Windows / Docker Desktop — Phase A Validation Runbook

> **Goal:** prove the full **Phase A** path on a Windows host end-to-end — prepare the
> cross-compile engine → build a RHEL 10 **s390x** image under QEMU → produce a **RAW**
> → download it from the browser. Phase B (writing to DASD on the Z host) is covered in
> [`Deploy_Guide.md`](./Deploy_Guide.md).
>
> This is the one path the project's other environments can't exercise. Two things are
> being validated here: (1) does `bootc-image-builder` produce an s390x RAW under Docker
> Desktop's QEMU, and (2) what **entitlement strategy** lets RHEL-CDN packages install
> during a cross-build on a non-RHEL host. Read [§2](#2-the-entitlement-decision-do-this-first) first.

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| **Docker Desktop** (WSL2 backend) | Settings → General → *Use the WSL 2 based engine*. Linux containers, not Windows containers. |
| **WSL2 distro** (Ubuntu recommended) | `wsl --install -d Ubuntu`. Enable it in Docker Desktop → Settings → *Resources → WSL Integration*. |
| **Python 3** | Inside WSL: `sudo apt install -y python3`. (Windows Python also works for the UI, but not for the in-browser build — see §3.) |
| **Red Hat registry login** | A registry service account from <https://access.redhat.com/terms-based-registry/>. Gives a username + token for `registry.redhat.io`. |
| **Disk + patience** | A RAW image is multi-GB and a QEMU-emulated s390x build is **much slower** than native. Budget 20–40+ min and several GB free in the WSL VM. |

Quick sanity check inside WSL:

```bash
docker version           # client + server (Docker Desktop) both respond
docker buildx version    # buildx present
uname -m                 # x86_64 — confirms this is a cross-build
```

---

## 2. The entitlement decision (do this FIRST)

The Studio deliberately **does not** mount host entitlement certs in cross mode — a Windows
host doesn't have them. But the default image installs packages (`s390utils-base`, `zipl`,
`lvm2`, …) that come from the **RHEL CDN**, which requires entitlement. So a real build will
hit this. Pick a strategy before you start:

| # | Strategy | When to use | Trade-off |
|---|---|---|---|
| **A** | **Mount entitlement certs copied from a subscribed RHEL box** | You have any subscribed RHEL system to copy from | Most faithful to production; a few manual steps (below) |
| **B** | **Build a minimal image** (only packages already in `rhel-bootc`) | Pure smoke-test of the QEMU/buildx/RAW pipeline | Not a deployable image — trims the package list |
| **C** | **Run the cross-build on a subscribed RHEL x86 VM instead** | You have a RHEL VM handy | Still QEMU cross-compile, but entitlements are native — least friction |

**Strategy A — copy entitlements into the build (recommended for a real validation):**

On a subscribed RHEL system:
```bash
sudo tar czf entitlement.tgz /etc/pki/entitlement /etc/rhsm /etc/yum.repos.d/redhat.repo
```
Copy `entitlement.tgz` into WSL and extract to the same paths (`sudo tar xzf entitlement.tgz -C /`).
Then add the mounts back for this validation — either run the Studio host in **native-style**
by temporarily setting the mounts, or **Generate Script** and add these three flags to the
`docker buildx build` / `podman build` invocation:
```
    --volume /etc/pki/entitlement:/etc/pki/entitlement:ro \
    --volume /etc/rhsm:/etc/rhsm:ro \
    --volume /etc/yum.repos.d/redhat.repo:/etc/yum.repos.d/redhat.repo:ro \
```
> Note: `docker buildx` doesn't mount `--volume` for RUN layers the way `podman build` does.
> If you're on the docker path and CDN installs fail, prefer **Strategy C** (podman on a
> subscribed RHEL VM) — that path mounts entitlements cleanly. This docker-entitlement gap is
> exactly what this runbook is meant to pin down.

**Strategy B — minimal image:** in the Studio form, keep only base packages and skip
`s390utils-base`/`zipl`/`lvm2` for the smoke test. The image won't boot on Z, but it proves
the engine → RAW → download chain works.

---

## 3. Two ways to run the Studio on Windows

The in-browser **Build Image** button runs a **bash** script that calls the container engine.
Native Windows Python has no `bash`, so:

### Path A — Studio inside WSL2 (recommended: full build works)

Everything (Python, bash, docker CLI → Docker Desktop) lives in one place, so the built RAW
and the download endpoint agree on paths.

```bash
# inside WSL (Ubuntu)
cd /mnt/c/Users/<you>/git/RHEL10-s390x     # or clone into the WSL filesystem for speed
docker login registry.redhat.io
sudo ./studio.sh start                      # root satisfies the script's id-u check
./studio.sh status
```
Open `http://localhost:8080` in your Windows browser (WSL forwards localhost).

> The generated script enforces `id -u == 0`. Running the Studio with `sudo` (as above) makes
> the in-browser build pass that check. If you'd rather not run the server as root, use
> **Generate Script** and run it yourself with `sudo`.

### Path B — Studio via PowerShell (UI + prepare-engine + generate only)

`studio.ps1` runs the server under Windows Python. The **UI, pre-flight, Prepare Build
Engine, and Generate Script all work** (they shell out to `docker.exe`). Only the in-browser
*Build* won't (no bash). Use this to drive the engine setup and produce the script, then run
that script in WSL.

```powershell
cd C:\Users\<you>\git\RHEL10-s390x
docker login registry.redhat.io
.\studio.ps1 start        # → http://localhost:8080
```

---

## 4. Validation checklist

Run through these and record pass/fail:

- [ ] **1. Login** — `docker login registry.redhat.io` succeeds.
- [ ] **2. Start** — `sudo ./studio.sh start` (WSL) prints the URL; page loads.
- [ ] **3. Pre-flight** — click *Run checks*. Expect: `container engine: docker`,
      `host architecture: x86_64 — cross-compile`, `QEMU s390x emulation: ✗` (first run),
      `registry.redhat.io login: <account>`.
- [ ] **4. Prepare Build Engine** — click it. Watch the stream install `tonistiigi/binfmt`
      and create the `mainframe-builder` buildx builder. Badge → `docker · cross · qemu ✓`.
- [ ] **5. Idempotency** — click *Prepare Build Engine* again → *"already registers
      linux/s390x — no change."* (proves self-healing is a no-op when healthy).
- [ ] **6. Configure** — arch **s390x**, admin user + your SSH pubkey, DASD/qeth defaults,
      LVM, SELinux permissive. (Apply your §2 entitlement strategy.)
- [ ] **7. Build Image** — streams `docker buildx build --platform linux/s390x` → then
      `bootc-image-builder --type raw --target-arch s390x`. Ends with
      `RAW image ready — Phase A complete` and `ARTIFACT_PATH=…`.
- [ ] **8. Download** — the **Download** button appears; the file saves and its size matches
      the log.
- [ ] **9. Verify the RAW** (in WSL):
      ```bash
      file <downloaded>.raw           # DOS/MBR or partition data
      ls -lh <downloaded>.raw         # multi-GB, matches build log
      # optional: partition table
      fdisk -l <downloaded>.raw 2>/dev/null | head
      ```
- [ ] **10. Hand off** — proceed to [`Deploy_Guide.md`](./Deploy_Guide.md) (Phase B) on the Z host.

If step 7 fails only at the package-install layer, that's the **entitlement** issue from §2 —
not a Studio bug. Note which strategy you used and whether it cleared.

---

## 5. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `bash: command not found` on Build | Server running under Windows Python (Path B). Run it in **WSL** (Path A) or use Generate Script. |
| `This script must run as root` | Start the Studio with `sudo` in WSL, or run the generated script with `sudo`. |
| Pre-flight `QEMU s390x emulation: ✗` after prepare | Docker Desktop restarted and cleared binfmt — click *Prepare Build Engine* again (it re-registers). |
| `docker buildx` has no `linux/s390x` | Prepare-engine didn't finish; re-run. Confirm `docker buildx ls` lists `mainframe-builder` with `linux/s390x`. |
| CDN `dnf install` fails with 403 / no entitlement | The §2 entitlement gap. Try Strategy A (mount certs) or Strategy C (subscribed RHEL VM, podman). |
| `bootc-image-builder` can't find the image | Under docker the target image must be in a store it can read (or pushed to a registry). This is the known docker + image-builder rough edge — validate whether a registry push is needed, or use podman. |
| Build is extremely slow | Expected — s390x runs under QEMU emulation. Give it time and RAM (Docker Desktop → Resources). |
| Download is empty / 404 | Build failed (rc≠0) so no artifact was recorded, or the server (Windows) and build (WSL) disagree on paths — keep both in WSL (Path A). |

---

## 6. Success criteria

Validation **passes** when: engine prepares (and re-prepare is a no-op), a **s390x RAW** is
produced under Docker Desktop QEMU, the **Download** delivers the byte-exact file, and you've
recorded which **entitlement strategy** made the package layers install. Capture the working
entitlement approach back into the README's cross-build note so the next person skips the guesswork.

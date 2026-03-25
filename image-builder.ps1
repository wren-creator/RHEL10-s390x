<#
.SYNOPSIS
  Use bootc-image-builder in Docker Desktop to create a bootable disk image (QCOW2 or RAW) for s390x.

.PARAMETER BootcImage
  The bootc container image reference (e.g., images.pkgrepo.bcbssc.com/mu94/rhel10-bootc-s390x:base).

.PARAMETER OutputPath
  Local path to store the resulting disk image (e.g., C:\Users\<YourUser>\output).

.PARAMETER ImageType
  Disk image type: qcow2 or raw. Default: qcow2.

.EXAMPLE
  .\Build-RHEL10-BootcDisk.ps1 -BootcImage images.pkgrepo.bcbssc.com/mu94/rhel10-bootc-s390x:base `
                               -OutputPath "C:\Users\YourUser\output" `
                               -ImageType qcow2
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BootcImage,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [ValidateSet('qcow2','raw')]
    [string]$ImageType = 'qcow2'
)

# Ensure output directory exists
if (-not (Test-Path -LiteralPath $OutputPath)) {
    Write-Host "Creating output directory: $OutputPath"
    New-Item -ItemType Directory -Path $OutputPath | Out-Null
}

# Pull bootc-image-builder if not present
Write-Host "Checking for bootc-image-builder image..."
$builderImage = "registry.redhat.io/rhel10/bootc-image-builder:latest"
docker image inspect $builderImage 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Pulling $builderImage..."
    docker pull $builderImage
}

# Build docker run arguments
$runArgs = @(
    "run", "--rm", "-it",
    "--privileged",
    "--security-opt", "seccomp=unconfined",
 #   "-v", "\\wsl.localhost\docker-desktop\mnt\docker-desktop-disk\data\docker\containers:/var/lib/containers/storage", # Access Docker image store
 #   "-v", "/var/lib/docker:/var/lib/containers/storage", # Access Docker image store
    "-v", "${OutputPath}:/output",                         # Mount output directory
    $builderImage,
    "--type", $ImageType,
    $BootcImage
)

Write-Host "`nRunning bootc-image-builder to create $ImageType image for s390x..." -ForegroundColor Cyan
Write-Host ("docker " + ($runArgs -join ' ')) -ForegroundColor Yellow

# Execute
docker @runArgs

Write-Host "`n✅ Disk image created in: $OutputPath" -ForegroundColor Green

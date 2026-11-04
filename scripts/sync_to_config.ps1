# Sync integration source -> config/custom_components (used during development)
# Run this after making changes to custom_components/openwrt_router/
# Usage: .\scripts\sync_to_config.ps1

$src = Join-Path $PSScriptRoot "..\custom_components\openwrt_router"
$dst = Join-Path $PSScriptRoot "..\config\custom_components\openwrt_router"

Copy-Item -Recurse -Force $src $dst
Write-Host "Synced custom_components/openwrt_router -> config/custom_components/openwrt_router"

# PH3 Tracker - Claude Code Instructions

## Deploy workflow
- The Predator laptop is dead (as of 2026-11-14); all work is on the Lightweight. No need to ask which laptop, and no laptop tag needed in commit messages.
- The live app is https://ph3.azurewebsites.net (see continuity.md for deploy). Pushing to GitHub only updates the code/backup; the old GitHub Pages URL just redirects to Azure.

## Azure (personal account only)
- This is a personal project. Use Adam's PERSONAL Azure login only, never the NCTL work login (the default `az` profile on this laptop).
- Start every PowerShell call that runs `az` with `$env:AZURE_CONFIG_DIR='C:\wine\.azure-personal'`, and confirm `az account show` is the personal account and the PH3 subscription (not Tendril) before creating anything.
- Never touch the Tendril subscription or anything NCTL (C:\KONDO, LIMS, KONDO VM).
- Ask before anything that bills, changes plan size/budget, or can't be undone. Budget ceiling: $20/month.
- Secrets live in 1Password (`op`) and App Service app settings only, never in git, chat, or the deploy zip.
- Account IDs and the full checklist are in Claude's memory, not here (this repo is public).

#!/usr/bin/env bash
# Run on the local Mac (Apple Silicon). Installs REA (Reverse Engineer Anything, MIT) and
# lists candidate target apps. Analyze only software you own or are authorized to examine.
set -euo pipefail

echo "== Node (REA needs Node 22.23.4+ / 24.22.1+ / 26.12.1+) =="
node -v || { echo "Install Node first: brew install node"; exit 1; }

echo "== Candidate target apps =="
ls -d /Applications/*.app ~/Applications/*.app 2>/dev/null | grep -iE "pickle|instinct|muse|openclaw|hermes" || echo "(no Pickle/Instinct app found)"

echo "== REA setup (registers MCP server for your coding agent; restart the agent after) =="
npx -y rea-agents setup

echo "== Example: analyze an Electron app bundle =="
echo "npx -y rea-agents@latest analyze-javascript-application \"/Applications/<App>.app\" --json > rea-report.json"
